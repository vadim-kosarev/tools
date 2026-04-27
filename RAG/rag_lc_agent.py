"""
LangChain Tool-Calling RAG агент по документации СОИБ КЦОИ.

Принцип работы:
  1. Обогащение запроса — expand_query() генерирует перефразировки, ключевые термины
     и синонимы; build_expanded_query() формирует структурированный промпт для агента.
  2. Один проход LangGraph ReAct — LLM сам выбирает из 8 инструментов KB,
     вызывает их сколько нужно, синтезирует ответ.
  3. Расширение контекста — агент дочитывает разделы через get_section_content
     по правилам поисковой стратегии в системном промпте.
  4. Оценка ответа — evaluate_answer() выставляет relevance + completeness (1-10)
     и добавляет результат к выводу.
  5. Cross-session память — recall до вызова агента, save после оценки.

Инструменты агента (см. kb_tools.py):
  semantic_search      — семантический поиск по эмбеддингам
  exact_search         — точный поиск по подстроке
  regex_search         — regex-поиск по .md файлам (IP, порты, ВЛАНы)
  read_table           — чтение строк таблицы по разделу
  get_section_content  — полный текст раздела из .md файла
  list_sections        — дерево разделов базы знаний
  get_neighbor_chunks  — соседние чанки вокруг найденного
  list_sources         — список всех файлов в базе знаний

Использование:
    python rag_lc_agent.py                         # интерактивный чат
    python rag_lc_agent.py "что такое КЦОИ"        # одиночный вопрос
    python rag_lc_agent.py --verbose               # показывать вызовы инструментов

Переменные окружения — те же что у rag_chat.py (читает тот же .env).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Принудительно переключаем stdout/stderr на UTF-8 — иначе кириллица
# в логах отображается иероглифами в PowerShell (cp866/cp1251 по умолчанию)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_ollama import ChatOllama

import rag_chat
from rag_chat import SEP, build_vectorstore, settings
from clickhouse_store import ClickHouseVectorStore
from kb_tools import create_kb_tools, ALL_TOOLS, AGENT_SELECTABLE_TOOLS
from llm_call_logger import LangChainFileLogger, LlmCallLogger
from query_refiner import (
    AnswerEvaluation,
    build_expanded_query,
    evaluate_answer,
    expand_query,
    _expansion_details,
    _evaluation_details,
)
from session_memory import (
    SessionMemory,
    extract_effective_tools,
    extract_key_sections,
    format_memory_hint,
)
from logging_config import setup_logging

logger = setup_logging("rag_lc_agent")

# ---------------------------------------------------------------------------
# LLM call logger (singleton per process)
# ---------------------------------------------------------------------------

_llm_logger: LlmCallLogger | None = None


def _get_llm_logger() -> LlmCallLogger:
    """Returns the process-level LlmCallLogger instance (lazy init)."""
    global _llm_logger
    if _llm_logger is None:
        _llm_logger = LlmCallLogger(
            enabled=settings.llm_log_enabled,
            log_dir=Path(__file__).parent / "logs",
            stream_to_console=True,  # Live output of LLM thinking
        )
    return _llm_logger


# ---------------------------------------------------------------------------
# Cross-session memory (singleton per process)
# ---------------------------------------------------------------------------

_session_memory: SessionMemory | None = None


def _get_session_memory(vectorstore: ClickHouseVectorStore) -> SessionMemory | None:
    """Returns the process-level SessionMemory instance (lazy init).

    Returns None if agent_memory_enabled=False in settings.
    Uses the same ClickHouse database and embedding model as the vectorstore.
    """
    global _session_memory
    if not settings.agent_memory_enabled:
        return None
    if _session_memory is None:
        import clickhouse_connect
        import urllib3
        from rag_chat import _make_embeddings, _make_ch_settings
        ch_cfg = _make_ch_settings()
        _session_memory = SessionMemory(
            client=clickhouse_connect.get_client(
                host=ch_cfg.host,
                port=ch_cfg.port,
                username=ch_cfg.username,
                password=ch_cfg.password,
                pool_mgr=urllib3.PoolManager(maxsize=16),
            ),
            embedding=_make_embeddings(),
            database=ch_cfg.database,
            table=settings.agent_memory_table,
            min_score=settings.agent_memory_min_score,
            min_tool_calls=settings.agent_memory_min_tool_calls,
            recall_sim=settings.agent_memory_recall_sim,
            dedup_sim=settings.agent_memory_dedup_sim,
        )
        logger.info(
            f"SessionMemory инициализирована\n"
            f"  Таблица: {ch_cfg.database}.{settings.agent_memory_table}\n"
            f"  Записей: {_session_memory.count()}\n"
            f"  Порог recall: {settings.agent_memory_recall_sim:.0%}   "
            f"dedup: {settings.agent_memory_dedup_sim:.0%}"
        )
    return _session_memory


# ---------------------------------------------------------------------------
# Системный промпт
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
Ты — эксперт-аналитик по документации системы СОИБ КЦОИ Банка России.

━━━ ФАЙЛЫ В БАЗЕ ЗНАНИЙ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{sources_list}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ИНСТРУМЕНТЫ:
  semantic_search           — поиск по смыслу (эмбеддинги bge-m3, cosineDistance)
  exact_search              — точный поиск по одной подстроке (case-insensitive)
  multi_term_exact_search   — точный поиск по списку терминов; чанки ранжированы по числу совпадений
  regex_search              — regex-поиск по .md файлам (IP, порты, ВЛАНы, коды)
  read_table                — строки таблицы по названию раздела
  get_section_content       — полный текст раздела из .md файла
  list_sections             — список разделов файла или всей KB
  get_neighbor_chunks       — соседние чанки вокруг найденного (расширение контекста)
  list_sources              — список файлов с количеством чанков

━━━ ОБЯЗАТЕЛЬНАЯ СТРАТЕГИЯ ПОИСКА ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ПРАВИЛО 1 — ИСПОЛЬЗУЙ РЕАЛЬНЫЕ ИМЕНА.
  Имена файлов из блока выше — единственные доступные источники.
  Никогда не угадывай имена файлов — только из списка выше.

ПРАВИЛО 2 — МИНИМУМ 5 ИНСТРУМЕНТОВ ДО ОТВЕТА.
  Нельзя давать финальный ответ после менее чем 5 вызовов.
  Если первый инструмент не нашёл → пробуй другой. Нашёл мало → расширяй.

ПРАВИЛО 2а — СТРАТЕГИЯ ТОЧНОГО ПОИСКА ПО НЕСКОЛЬКИМ ТЕРМИНАМ.
  Если у тебя есть список из 2+ терминов для exact-поиска:
    Шаг 1. Вызови multi_term_exact_search([все термины]) — 1 вызов.
           Результаты уже ранжированы по числу совпадений:
           [OK] ВСЕ термины → [-] большинство → [-] меньше.
           Начинай анализ с верхних результатов.
    Шаг 2. Для терминов, по которым верхние результаты не дали ответа,
           вызови exact_search("термин") отдельно.

ПРАВИЛО 9 — ОБЯЗАТЕЛЬНЫЙ ПЕРЕБОР ВСЕХ ТЕРМИНОВ ИЗ БЛОКОВ [T] и [S] и [~].
  Если вопрос содержит блоки «━━━ ОБЯЗАТЕЛЬНЫЕ ПОИСКОВЫЕ ДЕЙСТВИЯ ━━━»
  или «━━━ УТОЧНЁННЫЙ ПОИСК ━━━» — это ПРЯМЫЕ ИНСТРУКЦИИ, не подсказки.

  АЛГОРИТМ:
    Шаг 1. Прочитай список из блока [T] — вызови exact_search для КАЖДОГО термина
           отдельным вызовом. Не объединяй термины.
    Шаг 2. Прочитай список из блока [S] — вызови semantic_search для КАЖДОЙ
           формулировки отдельным вызовом.
    Шаг 3. Прочитай список из блока [~] — вызови semantic_search для КАЖДОГО
           синонима отдельным вызовом.
    Шаг 4. Дочитай найденные разделы через get_section_content (правило 8).
    Шаг 5. Только после всех этих вызовов формируй финальный ответ.

  В запросе написано "Итого ожидается не менее N вызовов" — это минимум,
  которого нужно достичь. Делай именно столько вызовов, сколько указано.

  НЕЛЬЗЯ пропускать пункты из блоков [T]/[S]/[~] ради экономии вызовов.

ПРАВИЛО 10 — АВТОМАТИЧЕСКОЕ РАСШИРЕНИЕ КОНТЕКСТА ДЛЯ ЗАГОЛОВКОВ СПИСКОВ. ⚠️
  Проблема: exact_search часто находит ЗАГОЛОВОК списка/таблицы, но САМ СПИСОК
  находится в следующих чанках, которые не содержат поисковый термин.
  
  ПРИЗНАКИ ЗАГОЛОВКА В НАЙДЕННОМ ЧАНКЕ:
    - "установлены следующие"
    - "включает в себя:"
    - "состоит из:"
    - "перечень:"
    - "список:"
    - чанк заканчивается на ":" без списка после
    - номера списка (1), 2), 3)...) в начале следующей строки
  
  ОБЯЗАТЕЛЬНОЕ ДЕЙСТВИЕ при обнаружении заголовка:
    ШАГ A) Используй get_section_content(source_file, section) для получения
           ПОЛНОГО раздела. Это гарантирует, что ты увидишь весь список целиком.
           
    ШАГ Б) Если get_section_content не подходит (раздел слишком большой),
           используй get_neighbor_chunks(source, line_start, before=5, after=15)
           с параметром include_anchor=True (по умолчанию).
           Это вернет якорный чанк (заголовок) + 15 чанков после него.
           
  ПРИМЕР правильной цепочки для вопроса "ПО на АРМ СОИБ":
    1. exact_search("АРМ эксплуатационного персонала СОИБ") → нашёл чанк:
       "На АРМ эксплуатационного персонала СОИБ КЦОИ установлены следующие
        программные средства Лаборатории Касперского:"
       ⚠️ Это ЗАГОЛОВОК! Ключевая фраза "установлены следующие".
       
    2. ОБЯЗАТЕЛЬНО вызвать get_section_content("file.md", "section") 
       ИЛИ get_neighbor_chunks(source="file.md", line_start=1476, after=15)
       → получить полный список ПО из следующих чанков
       
    3. Формируем ответ на основе ПОЛНЫХ данных (заголовок + список)
  
  НЕПРАВИЛЬНО: увидеть "установлены следующие" и ответить "информация не найдена".
  ПРАВИЛЬНО: расширить контекст и получить сам список.

ПРАВИЛО 3 — КАСКАДНЫЙ ПОИСК для вопросов "дай список/перечень/все X":
  Шаг A) exact_search("ключевое слово") БЕЗ фильтра chunk_type — ищем везде
  Шаг Б) По найденным source+section → list_sections(source_file) для навигации
  Шаг В) read_table или get_section_content для полного содержимого
  Только после этих шагов формируй ответ.

ПРАВИЛО 4 — ВАРИАЦИИ ЗАПРОСА.
  Если exact_search("вендор") не нашёл → пробуй:
    exact_search("производитель"), exact_search("поставщик"), exact_search("состав ПО"),
    semantic_search("список программного обеспечения вендоры")

ПРАВИЛО 5 — НЕ СДАВАЙСЯ ПОСЛЕ ПЕРВОЙ НЕУДАЧИ.
  Если первый инструмент вернул "Ничего не найдено" — это сигнал попробовать
  другой инструмент или другую формулировку, а НЕ сигнал для финального ответа.

ПРАВИЛО 6 — chunk_type БЕЗ ФИЛЬТРА ПО УМОЛЧАНИЮ.
  Не добавляй chunk_type в exact_search если явно не ищешь только таблицы или только прозу.
  Поиск без фильтра охватывает все типы чанков и даёт больше результатов.

ПРАВИЛО 7 — ДОЧИТЫВАЙ НАЙДЕННЫЕ РАЗДЕЛЫ.
  Если semantic_search или exact_search нашёл раздел с релевантным названием
  (например "Состав программных средств", "Перечень ПО", "Список оборудования") —
  ОБЯЗАТЕЛЬНО вызови get_section_content(source_file, section) чтобы прочитать
  полное содержимое раздела. Не останавливайся на превью из 800 символов.

ПРАВИЛО 8 — ОБЯЗАТЕЛЬНОЕ ОБОГАЩЕНИЕ РАЗДЕЛАМИ (самое важное).
  После любого поиска (semantic_search, exact_search, read_table) нужно:

  Шаг 1. Из каждого найденного результата извлечь поля: source (файл) и section (раздел).
  Шаг 2. Для каждой уникальной пары (source, section) с релевантным названием раздела
          вызвать get_section_content(source_file=source, section=section).
  Шаг 3. Только после этого формировать финальный ответ — используя и чанки,
          и полные тексты разделов.

  ИСКЛЮЧЕНИЯ — не вызывать get_section_content для:
    - Разделов с названием "Перечень принятых сокращений" (словарь, не нужен)
    - Разделов длиннее, чем необходимо (если раздел уже полностью виден в чанке)
    - Более 5 разделов за один ответ (ограничение — брать только самые релевантные)

  ПРИМЕР правильной цепочки для вопроса "состав ПО":
    1. semantic_search("состав программного обеспечения") → нашёл 3 чанка
       Чанк 1: source="Приложение И.md", section="Состав программных средств КОИБ z/OS"
    2. get_section_content("Приложение И.md", "Состав программных средств КОИБ z/OS")
       → получил полный текст раздела (таблицы, списки)
    3. Формируем ответ на основе полных данных

━━━ ПРАВИЛА ОТВЕТА ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Используй ТОЛЬКО информацию из инструментов. Не придумывай факты.
- Приводи точные цитаты: [имя_файла] — Раздел.
- Аббревиатуры раскрывай при первом упоминании: «КЦОИ (коллективный центр обработки информации)».
- Если после всех попыток информации нет — перечисли что именно пробовал и предложи уточнить запрос.
- Отвечай на русском языке, структурированно.
"""


def _build_system_prompt(vectorstore: ClickHouseVectorStore) -> str:
    """
    Builds a dynamic system prompt that embeds the real list of available
    source files from ClickHouse.  This prevents the agent from guessing
    filenames and makes it immediately aware of what knowledge is available.
    """
    try:
        db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
        sql = (
            f"SELECT source, count() AS cnt "
            f"FROM {db}.{tbl} FINAL "
            f"GROUP BY source ORDER BY source"
        )
        result = vectorstore._client.query(sql)
        rows = result.result_rows
        if rows:
            sources_list = "\n".join(f"  • {src}  ({cnt} чанков)" for src, cnt in rows)
        else:
            sources_list = "  (база знаний пуста)"
    except Exception as exc:
        logger.warning(f"Не удалось загрузить список источников для промпта: {exc}")
        sources_list = "  (не удалось загрузить список)"

    return _SYSTEM_PROMPT_TEMPLATE.format(sources_list=sources_list)


# ---------------------------------------------------------------------------
# Основная функция: один проход агента
# ---------------------------------------------------------------------------

def run_agent(
    agent: Any,
    question: str,
    chat_history: list[BaseMessage],
    memory: SessionMemory | None = None,
    save_to_memory: bool = True,
) -> dict:
    """
    Single-pass agent invocation: memory recall → tools → answer → (optional) memory save.

    The agent receives the full chat_history so it can reference prior Q&A
    without repeating already-established facts.  The system prompt (rules +
    KB file list) is injected by the agent's LangGraph configuration and is
    intentionally NOT stored in chat_history.

    Flow:
      1. SessionMemory.recall() → navigation hint prepended to question (if any).
      2. agent.invoke(chat_history + [HumanMessage]) → KB tool calls → AIMessage.
      3. If save_to_memory=True and tool_calls >= min_tool_calls → save Q&A to memory.

    Args:
        agent:           Compiled LangGraph agent (from build_lc_agent).
        question:        Current user question (may be an expanded/refined variant).
        chat_history:    Previous conversation as [HumanMessage, AIMessage, ...].
                         Must NOT contain the system prompt.
        memory:          Optional SessionMemory for cross-session hints and saving.
        save_to_memory:  If False, skip saving to cross-session memory (used during
                         refinement passes where the caller saves the best answer).

    Returns:
        dict with keys: 'messages' (full LangGraph state),
        '_all_tool_messages' (list[ToolMessage] for source extraction).
    """
    file_callback = LangChainFileLogger(_get_llm_logger())
    invoke_config = {"callbacks": [file_callback]}

    # ── Cross-session memory: inject navigation hint ──────────────────────
    memory_hint = ""
    if memory is not None:
        recalled = memory.recall(question)
        memory_hint = format_memory_hint(recalled)
        if recalled:
            logger.info(
                f"Memory: найдено {len(recalled)} похожих записей\n"
                + "\n".join(f"  [{e.similarity:.0%}] {e.question[:80]}" for e in recalled)
            )

    query = (memory_hint + question) if memory_hint else question
    logger.info(f"Запрос к агенту: '{question[:120]}'")

    # ── Single LangGraph invocation ───────────────────────────────────────
    result = agent.invoke(
        {"messages": chat_history + [HumanMessage(content=query)]},
        config=invoke_config,
    )

    messages: list[BaseMessage] = result.get("messages", [])
    tool_msgs: list[ToolMessage] = [m for m in messages if isinstance(m, ToolMessage)]

    # Collect all tool calls made in this pass: [(name, args_str), ...]
    tool_calls_list: list[tuple[str, str]] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "?")
                try:
                    args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)[:120]
                except Exception:
                    args_str = str(tc.get("args", ""))[:80]
                tool_calls_list.append((name, args_str))

    logger.info(
        f"Агент завершил ответ\n"
        f"  Tool calls: {len(tool_calls_list)}\n"
        f"  Инструменты: {', '.join(name for name, _ in tool_calls_list) or '—'}"
    )

    # ── Cross-session memory: save if non-trivial and allowed ────────────────
    if save_to_memory and memory is not None and len(tool_calls_list) >= memory.min_tool_calls:
        final_ai_msg = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls),
            None,
        )
        answer = final_ai_msg.content if final_ai_msg else ""

        all_tool_texts = [str(tm.content) for tm in tool_msgs]
        key_sections    = extract_key_sections(all_tool_texts)
        effective_tools = extract_effective_tools(tool_calls_list)
        sources = list({s["source"] for s in key_sections if s.get("source")})

        saved = memory.save(
            question=question,
            answer=answer,
            sources=sources,
            key_sections=key_sections,
            effective_tools=effective_tools,
            rounds=1,
            score=4,      # single-pass: trust the agent completed the task
            tool_calls_count=len(tool_calls_list),
        )
        if saved:
            logger.info("Memory: запись сохранена")

    return {
        **result,
        "_all_tool_messages": tool_msgs,
    }


# ---------------------------------------------------------------------------
# Сборка агента (langchain.agents.create_agent)
# ---------------------------------------------------------------------------

def build_lc_agent(
    vectorstore: ClickHouseVectorStore,
    llm: ChatOllama,
    knowledge_dir: Path,
) -> Any:
    """
    Builds a LangGraph ReAct agent for knowledge base Q&A.

    Uses langchain.agents.create_agent with a dynamic system prompt that
    embeds the real list of source files from ClickHouse so the agent never guesses
    filenames.

    Args:
        vectorstore:   Initialized ClickHouseVectorStore.
        llm:           ChatOllama instance (must support bind_tools / tool calling).
        knowledge_dir: Path to source .md files.

    Returns:
        Compiled LangGraph agent (CompiledGraph), ready for .invoke().
    """
    llm_logger = _get_llm_logger()
    tools = create_kb_tools(
        vectorstore=vectorstore,
        knowledge_dir=knowledge_dir,
        semantic_top_k=settings.retriever_top_k,
        llm_logger=llm_logger,
    )

    system_prompt = _build_system_prompt(vectorstore)
    logger.debug(f"Системный промпт агента сформирован ({len(system_prompt)} символов)")

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )

    logger.info(
        f"LangGraph агент создан\n"
        f"  Инструментов: {len(tools)}\n"
        f"  Названия: {', '.join(t.name for t in tools)}\n"
        f"  Логирование в файл: {'включено' if llm_logger._enabled else 'отключено'}"
    )

    return agent


# ---------------------------------------------------------------------------
# Вспомогательные функции для вывода
# ---------------------------------------------------------------------------

def _extract_tool_calls(messages: list[BaseMessage]) -> list[tuple[str, str, str]]:
    """
    Extracts tool calls from the agent's message history.
    Returns list of (tool_name, tool_input_json, output_preview) tuples.
    """
    calls: list[tuple[str, str, str]] = []
    tool_outputs: dict[str, str] = {}

    # Collect tool outputs by tool_call_id
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_outputs[msg.tool_call_id] = str(msg.content)[:200]

    # Match AI tool calls with their outputs
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name  = tc.get("name", "?")
                args  = tc.get("args", {})
                tc_id = tc.get("id", "")
                try:
                    args_str = json.dumps(args, ensure_ascii=False)[:120]
                except Exception:
                    args_str = str(args)[:80]
                output_preview = tool_outputs.get(tc_id, "—")[:200].replace("\n", " ")
                calls.append((name, args_str, output_preview))

    return calls


def _collect_sources(messages: list[BaseMessage]) -> set[str]:
    """Extracts .md filenames from tool outputs in the message history."""
    sources: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolMessage):
            for m in re.finditer(r'\[([^\[\]]+\.md)\]', str(msg.content)):
                sources.add(m.group(1))
    return sources


def _format_tool_input(args: dict | str) -> str:
    """Formats tool input dict as compact JSON string for display."""
    if isinstance(args, str):
        return args[:80]
    try:
        return json.dumps(args, ensure_ascii=False)[:120]
    except Exception:
        return str(args)[:80]


def print_lc_agent_answer(
    question: str,
    result: dict,
    verbose: bool = False,
) -> None:
    """Prints the agent's final answer with tool call summary."""
    messages: list[BaseMessage] = result.get("messages", [])
    answer = ""
    if messages:
        last = messages[-1]
        answer = last.content if hasattr(last, "content") else str(last)

    tool_calls = _extract_tool_calls(messages)

    all_tool_msgs: list[ToolMessage] = result.get("_all_tool_messages", []) or []
    sources = _collect_sources(messages)
    for m in all_tool_msgs:
        for match in re.finditer(r'\[([^\[\]]+\.md)\]', str(m.content)):
            sources.add(match.group(1))

    total_tools = len(all_tool_msgs) if all_tool_msgs else len(tool_calls)

    if verbose and tool_calls:
        print(f"\n{'─' * 50}")
        print(f"Tool calls: {len(tool_calls)}")
        for name, args_str, output_preview in tool_calls:
            print(f"  >> {name}({args_str})")
            print(f"     → {output_preview}...")

    print(f"\n{SEP}")
    print(f"Вопрос: {question}")
    print(f"[Tool calls: {total_tools}  |  Инструменты: {', '.join(name for name, _, _ in tool_calls) or '—'}]")
    print(SEP)
    print(answer)
    if sources:
        print(f"\nИсточники: {', '.join(sorted(sources))}")
    print(SEP)


# ---------------------------------------------------------------------------
# Single-pass: expand → run → evaluate
# ---------------------------------------------------------------------------

def run_with_evaluation(
    agent: Any,
    llm: ChatOllama,
    question: str,
    chat_history: list[BaseMessage],
    memory: SessionMemory | None = None,
    llm_logger: LlmCallLogger | None = None,
) -> tuple[dict, AnswerEvaluation]:
    """Expand query → single agent pass → evaluate answer.

    Flow:
      1. expand_query()         → 5 rephrased queries + key terms + synonyms
      2. build_expanded_query() → structured mandatory-action prompt for agent
      3. run_agent()            → KB tool calls → raw answer
                                  (memory hint injected if memory provided)
      4. evaluate_answer()      → relevance + completeness (1-10) + missing aspects
      5. Save to memory with evaluation-based score (if memory provided)

    Args:
        agent:        Compiled LangGraph agent.
        llm:          ChatOllama for expansion and evaluation calls.
        question:     Original user question (stored verbatim in memory).
        chat_history: Session conversation history (without system prompt).
        memory:       SessionMemory for recall hint injection and saving.
        llm_logger:   Optional LlmCallLogger for file annotations.

    Returns:
        Tuple of (raw run_agent result dict, AnswerEvaluation).
    """
    # ── Query expansion ──────────────────────────────────────────────────────
    if llm_logger:
        llm_logger.log_stage("QUERY EXPANSION START", f"Вопрос: {question[:200]}")

    expansion = expand_query(llm, question, llm_logger=llm_logger)
    expanded_q = build_expanded_query(question, expansion)

    if llm_logger:
        llm_logger.log_stage("QUERY EXPANSION COMPLETE", _expansion_details(question, expansion))
        llm_logger.log_stage(
            "AGENT PASS START",
            f"Длина запроса: {len(expanded_q)} символов\n"
            f"Память: {'есть' if memory else 'нет'}\n"
            f"Начало: {expanded_q}",
        )

    # ── Single agent pass ────────────────────────────────────────────────────
    result = run_agent(
        agent=agent,
        question=expanded_q,
        chat_history=chat_history,
        memory=memory,
        save_to_memory=False,         # save after evaluation with proper score
    )

    msgs = result.get("messages", [])
    answer = ""
    if msgs:
        last = msgs[-1]
        answer = last.content if hasattr(last, "content") else str(last)
    tc_count = len(result.get("_all_tool_messages", []))

    if llm_logger:
        llm_logger.log_stage(
            "AGENT PASS COMPLETE",
            f"Инструментов вызвано: {tc_count}\nДлина ответа: {len(answer)} символов",
        )
        llm_logger.log_stage("EVALUATION START")

    # ── Evaluate ─────────────────────────────────────────────────────────────
    evaluation = evaluate_answer(llm, question, answer, pass_number=1, llm_logger=llm_logger)

    if llm_logger:
        llm_logger.log_stage("EVALUATION COMPLETE", _evaluation_details(evaluation, 1))

    # ── Save to memory ────────────────────────────────────────────────────────
    if memory:
        _save_evaluated_to_memory(question, answer, result, evaluation, memory)

    return result, evaluation


def _save_evaluated_to_memory(
    question: str,
    answer: str,
    result: dict,
    evaluation: AnswerEvaluation,
    memory: SessionMemory,
) -> None:
    """Save answer + evaluation score to SessionMemory.

    Maps the 1-10 completeness score to the 1-5 scale used by SessionMemory.

    Args:
        question:   Original user question (not the expanded variant).
        answer:     Final agent answer text.
        result:     Raw run_agent result (for tool call extraction).
        evaluation: AnswerEvaluation from evaluate_answer().
        memory:     SessionMemory instance.
    """
    msgs = result.get("messages", [])
    tool_msgs: list[ToolMessage] = result.get("_all_tool_messages", [])

    tool_calls_list: list[tuple[str, str]] = []
    for msg in msgs:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "?")
                try:
                    args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)[:120]
                except Exception:
                    args_str = str(tc.get("args", ""))[:80]
                tool_calls_list.append((name, args_str))

    all_tool_texts = [str(tm.content) for tm in tool_msgs]
    key_sections    = extract_key_sections(all_tool_texts)
    effective_tools = extract_effective_tools(tool_calls_list)
    sources = list({s["source"] for s in key_sections if s.get("source")})

    # Map 1-10 → 1-5 for SessionMemory
    score_5 = max(1, min(5, round(evaluation.completeness_score / 2)))
    saved = memory.save(
        question=question,
        answer=answer,
        sources=sources,
        key_sections=key_sections,
        effective_tools=effective_tools,
        rounds=1,
        score=score_5,
        tool_calls_count=len(tool_calls_list),
    )
    if saved:
        logger.info(
            f"Memory: сохранено\n"
            f"  relevance={evaluation.relevance_score}/10  "
            f"completeness={evaluation.completeness_score}/10"
        )


def print_evaluated_answer(
    question: str,
    result: dict,
    evaluation: AnswerEvaluation,
    verbose: bool = False,
) -> None:
    """Print agent answer with evaluation footer.

    Footer block shows relevance + completeness scores with icons,
    missing aspects (if any), and the evaluator's reasoning.

    Args:
        question:   Original user question.
        result:     Raw run_agent result dict.
        evaluation: AnswerEvaluation from evaluate_answer().
        verbose:    If True, print individual tool-call details.
    """
    messages: list[BaseMessage] = result.get("messages", [])
    answer = ""
    if messages:
        last = messages[-1]
        answer = last.content if hasattr(last, "content") else str(last)

    tool_calls = _extract_tool_calls(messages)
    all_tool_msgs: list[ToolMessage] = result.get("_all_tool_messages", []) or []

    sources = _collect_sources(messages)
    for m in all_tool_msgs:
        for match in re.finditer(r'\[([^\[\]]+\.md)]', str(m.content)):
            sources.add(match.group(1))

    total_tools = len(all_tool_msgs) if all_tool_msgs else len(tool_calls)

    if verbose and tool_calls:
        print(f"\n{'─' * 50}")
        print(f"Tool calls: {total_tools}")
        for name, args_str, output_preview in tool_calls:
            print(f"  >> {name}({args_str})")
            print(f"     → {output_preview}...")

    print(f"\n{SEP}")
    print(f"Вопрос: {question}")
    print(f"[Tool calls: {total_tools}]")
    print(SEP)
    print(answer)

    if sources:
        print(f"\nИсточники: {', '.join(sorted(sources))}")

    # ── Evaluation footer ────────────────────────────────────────────────────
    r, c = evaluation.relevance_score, evaluation.completeness_score
    r_icon = "[OK]" if r >= 8 else "[!]" if r >= 5 else "[x]"
    c_icon = "[OK]" if c >= 8 else "[!]" if c >= 5 else "[x]"
    print(f"\n{r_icon} Релевантность: {r}/10   {c_icon} Полнота: {c}/10")
    if evaluation.missing_aspects:
        print(f"[!]  Нераскрытые аспекты: {'; '.join(evaluation.missing_aspects[:5])}")
    print(f"[{evaluation.reasoning}]")
    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный чат
# ---------------------------------------------------------------------------

def run_interactive_chat(
    agent: Any,
    llm: ChatOllama,
    memory: SessionMemory | None = None,
    verbose: bool = False,
) -> None:
    """
    Interactive REPL for the LangGraph agent.

    Each question runs through a single agent pass:
      1. expand_query()         → rephrased queries, key terms, synonyms.
      2. build_expanded_query() → structured mandatory-action prompt for agent.
      3. run_agent()            → KB tool calls → answer
                                  (memory hint injected if memory≠None).
      4. evaluate_answer()      → relevance + completeness (1-10) appended to output.
      5. Save to memory with evaluation-based score.

    Maintains chat_history (sliding window of last memory_max_turns Q&A pairs)
    so the agent can reference prior answers.  The system prompt is NOT stored
    in chat_history — it lives inside the agent.
    """
    max_turns    = settings.memory_max_turns
    chat_history: list[BaseMessage] = []

    mem_status = (
        f"память {'включена (' + str(memory.count()) + ' записей)' if memory else 'выключена'}"
    )
    print(f"\n{SEP}")
    print("LangChain Tools RAG-агент по документации СОИБ КЦОИ (LangGraph + оценка ответа)")
    print(f"  {mem_status}")
    print("  Вопрос → обогащение запроса → tool calls → расширение контекста → ответ → оценка")
    print("  /reset    → очистить историю диалога")
    print("  /memory   → показать статистику памяти")
    print("  /verbose  → переключить режим подробного вывода tool calls")
    print("  exit / quit / выход → выйти")
    print(f"{SEP}\n")

    while True:
        try:
            question = input("Вопрос: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "выход"):
            print("До свидания!")
            break
        if question.lower() == "/reset":
            chat_history.clear()
            print("История диалога очищена.")
            continue
        if question.lower() == "/verbose":
            verbose = not verbose
            print(f"Verbose: {'включён' if verbose else 'выключён'}.")
            continue
        if question.lower() == "/memory":
            if memory:
                print(f"Записей в памяти: {memory.count()}")
                print(
                    f"Просмотр: SELECT id, created_at, question, score, rounds "
                    f"FROM {memory._db}.{memory._tbl} ORDER BY created_at DESC LIMIT 20;"
                )
            else:
                print("Память отключена (AGENT_MEMORY_ENABLED=false).")
            continue

        try:
            result, evaluation = run_with_evaluation(
                agent=agent,
                llm=llm,
                question=question,
                chat_history=chat_history,
                memory=memory,
                llm_logger=_get_llm_logger(),
            )
            print_evaluated_answer(question, result, evaluation, verbose=verbose)

            # Update chat history with original question and best answer
            msgs = result.get("messages", [])
            answer = ""
            if msgs:
                last = msgs[-1]
                answer = last.content if hasattr(last, "content") else str(last)
            chat_history.append(HumanMessage(content=question))
            chat_history.append(AIMessage(content=answer))
            # Sliding buffer: keep last max_turns Q&A pairs
            if len(chat_history) > max_turns * 2:
                chat_history = chat_history[-(max_turns * 2):]

        except Exception as exc:
            logger.error(f"Ошибка агента: {exc}", exc_info=True)
            print(f"\n[!]  Ошибка: {exc}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LangGraph Tool-Calling RAG агент по документации СОИБ КЦОИ"
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="Вопрос (если не указан — интерактивный режим)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать вызовы инструментов (tool calls)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info(
        f"Запуск LangGraph Tools RAG-агента\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  ClickHouse:  {settings.clickhouse_host}:{settings.clickhouse_port} "
        f"→ {settings.clickhouse_database}.{settings.clickhouse_table}\n"
        f"  Память:      {'включена (таблица: ' + settings.agent_memory_table + ')' if settings.agent_memory_enabled else 'отключена'}"
    )

    vectorstore   = build_vectorstore(force_reindex=False)
    llm           = rag_chat.build_llm()
    knowledge_dir = Path(settings.knowledge_dir)
    agent         = build_lc_agent(vectorstore, llm, knowledge_dir)
    memory        = _get_session_memory(vectorstore)

    if args.question:
        question = " ".join(args.question)
        result, evaluation = run_with_evaluation(
            agent=agent,
            llm=llm,
            question=question,
            chat_history=[],
            memory=memory,
            llm_logger=_get_llm_logger(),
        )
        print_evaluated_answer(question, result, evaluation, verbose=args.verbose)
    else:
        run_interactive_chat(agent, llm, memory=memory, verbose=args.verbose)


if __name__ == "__main__":
    main()

