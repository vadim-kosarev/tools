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
# Pre-analysis: авто-детектирование точных значений в вопросе
# (до LLM, детерминированно, через regex)
# ---------------------------------------------------------------------------

# Паттерны для авто-детектирования конкретных значений в вопросе пользователя
_PREANALYSIS_PATTERNS: list[tuple[str, str]] = [
    # (название, regex для поиска в вопросе, regex для поиска в документах)
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


class AgentAnswer(BaseModel):
    """Итоговый ответ агента."""
    question: str
    analysis: QueryAnalysis
    retrieved_chunks: int
    answer: str
    source_files: list[str]


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------

_ANALYSIS_PROMPT = """\
Ты — аналитик запросов к документации СОИБ КЦОИ Банка России.

Проанализируй запрос пользователя и составь план поиска информации.

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
"""

_SYNTHESIS_PROMPT = """\
Ты — эксперт-аналитик по документации системы СОИБ КЦОИ Банка России.

Правила:
1. Используй ТОЛЬКО информацию из предоставленного контекста.
2. Приводи точные цитаты с указанием источника [имя_файла].
3. Если информации недостаточно — явно скажи об этом.
4. Отвечай на русском языке, структурированно.
5. Если вопрос о списке (IP, серверов, систем) — дай полный список.

Контекст:
{context}

Вопрос: {question}

Ответ:"""


# ---------------------------------------------------------------------------
# Шаги агентного пайплайна
# ---------------------------------------------------------------------------

def analyze_query(llm: ChatOllama, question: str) -> QueryAnalysis:
    """
    Шаг 1: LLM анализирует запрос и строит план поиска.
    Возвращает QueryAnalysis с перефразировками и опциональными regex-паттернами.
    """
    prompt = ChatPromptTemplate.from_template(_ANALYSIS_PROMPT)
    chain = prompt | llm | StrOutputParser()

    logger.debug(f"Анализируем запрос: {question}")
    raw = chain.invoke({"question": question})

    # Стрипаем <think>...</think> блоки qwen3/deepseek-r1 перед парсингом JSON
    clean = _strip_think_tags(raw)

    # Извлекаем JSON из ответа (LLM иногда добавляет лишнее)
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

    Args:
        forced_regex: список (тип, паттерн) из pre-analysis — выполняется всегда,
                      независимо от решения LLM.
    """
    search_kwargs: dict = {"k": settings.retriever_top_k}
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
        f"  Forced regex (pre-analysis): {len(forced)}\n"
        f"  LLM regex: {len(analysis.regex_patterns)}\n"
        f"  Score threshold: {settings.retriever_score_threshold or 'off'}"
    )
    return all_docs


def synthesize_answer(
    llm: ChatOllama,
    question: str,
    docs: list[Document],
) -> tuple[str, list[str]]:
    """
    Шаг 3: LLM синтезирует финальный ответ из найденных чанков.

    Контекст обрезается до settings.max_context_chars символов — сначала по
    числу чанков, чтобы не превысить окно контекста LLM.

    Возвращает (answer_text, source_files).
    """
    context_parts = []
    total_chars = 0
    truncated_at: int | None = None

    for i, doc in enumerate(docs):
        src = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "")
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
            f"Контекст обрезан: использовано {truncated_at} из {len(docs)} чанков "
            f"({total_chars:,} / {settings.max_context_chars:,} символов)"
        )

    context = "\n\n---\n\n".join(context_parts)
    sources = list({doc.metadata.get("source", "?") for doc in docs[:truncated_at or len(docs)]})

    prompt = ChatPromptTemplate.from_template(_SYNTHESIS_PROMPT)
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": question})

    # Убираем <think> блоки из финального ответа
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
) -> AgentAnswer:
    """
    Полный агентный цикл:
    0. extract_exact_values — pre-analysis: детерминированно извлекает точные значения
       (IP, FQDN, коды документов) из вопроса — до LLM, гарантированно
    1. analyze_query  — LLM строит план поиска (перефразировки + regex)
    2. multi_retrieve — N семантических + forced regex + LLM regex, дедупликация
    3. synthesize_answer — LLM формирует финальный ответ
    """
    # Шаг 0: детерминированный pre-analysis (не зависит от LLM)
    forced_regex = extract_exact_values(question)
    if forced_regex:
        logger.info(
            f"Pre-analysis: найдено {len(forced_regex)} точных значений → принудительный regex\n"
            + "\n".join(f"  [{kind}] {pat}" for kind, pat in forced_regex)
        )

    # Шаг 1: LLM-анализ
    analysis = analyze_query(llm, question)

    # Шаг 2: поиск
    docs = multi_retrieve(vectorstore, analysis, knowledge_dir, forced_regex=forced_regex)

    # Шаг 3: синтез
    answer, sources = synthesize_answer(llm, question, docs)

    logger.info(
        f"Агентный ответ готов\n"
        f"  Источников: {len(sources)}\n"
        f"  Файлы: {', '.join(sources)}"
    )
    return AgentAnswer(
        question=question,
        analysis=analysis,
        retrieved_chunks=len(docs),
        answer=answer,
        source_files=sources,
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
    """Выводит агентный ответ с планом поиска."""
    print(f"\n{SEP}")
    print(f"Вопрос: {result.question}")
    print(f"{'-' * 70}")
    print(f"[Анализ] тип={result.analysis.query_type} | чанков={result.retrieved_chunks}")
    print(f"[Запросы] {' / '.join(result.analysis.search_queries[:2])}...")
    if result.analysis.reasoning:
        print(f"[План] {result.analysis.reasoning}")
    print(SEP)
    print(result.answer)
    print(f"\nИсточники: {', '.join(result.source_files)}")
    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный чат
# ---------------------------------------------------------------------------

def run_interactive_chat(vectorstore, llm: ChatOllama, knowledge_dir: Path) -> None:
    """Интерактивный агентный чат с отображением плана поиска."""
    print(f"\n{SEP}")
    print("Agentic RAG-чат по документации СОИБ КЦОИ")
    print("  Обычный вопрос     → анализ → план → N поисков → синтез")
    print("  ips [файл]         → список всех IP из документов")
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

        # Команда: ips [output_file]
        if question.lower().startswith("ips"):
            parts = question.split(maxsplit=1)
            out = Path(parts[1]) if len(parts) > 1 else None
            extract_all_ips(knowledge_dir, output_file=out)
            continue

        result = agent_ask(vectorstore, llm, question, knowledge_dir)
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

    # Режим извлечения IP — не требует LLM/vectorstore
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

