"""
Agentic RAG-чат по документации СОИБ КЦОИ.

Отличие от rag_chat.py: вместо одного прямого поиска используется
многошаговый агентный пайплайн (как у Claude Sonnet):

  1. АНАЛИЗ ЗАПРОСА — LLM разбирает что спрашивается, определяет
     тип вопроса, выделяет ключевые термины и аббревиатуры.
  2. ПЛАН ПОИСКА — LLM строит N перефразировок запроса + опциональные
     regex-паттерны для поиска точных данных (IP, номера, коды).
  3. ПАРАЛЛЕЛЬНЫЙ ПОИСК — N семантических поисков + regex (если нужен),
     результаты дедуплицируются по содержимому.
  3.5 ОБОГАЩЕНИЕ СЕКЦИЯМИ — для каждого найденного чанка агент определяет
     исходный файл и раздел, читает полный контент секции напрямую из файла
     и добавляет его в контекст — это позволяет не терять данные таблиц,
     которые были порезаны при чанкинге.
  4. СИНТЕЗ — LLM формирует структурированный ответ с цитатами и
     ссылками на источники.

Использование:
    python rag_agent.py                      # интерактивный чат
    python rag_agent.py "что такое КЦОИ"     # одиночный вопрос

Переменные окружения — те же что у rag_chat.py (читает тот же .env).
"""

import re
import json
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama

import rag_chat
from rag_chat import (
    settings,
    build_vectorstore,
    regex_search,
    SEP,
    _HEADER_RE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Утилита: стриппинг <think>...</think> блоков (qwen3 и аналоги)
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    """Удаляет <think>...</think> блоки из ответа LLM (qwen3, deepseek-r1 и др.)."""
    return _THINK_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Conversation Memory — скользящий буфер истории диалога
# ---------------------------------------------------------------------------

class ConversationTurn(BaseModel):
    """Один обмен вопрос/ответ в истории диалога."""
    question: str
    answer: str


class ConversationBuffer:
    """
    Скользящий буфер последних N обменов вопрос/ответ.

    Передаётся в analyze_query и synthesize_answer, чтобы LLM понимал
    контекст уточняющих вопросов ("а что с этим сервером?", "расскажи подробнее").
    Размер буфера задаётся settings.memory_max_turns.
    """

    def __init__(self, max_turns: int | None = None) -> None:
        self._max_turns = max_turns or settings.memory_max_turns
        self._turns: deque[ConversationTurn] = deque(maxlen=self._max_turns)

    def add(self, question: str, answer: str) -> None:
        """Добавляет новый обмен в буфер."""
        self._turns.append(ConversationTurn(question=question, answer=answer))

    def is_empty(self) -> bool:
        return len(self._turns) == 0

    def format_for_prompt(self, answer_max_chars: int = 400) -> str:
        """
        Форматирует историю для вставки в промпт.
        Ответы обрезаются до answer_max_chars символов чтобы не раздувать промпт.
        Возвращает пустую строку если история пуста.
        """
        if self.is_empty():
            return ""
        lines = ["История диалога:"]
        for i, turn in enumerate(self._turns, 1):
            short_answer = turn.answer[:answer_max_chars].replace("\n", " ")
            if len(turn.answer) > answer_max_chars:
                short_answer += "..."
            lines.append(f"  Q{i}: {turn.question}")
            lines.append(f"  A{i}: {short_answer}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Exhaustive query detection — запросы "дай список всех X"
# ---------------------------------------------------------------------------

_EXHAUSTIVE_RE = re.compile(
    r"""
    \b(
        все\s+\w+           # все IP, все серверы, все СУБД
      | всех\s+\w+          # всех серверов
      | список\s+всех       # список всех
      | полный\s+список     # полный список
      | перечень\s+всех     # перечень всех
      | перечисл\w+         # перечислить, перечисление
      | дай\s+все           # дай все
      | покажи\s+все        # покажи все
      | вывести\s+все       # вывести все
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _is_exhaustive_query(question: str) -> bool:
    """
    Определяет, является ли запрос исчерпывающим (требующим полного списка).

    Для таких запросов semantic search с top_k=10 даёт неполный результат —
    нужен полный regex-скан по исходным файлам.
    """
    return bool(_EXHAUSTIVE_RE.search(question))


# ---------------------------------------------------------------------------
# Pre-analysis: авто-детектирование точных значений в вопросе
# (до LLM, детерминированно, через regex)
# ---------------------------------------------------------------------------

# Паттерны для авто-детектирования конкретных значений в вопросе пользователя
_PREANALYSIS_PATTERNS: list[tuple[str, str]] = [
    ("IPv4",        r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?\b"),
    ("FQDN/host",   r"\b(?:[a-zA-Z0-9-]+\.){2,}[a-zA-Z]{2,}\b"),
    ("doc_number",  r"\b[А-ЯA-Z]{2,}-\d+(?:\.\d+)*\b"),
    ("hex_code",    r"\b0x[0-9A-Fa-f]{4,}\b"),
    ("port",        r"(?:порт|port)\s*:?\s*(\d{2,5})\b"),
    ("vlan",        r"(?:vlan|влан)\s*:?\s*(\d+)\b"),
]


def extract_exact_values(question: str) -> list[tuple[str, str]]:
    """
    Детерминированно извлекает из вопроса точные значения (IP, FQDN, коды документов и т.д.).
    Возвращает список (тип, regex_паттерн_для_документов).
    """
    found: list[tuple[str, str]] = []
    for name, pattern in _PREANALYSIS_PATTERNS:
        for match in re.finditer(pattern, question, re.IGNORECASE):
            value = match.group(0).strip()
            # Для поиска в документах — экранируем точки в IP/FQDN
            doc_pattern = re.escape(value)
            found.append((name, doc_pattern))
            logger.debug(f"Pre-analysis: найдено {name} = '{value}' → regex: '{doc_pattern}'")
    return found


# ---------------------------------------------------------------------------
# DTO для агентного пайплайна
# ---------------------------------------------------------------------------

class QueryAnalysis(BaseModel):
    """Результат анализа запроса пользователя."""
    query_type: str           # "factual" | "list" | "comparison" | "pattern_search"
    key_terms: list[str]      # ключевые термины и аббревиатуры
    search_queries: list[str] # 2-4 перефразировки для semantic search
    regex_patterns: list[str] # regex-паттерны (пусто если не нужны)
    reasoning: str            # краткое объяснение плана


class SectionRef(BaseModel):
    """Ссылка на конкретный раздел исходного файла."""
    source_file: str    # имя .md файла
    section: str        # breadcrumb раздела (H1 > H2 > ...)
    chunk_type: str     # "table_row" | "table_raw" | "" (prose)


class AgentAnswer(BaseModel):
    """Итоговый ответ агента."""
    question: str
    analysis: QueryAnalysis
    retrieved_chunks: int
    enriched_sections: int      # количество секций, дочитанных из файлов
    answer: str
    source_files: list[str]
    found_sections: list[SectionRef]  # все найденные секции для прозрачности


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------

_ANALYSIS_PROMPT = """\
Ты — аналитик запросов к документации СОИБ КЦОИ Банка России.

Проанализируй запрос пользователя и составь план поиска информации.
{history}
Запрос: {question}

Верни ТОЛЬКО валидный JSON без комментариев и markdown-блоков:
{{
  "query_type": "<factual|list|comparison|pattern_search>",
  "key_terms": ["термин1", "термин2"],
  "search_queries": [
    "оригинальный запрос",
    "перефразировка 1 с раскрытыми аббревиатурами",
    "перефразировка 2 другими словами",
    "перефразировка 3 по смыслу"
  ],
  "regex_patterns": [],
  "reasoning": "кратко: что ищем и почему такой план"
}}

Правила:
- query_type = "pattern_search" если ищут IP, номера, коды, шаблоны
- regex_patterns заполнять только при pattern_search
- search_queries: 2-4 штуки, разными словами
- key_terms: аббревиатуры раскрывать (КЦОИ → "коллективный центр обработки информации")
- если есть история диалога — учитывай её при построении плана поиска
"""

_SYNTHESIS_PROMPT = """\
Ты — эксперт-аналитик по документации системы СОИБ КЦОИ Банка России.

Правила:
1. Используй ТОЛЬКО информацию из предоставленного контекста.
2. Приводи точные цитаты с указанием источника [имя_файла].
3. Если информации недостаточно — явно скажи об этом.
4. Отвечай на русском языке, структурированно.
5. Если вопрос о списке (IP, серверов, систем) — дай полный список.
6. Если есть история диалога — учитывай её при формировании ответа.
7. Контекст содержит как отдельные чанки (строки таблиц), так и полные секции файлов
   (помечены тегом [FULL_SECTION]). Полные секции имеют приоритет — используй их для
   исчерпывающих ответов о списках и таблицах.
{history}
Контекст:
{context}

Вопрос: {question}

Ответ:"""


# ---------------------------------------------------------------------------
# Шаги агентного пайплайна
# ---------------------------------------------------------------------------

def analyze_query(
    llm: ChatOllama,
    question: str,
    memory: ConversationBuffer | None = None,
) -> QueryAnalysis:
    """
    Шаг 1: LLM анализирует запрос и строит план поиска.

    Если передан memory — история диалога добавляется в промпт, что позволяет
    корректно обрабатывать уточняющие вопросы ("а что с этим?", "расскажи подробнее").
    Возвращает QueryAnalysis с перефразировками и опциональными regex-паттернами.
    """
    prompt = ChatPromptTemplate.from_template(_ANALYSIS_PROMPT)
    chain = prompt | llm | StrOutputParser()

    history_text = memory.format_for_prompt() if memory and not memory.is_empty() else ""
    history_block = f"\n{history_text}\n" if history_text else ""

    logger.debug(f"Анализируем запрос: {question}")
    raw = chain.invoke({"question": question, "history": history_block})

    clean = _strip_think_tags(raw)

    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not json_match:
        logger.warning(f"LLM вернул не-JSON при анализе запроса, используем fallback\nRaw: {raw[:300]}")
        return QueryAnalysis(
            query_type="factual",
            key_terms=[question],
            search_queries=[question],
            regex_patterns=[],
            reasoning="fallback: не удалось распарсить план",
        )

    try:
        data = json.loads(json_match.group(0))
        analysis = QueryAnalysis(**data)
    except Exception as exc:
        logger.warning(f"Ошибка парсинга QueryAnalysis: {exc}\nRaw: {raw[:300]}")
        analysis = QueryAnalysis(
            query_type="factual",
            key_terms=[question],
            search_queries=[question],
            regex_patterns=[],
            reasoning=f"fallback: {exc}",
        )

    logger.info(
        f"План поиска построен\n"
        f"  Тип запроса:   {analysis.query_type}\n"
        f"  Ключевые слова: {json.dumps(analysis.key_terms, ensure_ascii=False)}\n"
        f"  Поисковых запросов: {len(analysis.search_queries)}\n"
        f"  Regex-паттернов: {len(analysis.regex_patterns)}\n"
        f"  Обоснование: {analysis.reasoning}"
    )
    return analysis


def multi_retrieve(
    vectorstore,
    analysis: QueryAnalysis,
    knowledge_dir: Path,
    forced_regex: list[tuple[str, str]] | None = None,
) -> list[Document]:
    """
    Шаг 2: Выполняет N семантических поисков по всем перефразировкам параллельно
    (ThreadPoolExecutor) + regex-поиск (из плана LLM + принудительные из pre-analysis).
    Дедуплицирует результаты по содержимому.

    Семантический поиск использует score_threshold если задан в settings
    (settings.retriever_score_threshold > 0).

    Для запросов типа "list" top_k увеличивается в 3 раза чтобы захватить
    все строки таблицы (все СУБД, все серверы, все IP и т.д.).

    Args:
        forced_regex: список (тип, паттерн) из pre-analysis — выполняется всегда,
                      независимо от решения LLM.
    """
    # Для исчерпывающих list-запросов нужно больше чанков на семантический поиск
    top_k = settings.retriever_top_k
    if analysis.query_type == "list":
        top_k = top_k * 3

    search_kwargs: dict = {"k": top_k}
    if settings.retriever_score_threshold > 0:
        retriever = vectorstore.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={**search_kwargs, "score_threshold": settings.retriever_score_threshold},
        )
    else:
        retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs=search_kwargs,
        )

    seen: set[str] = set()
    all_docs: list[Document] = []

    # Параллельные семантические поиски по всем перефразировкам
    def _search(query: str) -> tuple[str, list[Document]]:
        logger.debug(f"Semantic search: {query}")
        return query, retriever.invoke(query)

    with ThreadPoolExecutor(max_workers=len(analysis.search_queries)) as executor:
        futures = {executor.submit(_search, q): q for q in analysis.search_queries}
        for future in as_completed(futures):
            try:
                _, docs = future.result()
                for doc in docs:
                    key = doc.page_content[:200]
                    if key not in seen:
                        seen.add(key)
                        all_docs.append(doc)
            except Exception as exc:
                logger.warning(f"Ошибка семантического поиска: {exc}")

    # Принудительные regex-паттерны из pre-analysis (IP, FQDN, коды)
    forced = forced_regex or []
    for kind, pattern in forced:
        logger.debug(f"Forced regex [{kind}]: {pattern}")
        result = regex_search(pattern, knowledge_dir)
        for match in result.matches:
            content = f"[regex:{kind} match={match.match}]\n{match.context}"
            key = content[:200]
            if key not in seen:
                seen.add(key)
                all_docs.append(Document(
                    page_content=content,
                    metadata={"source": match.file, "section": f"regex:{kind}"},
                ))

    # Дополнительные regex из плана LLM (если не дублируют принудительные)
    forced_patterns = {p for _, p in forced}
    for pattern in analysis.regex_patterns:
        if pattern in forced_patterns:
            continue
        result = regex_search(pattern, knowledge_dir)
        for match in result.matches[:30]:
            content = f"[regex match: {match.match}]\n{match.context}"
            key = content[:200]
            if key not in seen:
                seen.add(key)
                all_docs.append(Document(
                    page_content=content,
                    metadata={"source": match.file, "section": "regex-match"},
                ))

    logger.info(
        f"Поиск завершён\n"
        f"  Уникальных чанков: {len(all_docs)}\n"
        f"  Семантических запросов (параллельно): {len(analysis.search_queries)}\n"
        f"  top_k per query: {top_k} (list-mode: {analysis.query_type == 'list'})\n"
        f"  Forced regex (pre-analysis): {len(forced)}\n"
        f"  LLM regex: {len(analysis.regex_patterns)}\n"
        f"  Score threshold: {settings.retriever_score_threshold or 'off'}"
    )
    return all_docs


def rerank_docs(docs: list[Document], question: str) -> list[Document]:
    """
    Переранжирует найденные чанки с помощью FlashrankRerank (cross-encoder).

    Использует локальную модель без GPU и внешних API.
    Если flashrank недоступен или reranker_top_n == 0 — возвращает исходный список.
    """
    top_n = settings.reranker_top_n
    if top_n <= 0 or not docs:
        return docs

    try:
        from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank
        compressor = FlashrankRerank(top_n=min(top_n, len(docs)))
        reranked = compressor.compress_documents(docs, question)
        logger.info(
            f"Reranking завершён\n"
            f"  До: {len(docs)} чанков → После: {len(reranked)} чанков\n"
            f"  top_n={top_n}"
        )
        return list(reranked)
    except Exception as exc:
        logger.warning(f"Reranking недоступен, используем исходный порядок: {exc}")
        return docs


# ---------------------------------------------------------------------------
# Шаг 3.5: Обогащение секциями — читаем полный контент найденных разделов
# ---------------------------------------------------------------------------

def _read_full_section(md_file: Path, section_breadcrumb: str) -> str | None:
    """
    Читает полный текст раздела из .md файла по breadcrumb-пути.

    Ищет заголовок, соответствующий последнему компоненту breadcrumb,
    и собирает весь текст до следующего заголовка того же или высшего уровня.
    Возвращает None если раздел не найден.
    """
    try:
        text = md_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning(f"Не удалось прочитать {md_file.name}: {exc}")
        return None

    # Последний компонент breadcrumb — имя искомого заголовка
    target_header = section_breadcrumb.split(" > ")[-1].strip() if section_breadcrumb else ""
    if not target_header:
        return None

    lines = text.splitlines(keepends=True)
    in_section = False
    section_level = 0
    collected: list[str] = []

    for line in lines:
        m = _HEADER_RE.match(line.rstrip())
        if m:
            level = len(m.group(1))
            header_text = m.group(2).strip()

            if not in_section:
                # Ищем нужный заголовок (нечувствительно к регистру)
                if header_text.lower() == target_header.lower():
                    in_section = True
                    section_level = level
                    collected.append(line)
            else:
                # Заканчиваем секцию при заголовке того же или высшего уровня
                if level <= section_level:
                    break
                collected.append(line)
        elif in_section:
            collected.append(line)

    return "".join(collected).strip() if collected else None


def enrich_with_full_sections(
    docs: list[Document],
    knowledge_dir: Path,
    max_sections: int = 10,
) -> tuple[list[Document], list[SectionRef]]:
    """
    Шаг 3.5: Для каждого найденного чанка определяет (файл, раздел),
    читает полный контент раздела напрямую из .md файла и добавляет
    его как дополнительный Document с тегом [FULL_SECTION].

    Ограничение max_sections — не более N уникальных секций, чтобы
    не перегружать контекст LLM.

    Возвращает (enriched_docs, section_refs) где enriched_docs =
    исходные чанки + полные секции файлов.
    """
    # Собираем уникальные (source, section) пары из найденных чанков
    seen_sections: set[tuple[str, str]] = set()
    section_refs: list[SectionRef] = []

    for doc in docs:
        source = doc.metadata.get("source", "")
        section = doc.metadata.get("section", "")
        chunk_type = doc.metadata.get("chunk_type", "")

        # Пропускаем regex-чанки (нет привязки к файловой секции)
        if not source or source.startswith("regex") or "regex" in section:
            continue

        key = (source, section)
        if key not in seen_sections:
            seen_sections.add(key)
            section_refs.append(SectionRef(
                source_file=source,
                section=section,
                chunk_type=chunk_type,
            ))

    # Ограничиваем количество секций для дочтения
    sections_to_fetch = section_refs[:max_sections]
    extra_docs: list[Document] = []
    fetched = 0

    for ref in sections_to_fetch:
        # Ищем файл в knowledge_dir (рекурсивно)
        matches = list(knowledge_dir.glob(f"**/{ref.source_file}"))
        if not matches:
            logger.debug(f"Файл не найден: {ref.source_file}")
            continue

        md_file = matches[0]
        section_text = _read_full_section(md_file, ref.section)

        if not section_text:
            logger.debug(f"Раздел не найден: [{ref.source_file}] {ref.section}")
            continue

        extra_docs.append(Document(
            page_content=section_text,
            metadata={
                "source": ref.source_file,
                "section": ref.section,
                "chunk_type": "full_section",
            },
        ))
        fetched += 1
        logger.debug(f"  Дочитана секция [{ref.source_file}] '{ref.section[:60]}' ({len(section_text)} символов)")

    logger.info(
        f"Обогащение секциями завершено\n"
        f"  Найдено уникальных секций: {len(section_refs)}\n"
        f"  Дочитано из файлов: {fetched}/{len(sections_to_fetch)}\n"
        f"  Секции: " + ", ".join(f"[{r.source_file}] {r.section[:40]}" for r in sections_to_fetch[:5])
    )

    return docs + extra_docs, section_refs


def synthesize_answer(
    llm: ChatOllama,
    question: str,
    docs: list[Document],
    memory: ConversationBuffer | None = None,
) -> tuple[str, list[str]]:
    """
    Шаг 4: LLM синтезирует финальный ответ из найденных чанков.

    Контекст формируется из всех docs — включая полные секции файлов
    (chunk_type="full_section", помеченные тегом [FULL_SECTION]).
    Контекст обрезается до settings.max_context_chars символов.
    Возвращает (answer_text, source_files).
    """
    context_parts = []
    total_chars = 0
    truncated_at: int | None = None

    # Полные секции ставим в начало контекста — они приоритетнее отдельных чанков
    ordered_docs = sorted(
        docs,
        key=lambda d: 0 if d.metadata.get("chunk_type") == "full_section" else 1,
    )

    for i, doc in enumerate(ordered_docs):
        src = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "")
        chunk_type = doc.metadata.get("chunk_type", "")

        if chunk_type == "full_section":
            header = f"[FULL_SECTION][{src}]" + (f" — {section}" if section else "")
        else:
            header = f"[{src}]" + (f" — {section}" if section else "")

        chunk = f"{header}\n{doc.page_content}"
        chunk_len = len(chunk)

        if settings.max_context_chars > 0 and total_chars + chunk_len > settings.max_context_chars:
            truncated_at = i
            break

        context_parts.append(chunk)
        total_chars += chunk_len

    if truncated_at is not None:
        logger.warning(
            f"Контекст обрезан: использовано {truncated_at} из {len(ordered_docs)} чанков "
            f"({total_chars:,} / {settings.max_context_chars:,} символов)"
        )

    context = "\n\n---\n\n".join(context_parts)
    sources = list({doc.metadata.get("source", "?") for doc in ordered_docs[:truncated_at or len(ordered_docs)]})

    history_text = memory.format_for_prompt() if memory and not memory.is_empty() else ""
    history_block = f"\n{history_text}\n" if history_text else ""

    prompt = ChatPromptTemplate.from_template(_SYNTHESIS_PROMPT)
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": question, "history": history_block})

    answer = _strip_think_tags(answer)
    return answer, sources


# ---------------------------------------------------------------------------
# Главный метод агента
# ---------------------------------------------------------------------------

def agent_ask(
    vectorstore,
    llm: ChatOllama,
    question: str,
    knowledge_dir: Path,
    memory: ConversationBuffer | None = None,
) -> AgentAnswer:
    """
    Полный агентный цикл:
    0. extract_exact_values — pre-analysis: детерминированно извлекает точные значения
       (IP, FQDN, коды документов) из вопроса — до LLM, гарантированно
    1. analyze_query    — LLM строит план поиска (перефразировки + regex); учитывает memory
    2. multi_retrieve   — N семантических (параллельно) + forced regex + LLM regex, дедупликация
    2.5 rerank_docs     — cross-encoder переранжирует чанки по реальной релевантности вопросу
    3.5 enrich_with_full_sections — для каждой найденной секции читает полный контент
        из исходного .md файла и добавляет в контекст (позволяет получить все строки таблиц)
    4. synthesize_answer — LLM формирует финальный ответ; учитывает memory

    Args:
        memory: буфер истории диалога; если передан — включается в промпты анализа и синтеза,
                и обновляется новым обменом после получения ответа.
    """
    # Шаг 0: детерминированный pre-analysis
    forced_regex = extract_exact_values(question)
    if forced_regex:
        logger.info(
            f"Pre-analysis: найдено {len(forced_regex)} точных значений → принудительный regex\n"
            + "\n".join(f"  [{kind}] {pat}" for kind, pat in forced_regex)
        )

    # Шаг 1: LLM-анализ
    analysis = analyze_query(llm, question, memory=memory)

    # Шаг 2: поиск
    docs = multi_retrieve(vectorstore, analysis, knowledge_dir, forced_regex=forced_regex)

    # Шаг 2.5: переранжирование
    docs = rerank_docs(docs, question)

    # Шаг 3.5: обогащение полными секциями из исходных файлов
    docs, section_refs = enrich_with_full_sections(docs, knowledge_dir)

    # Шаг 4: синтез
    answer, sources = synthesize_answer(llm, question, docs, memory=memory)

    if memory is not None:
        memory.add(question, answer)
        logger.debug(f"Memory: добавлен обмен, всего в буфере: {len(memory._turns)}")

    full_section_count = sum(1 for d in docs if d.metadata.get("chunk_type") == "full_section")

    logger.info(
        f"Агентный ответ готов\n"
        f"  Источников: {len(sources)}\n"
        f"  Полных секций в контексте: {full_section_count}\n"
        f"  Файлы: {', '.join(sources)}"
    )
    return AgentAnswer(
        question=question,
        analysis=analysis,
        retrieved_chunks=len(docs),
        enriched_sections=full_section_count,
        answer=answer,
        source_files=sources,
        found_sections=section_refs,
    )


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------

def extract_all_ips(knowledge_dir: Path, output_file: Path | None = None) -> int:
    """
    Извлекает все уникальные IP-адреса и подсети из документов.
    Сортирует по октетам. Если output_file указан — сохраняет в файл.
    Возвращает количество уникальных IP.
    """
    result = regex_search(r'\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b', knowledge_dir)

    ip_map: dict[str, set[str]] = {}
    for m in result.matches:
        ip = m.match
        if ip not in ip_map:
            ip_map[ip] = set()
        ip_map[ip].add(m.file)

    def _sort_key(ip: str) -> tuple:
        clean = ip.split('/')[0]
        parts = clean.split('.')
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (999, 999, 999, 999)

    sorted_ips = sorted(ip_map.keys(), key=_sort_key)

    lines = [f"Уникальных IP/подсетей: {len(sorted_ips)}\n"]
    for ip in sorted_ips:
        files = ', '.join(sorted(ip_map[ip]))
        lines.append(f"{ip:22}  [{files}]")

    content = "\n".join(lines)

    if output_file:
        output_file.write_text(content, encoding="utf-8")
        logger.info(f"Список IP сохранён в {output_file} ({len(sorted_ips)} записей)")
    else:
        print(content)

    return len(sorted_ips)


# ---------------------------------------------------------------------------
# Вывод агентного ответа
# ---------------------------------------------------------------------------

def print_agent_answer(result: AgentAnswer) -> None:
    """Выводит агентный ответ с планом поиска и найденными секциями."""
    print(f"\n{SEP}")
    print(f"Вопрос: {result.question}")
    print(f"{'-' * 70}")
    print(f"[Анализ] тип={result.analysis.query_type} | чанков={result.retrieved_chunks} | секций={result.enriched_sections}")
    print(f"[Запросы] {' / '.join(result.analysis.search_queries[:2])}...")
    if result.analysis.reasoning:
        print(f"[План] {result.analysis.reasoning}")
    if result.found_sections:
        print(f"[Секции]")
        for ref in result.found_sections[:8]:
            print(f"  [{ref.source_file}] {ref.section[:70]}")
        if len(result.found_sections) > 8:
            print(f"  ... и ещё {len(result.found_sections) - 8} секций")
    print(SEP)
    print(result.answer)
    print(f"\nИсточники: {', '.join(result.source_files)}")
    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный чат
# ---------------------------------------------------------------------------

def run_interactive_chat(vectorstore, llm: ChatOllama, knowledge_dir: Path) -> None:
    """
    Интерактивный агентный чат с отображением плана поиска и найденных секций.
    Поддерживает историю диалога (ConversationBuffer) между вопросами.
    """
    memory = ConversationBuffer()

    print(f"\n{SEP}")
    print("Agentic RAG-чат по документации СОИБ КЦОИ")
    print("  Обычный вопрос     → анализ → план → N поисков → обогащение секциями → синтез")
    print("  ips [файл]         → список всех IP из документов")
    print("  /reset             → очистить историю диалога")
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
            memory = ConversationBuffer()
            print("История диалога очищена.")
            continue

        if question.lower().startswith("ips"):
            parts = question.split(maxsplit=1)
            out = Path(parts[1]) if len(parts) > 1 else None
            extract_all_ips(knowledge_dir, output_file=out)
            continue

        result = agent_ask(vectorstore, llm, question, knowledge_dir, memory=memory)
        print_agent_answer(result)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Agentic RAG-чат по документации СОИБ КЦОИ")
    parser.add_argument("question", nargs="*", help="Вопрос (если не указан — интерактивный режим)")
    parser.add_argument("--ips", metavar="FILE", nargs="?", const="", help="Извлечь все IP из документов (опционально: путь к файлу)")
    args = parser.parse_args()

    logger.info(
        f"Запуск Agentic RAG\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  Chroma HTTP: http://{settings.chroma_host}:{settings.chroma_port}"
    )

    knowledge_dir = Path(settings.knowledge_dir)

    if args.ips is not None:
        out_file = Path(args.ips) if args.ips else None
        extract_all_ips(knowledge_dir, output_file=out_file)
        return

    vectorstore = build_vectorstore(force_reindex=False)
    llm = rag_chat.build_llm()

    if args.question:
        question = " ".join(args.question)
        result = agent_ask(vectorstore, llm, question, knowledge_dir)
        print_agent_answer(result)
    else:
        run_interactive_chat(vectorstore, llm, knowledge_dir)


if __name__ == "__main__":
    main()

