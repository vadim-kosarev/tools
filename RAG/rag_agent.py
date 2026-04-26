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

import re as _re
import rag_chat
from rag_chat import (
    settings,
    build_vectorstore,
    regex_search,
    SEP,
)
from clickhouse_store import ClickHouseVectorStore
from md_splitter import _clean_text as _clean_header_text
from llm_call_logger import LlmCallLogger

# Heading regex (was in rag_chat before md_splitter refactoring)
_HEADER_RE = _re.compile(r"^(#{1,4})\s+(.+)$")

logger = logging.getLogger(__name__)

# LLM call logger — activated when settings.llm_log_enabled = true
# Lazy-initialised on first use so settings are fully loaded before construction.
_llm_call_logger: LlmCallLogger | None = None


def _get_llm_logger() -> LlmCallLogger:
    global _llm_call_logger
    if _llm_call_logger is None:
        from pathlib import Path as _Path
        _llm_call_logger = LlmCallLogger(
            enabled=settings.llm_log_enabled,
            log_dir=_Path(__file__).parent / "logs",
        )
    return _llm_call_logger


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
    exact_terms: list[str]    # короткие фразы для точного вхождения в текст чанков
    regex_patterns: list[str] # regex-паттерны (пусто если не нужны)
    reasoning: str            # краткое объяснение плана


class SearchEvaluation(BaseModel):
    """Результат оценки найденных чанков — нужна ли дополнительная итерация поиска."""
    needs_more: bool              # True = запустить ещё один поиск
    reasoning: str                # объяснение решения
    new_search_queries: list[str] # новые перефразировки (если needs_more=True)
    new_exact_terms: list[str]    # новые точные термины (если needs_more=True)
    new_query_type: str = "factual"  # тип запроса для второй итерации


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
    iterations: int = 1         # количество выполненных итераций поиска
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
  "exact_terms": [
    "краткая фраза",
    "аббревиатура",
    "полное название",
    "синоним"
  ],
  "regex_patterns": [],
  "reasoning": "кратко: что ищем и почему такой план"
}}

Правила:
- query_type = "pattern_search" если ищут IP, номера, коды, шаблоны
- regex_patterns заполнять только при pattern_search
- search_queries: 2-4 штуки, разными словами — для семантического поиска по смыслу
- exact_terms: 3-6 коротких фраз для поиска по точному вхождению в текст документа.
  Как составлять exact_terms:
  * аббревиатуру из запроса — добавить как есть: "СУБД"
  * составное понятие — добавить ключевое слово и полную фразу: "СУБД", "типы СУБД", "виды СУБД"
  * раскрыть аббревиатуру полностью: "система управления базами данных"
  * добавить синонимы: "база данных", "хранилище данных"
  Пример для запроса "какие типы СУБД используются":
    exact_terms: ["СУБД", "типы СУБД", "виды СУБД", "категории СУБД", "система управления базами данных"]
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


_EVALUATION_PROMPT = """\
Ты — аналитик качества поиска по документации СОИБ КЦОИ Банка России.

Пользователь задал вопрос, был выполнен поиск, найдены следующие фрагменты документов.
Твоя задача: оценить, достаточно ли найденной информации для полного ответа на вопрос.

Вопрос: {question}

Найденные фрагменты (краткое содержание):
{found_summary}

Верни ТОЛЬКО валидный JSON без комментариев и markdown-блоков:
{{
  "needs_more": <true|false>,
  "reasoning": "объяснение: что найдено, что отсутствует, почему нужен/не нужен доп. поиск",
  "new_search_queries": [
    "альтернативная формулировка 1",
    "альтернативная формулировка 2"
  ],
  "new_exact_terms": [
    "точный термин 1",
    "точный термин 2"
  ],
  "new_query_type": "<factual|list|comparison|pattern_search>"
}}

Правила:
- needs_more = true ТОЛЬКО если в найденных фрагментах явно не хватает конкретных данных
  (например, упоминается таблица, но её строки не найдены; или ответ частичный)
- needs_more = false если найдено достаточно для ответа, даже если ответ неполный
- new_search_queries: 2-3 формулировки, принципиально отличные от предыдущих
- new_exact_terms: 2-4 точных термина, которые могли быть пропущены
- если needs_more = false — new_search_queries и new_exact_terms могут быть пустыми
"""


# ---------------------------------------------------------------------------
# Шаги агентного пайплайна
# ---------------------------------------------------------------------------

_LLM_RERANK_PROMPT = """\
Ты — ранжировщик фрагментов документации. Оцени релевантность каждого фрагмента \
для ответа на вопрос пользователя.

Вопрос: {question}

Фрагменты документов:
{chunks_text}

Верни ТОЛЬКО валидный JSON без комментариев и markdown-блоков:
{{
  "ranked_indices": [<индекс наиболее релевантного>, ..., <индекс наименее релевантного>],
  "reasoning": "кратко: почему такой порядок"
}}

Правила:
- ranked_indices должен содержать ВСЕ индексы от 0 до {last_idx} ровно по одному разу
- первыми ставь фрагменты, которые напрямую отвечают на вопрос (факты, таблицы, списки)
- затем — косвенно релевантные (контекст, определения)
- в конце — нерелевантные
"""

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
    with _get_llm_logger().record("analyze_query") as rec:
        rendered = prompt.format(question=question, history=history_block)
        rec.set_request(rendered)
        raw = chain.invoke({"question": question, "history": history_block})
        rec.set_response(raw)

    clean = _strip_think_tags(raw)

    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not json_match:
        logger.warning(f"LLM вернул не-JSON при анализе запроса, используем fallback\nRaw: {raw[:300]}")
        return QueryAnalysis(
            query_type="factual",
            key_terms=[question],
            search_queries=[question],
            exact_terms=[],
            regex_patterns=[],
            reasoning="fallback: не удалось распарсить план",
        )

    try:
        data = json.loads(json_match.group(0))
        # exact_terms может отсутствовать в ответе старых версий промпта
        data.setdefault("exact_terms", [])
        analysis = QueryAnalysis(**data)
    except Exception as exc:
        logger.warning(f"Ошибка парсинга QueryAnalysis: {exc}\nRaw: {raw[:300]}")
        analysis = QueryAnalysis(
            query_type="factual",
            key_terms=[question],
            search_queries=[question],
            exact_terms=[],
            regex_patterns=[],
            reasoning=f"fallback: {exc}",
        )

    logger.info(
        f"План поиска построен\n"
        f"  Тип запроса:    {analysis.query_type}\n"
        f"  Ключевые слова: {json.dumps(analysis.key_terms, ensure_ascii=False)}\n"
        f"  Semantic queries: {len(analysis.search_queries)}\n"
        f"  Exact terms:    {json.dumps(analysis.exact_terms, ensure_ascii=False)}\n"
        f"  Regex-паттернов: {len(analysis.regex_patterns)}\n"
        f"  Обоснование: {analysis.reasoning}"
    )
    return analysis


def evaluate_search_results(
    llm: ChatOllama,
    question: str,
    docs: list[Document],
    max_summary_chars: int = 8000,
) -> SearchEvaluation:
    """
    Шаг 1.5: LLM оценивает найденные чанки и решает, нужна ли вторая итерация поиска.

    Передаёт LLM краткое содержание найденных фрагментов (source + section + начало текста).
    Ограничивает суммарный размер сводки до max_summary_chars символов.

    Возвращает SearchEvaluation:
      - needs_more=True  → запустить вторую итерацию с new_search_queries / new_exact_terms
      - needs_more=False → текущих данных достаточно для финального ответа
    """
    # Формируем краткую сводку найденных фрагментов для передачи в LLM
    summary_lines: list[str] = []
    total_chars = 0
    for i, doc in enumerate(docs, 1):
        src = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "")
        chunk_type = doc.metadata.get("chunk_type", "")
        preview = doc.page_content[:300].replace("\n", " ")
        line = f"[{i}] [{src}] {section} ({chunk_type}): {preview}"
        total_chars += len(line)
        if total_chars > max_summary_chars:
            summary_lines.append(f"... [ещё {len(docs) - i} фрагментов]")
            break
        summary_lines.append(line)

    found_summary = "\n".join(summary_lines)

    prompt = ChatPromptTemplate.from_template(_EVALUATION_PROMPT)
    chain = prompt | llm | StrOutputParser()

    logger.debug(f"Оцениваем результаты поиска: {len(docs)} чанков")
    with _get_llm_logger().record("evaluate_search") as rec:
        rendered = prompt.format(question=question, found_summary=found_summary)
        rec.set_request(rendered)
        raw = chain.invoke({"question": question, "found_summary": found_summary})
        rec.set_response(raw)

    clean = _strip_think_tags(raw)
    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not json_match:
        logger.warning(f"LLM вернул не-JSON при оценке, пропускаем вторую итерацию\nRaw: {raw[:300]}")
        return SearchEvaluation(
            needs_more=False,
            reasoning="не удалось распарсить оценку LLM",
            new_search_queries=[],
            new_exact_terms=[],
        )

    try:
        data = json.loads(json_match.group(0))
        data.setdefault("new_search_queries", [])
        data.setdefault("new_exact_terms", [])
        data.setdefault("new_query_type", "factual")
        evaluation = SearchEvaluation(**data)
    except Exception as exc:
        logger.warning(f"Ошибка парсинга SearchEvaluation: {exc}\nRaw: {raw[:300]}")
        return SearchEvaluation(
            needs_more=False,
            reasoning=f"ошибка парсинга: {exc}",
            new_search_queries=[],
            new_exact_terms=[],
        )

    logger.info(
        f"Оценка результатов поиска\n"
        f"  needs_more: {evaluation.needs_more}\n"
        f"  Обоснование: {evaluation.reasoning[:200]}\n"
        + (
            f"  Новые запросы: {evaluation.new_search_queries}\n"
            f"  Новые термины: {evaluation.new_exact_terms}"
            if evaluation.needs_more else ""
        )
    )
    return evaluation


def llm_rerank_docs(
    llm: ChatOllama,
    question: str,
    docs: list[Document],
    top_n: int,
    batch_size: int = 20,
) -> list[Document]:
    """
    Шаг 2.5: Listwise LLM-ранжирование найденных чанков.

    Алгоритм:
      1. Отделяем positional-чанки (с line_start) от non-positional (regex и т.п.).
         Non-positional всегда сохраняются — они содержат точно найденные данные.
      2. Разбиваем positional на батчи по batch_size.
      3. Для каждого батча LLM получает краткое описание чанков (source + section +
         250 символов содержимого) и возвращает ranked_indices — список индексов
         от наиболее к наименее релевантному.
      4. Каждой позиции присваивается score = batch_size - rank_pos.
         Батчи сравнимы по величине, т.к. одинакового размера.
      5. Если батчей > 1 — дополнительный финальный проход по топ-кандидатам
         из каждого батча для inter-batch сравнения.
      6. Возвращает top_n наиболее релевантных positional + все non-positional.

    При ошибке парсинга LLM-ответа батч сохраняется в исходном порядке.
    """
    if not docs or top_n <= 0:
        return docs

    positional = [d for d in docs if d.metadata.get("line_start", 0) != 0
                  and not d.metadata.get("source", "").startswith("regex")]
    non_positional = [d for d in docs if d not in positional]

    if len(positional) <= top_n:
        logger.debug(f"LLM rerank: пропускаем, positional чанков ({len(positional)}) ≤ top_n ({top_n})")
        return docs

    def _chunk_summary(idx: int, doc: Document) -> str:
        src = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "")[:60]
        preview = doc.page_content[:250].replace("\n", " ")
        return f"[{idx}] [{src}] — {section}\n    {preview}"

    def _rank_batch(batch: list[Document], offset: int) -> dict[int, float]:
        """Ранжирует батч, возвращает {global_idx: score}."""
        chunks_text = "\n\n".join(_chunk_summary(j, d) for j, d in enumerate(batch))
        prompt = ChatPromptTemplate.from_template(_LLM_RERANK_PROMPT)
        chain = prompt | llm | StrOutputParser()

        with _get_llm_logger().record("llm_rerank") as rec:
            rendered = prompt.format(
                question=question,
                chunks_text=chunks_text,
                last_idx=len(batch) - 1,
            )
            rec.set_request(rendered)
            raw = chain.invoke({
                "question": question,
                "chunks_text": chunks_text,
                "last_idx": len(batch) - 1,
            })
            rec.set_response(raw)

        clean = _strip_think_tags(raw)
        json_match = re.search(r'\{.*\}', clean, re.DOTALL)
        if not json_match:
            logger.warning(f"LLM rerank: не удалось распарсить ответ батча, сохраняем исходный порядок")
            return {offset + j: float(len(batch) - j) for j in range(len(batch))}

        try:
            data = json.loads(json_match.group(0))
            ranked = data.get("ranked_indices", [])
            reasoning = data.get("reasoning", "")
            logger.debug(f"LLM rerank батч [{offset}..{offset+len(batch)-1}]: {reasoning[:100]}")
        except Exception as exc:
            logger.warning(f"LLM rerank: ошибка парсинга: {exc}")
            return {offset + j: float(len(batch) - j) for j in range(len(batch))}

        scores: dict[int, float] = {}
        seen: set[int] = set()
        for rank_pos, local_idx in enumerate(ranked):
            if isinstance(local_idx, int) and 0 <= local_idx < len(batch) and local_idx not in seen:
                seen.add(local_idx)
                scores[offset + local_idx] = float(len(batch) - rank_pos)
        # Добавляем пропущенные индексы (LLM мог забыть часть)
        for j in range(len(batch)):
            if (offset + j) not in scores:
                scores[offset + j] = 0.0
        return scores

    # Ранжируем батчи
    batches = [positional[i:i + batch_size] for i in range(0, len(positional), batch_size)]
    all_scores: dict[int, float] = {}

    for b_idx, batch in enumerate(batches):
        batch_scores = _rank_batch(batch, b_idx * batch_size)
        all_scores.update(batch_scores)

    # Финальный проход если батчей > 1: берём топ-k из каждого батча и ранжируем ещё раз
    if len(batches) > 1:
        top_per_batch = max(1, top_n // len(batches) + 1)
        candidates_per_batch: list[Document] = []
        candidate_original_indices: list[int] = []

        for b_idx, batch in enumerate(batches):
            batch_indices = list(range(b_idx * batch_size, b_idx * batch_size + len(batch)))
            sorted_local = sorted(batch_indices, key=lambda i: all_scores[i], reverse=True)
            for gi in sorted_local[:top_per_batch]:
                candidates_per_batch.append(positional[gi])
                candidate_original_indices.append(gi)

        final_scores = _rank_batch(candidates_per_batch, 0)
        # Переносим финальные баллы на оригинальные индексы с бонусом
        bonus = float(batch_size * len(batches))
        for final_idx, orig_idx in enumerate(candidate_original_indices):
            all_scores[orig_idx] = final_scores.get(final_idx, 0.0) + bonus

    # Сортируем positional по score
    sorted_positional = sorted(
        range(len(positional)),
        key=lambda i: all_scores.get(i, 0.0),
        reverse=True,
    )
    top_positional = [positional[i] for i in sorted_positional[:top_n]]

    logger.info(
        f"LLM reranking завершён\n"
        f"  Positional чанков: {len(positional)} → топ {len(top_positional)}\n"
        f"  Non-positional (без изменений): {len(non_positional)}\n"
        f"  Батчей LLM: {len(batches)}" + (" + 1 финальный" if len(batches) > 1 else "")
    )
    return non_positional + top_positional


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

    seen: set[str] = set()
    all_docs: list[Document] = []

    # Параллельные семантические поиски по всем перефразировкам.
    # Каждый поток получает свой клон vectorstore с независимым HTTP-клиентом,
    # т.к. clickhouse-connect не поддерживает конкурентные запросы в одной сессии.
    def _search(query: str) -> tuple[str, list[Document]]:
        logger.debug(f"Semantic search: {query}")
        return query, vectorstore.clone().similarity_search(query, k=top_k)

    logger.info(
        f"Семантический поиск по {len(analysis.search_queries)} запросам (top_k={top_k}):\n"
        + "\n".join(f"  [{i+1}] {q}" for i, q in enumerate(analysis.search_queries))
    )

    with ThreadPoolExecutor(max_workers=len(analysis.search_queries)) as executor:
        futures = {executor.submit(_search, q): q for q in analysis.search_queries}
        for future in as_completed(futures):
            try:
                query, docs = future.result()
                logger.debug(f"  → '{query[:80]}': {len(docs)} чанков")
                for doc in docs:
                    key = doc.page_content[:200]
                    if key not in seen:
                        seen.add(key)
                        all_docs.append(doc)
            except Exception as exc:
                logger.warning(f"Ошибка семантического поиска: {exc}")

    # Log semantic search results
    _get_llm_logger().log_event(
        "semantic_search",
        f"Queries ({len(analysis.search_queries)}):\n"
        + "\n".join(f"  [{i+1}] {q}" for i, q in enumerate(analysis.search_queries))
        + f"\n\nFound {len(all_docs)} unique chunks:\n"
        + "\n".join(
            f"  [{i+1}] [{d.metadata.get('source','')}] {d.metadata.get('section','')[:60]}\n"
            f"       {d.page_content[:150].replace(chr(10), ' | ')}"
            for i, d in enumerate(all_docs[:20])
        )
        + (f"\n  ... and {len(all_docs) - 20} more" if len(all_docs) > 20 else "")
    )

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

    # Exact-term поиск по точному вхождению (из analysis.exact_terms)
    exact_docs_added: list[tuple[str, int]] = []  # (term, added_count) for logging
    if analysis.exact_terms and hasattr(vectorstore, "exact_search"):
        logger.info(
            f"Exact-term поиск по {len(analysis.exact_terms)} фразам:\n"
            + "\n".join(f"  \"{t}\"" for t in analysis.exact_terms)
        )
        for term in analysis.exact_terms:
            try:
                exact_docs = vectorstore.exact_search(term, limit=20)
                added = 0
                for doc in exact_docs:
                    key = doc.page_content[:200]
                    if key not in seen:
                        seen.add(key)
                        all_docs.append(doc)
                        added += 1
                exact_docs_added.append((term, added))
                logger.debug(f"  exact '{term}': {len(exact_docs)} найдено, {added} новых")
            except Exception as exc:
                logger.warning(f"Ошибка exact_search для '{term}': {exc}")

        _get_llm_logger().log_event(
            "exact_search",
            "Exact-term results:\n"
            + "\n".join(f"  \"{term}\": +{cnt} new chunks" for term, cnt in exact_docs_added)
        )

    logger.info(
        f"Поиск завершён\n"
        f"  Уникальных чанков: {len(all_docs)}\n"
        f"  Semantic запросов (параллельно): {len(analysis.search_queries)}\n"
        f"  Exact terms: {len(analysis.exact_terms)}\n"
        f"  top_k per query: {top_k} (list-mode: {analysis.query_type == 'list'})\n"
        f"  Forced regex (pre-analysis): {len(forced)}\n"
        f"  LLM regex: {len(analysis.regex_patterns)}\n"
        f"  Score threshold: {settings.retriever_score_threshold or 'off'}"
    )
    return all_docs


# ---------------------------------------------------------------------------
# Шаг 3: Обогащение соседними чанками — добавляем N предыдущих и N следующих
# ---------------------------------------------------------------------------

def enrich_with_neighbor_chunks(
    docs: list[Document],
    vectorstore: ClickHouseVectorStore,
) -> list[Document]:
    """
    Шаг 3: Для каждого якорного чанка загружает соседей из того же файла
    и склеивает их в единый текст (merged_group).

    Количество соседей определяется по символьному бюджету:
      - preceding (до якоря): settings.enrich_before_chars  (по умолчанию 3000)
      - following (после якоря): settings.enrich_after_chars (по умолчанию 1500)
    Предыдущий контекст вдвое больше последующего, чтобы дать LLM
    достаточно «разгона» перед найденным фрагментом.

    Алгоритм на каждый якорь:
      1. Запрашивает до enrich_candidates кандидатов в каждую сторону.
      2. Trim preceding: идёт от ближайшего к якорю назад, НО останавливается
         на границе подраздела — когда section чанка отличается от section якоря.
         Если раздел исчерпан раньше бюджета — берётся весь раздел.
         Если раздел длиннее бюджета — обрезаются самые дальние от якоря чанки.
      3. Trim following: берёт чанки вперёд до исчерпания after_chars.
         Граница раздела для следующих чанков не применяется.
      4. Объединяет anchor + отфильтрованных соседей, сортирует по line_start.

    Смежные группы разных якорей из одного файла объединяются в один Document.
    Non-positional чанки (regex, line_start==0) передаются без изменений.
    """
    before_chars: int = settings.enrich_before_chars
    after_chars: int = settings.enrich_after_chars
    candidates: int = settings.enrich_candidates

    # Разделяем позиционные якоря и non-positional (regex / без line_start)
    positional: list[Document] = []
    non_positional: list[Document] = []
    for doc in docs:
        source = doc.metadata.get("source", "")
        line_start = doc.metadata.get("line_start", 0)
        if source and not source.startswith("regex") and line_start != 0:
            positional.append(doc)
        else:
            non_positional.append(doc)

    seen_anchor_keys: set[tuple[str, int]] = set()
    # source -> {line_start -> Document} — все отобранные куски
    source_pieces: dict[str, dict[int, Document]] = {}

    for anchor in positional:
        source = anchor.metadata["source"]
        line_start = anchor.metadata["line_start"]
        key = (source, line_start)
        if key in seen_anchor_keys:
            continue
        seen_anchor_keys.add(key)

        # Загружаем кандидатов — намеренно берём много, обрежем по символам
        raw = vectorstore.get_neighbor_chunks(
            source=source,
            line_start=line_start,
            before=candidates,
            after=candidates,
        )
        # raw = предыдущие (DESC → reversed в методе → ASC) + следующие
        prev_candidates = [d for d in raw if d.metadata.get("line_start", 0) < line_start]
        next_candidates = [d for d in raw if d.metadata.get("line_start", 0) > line_start]

        # Trim preceding: идём от ближайшего к якорю назад.
        # Правило: не пересекаем границу подраздела — если section чанка
        # отличается от section якоря (начало параллельного/родительского раздела),
        # считаем это началом окна и останавливаемся.
        # Символьный бюджет (before_chars) применяется внутри найденной границы:
        # если раздел короче бюджета — берём весь раздел;
        # если длиннее — обрезаем самые дальние от якоря чанки.
        anchor_section = anchor.metadata.get("section", "")
        selected_prev: list[Document] = []
        section_boundary_hit = False
        budget = before_chars

        for chunk in reversed(prev_candidates):   # от ближайшего к якорю к самому далёкому
            chunk_section = chunk.metadata.get("section", "")
            if chunk_section != anchor_section:
                # Начало подраздела — дальше не идём
                section_boundary_hit = True
                break
            chunk_len = len(chunk.page_content)
            if budget <= 0:
                break
            selected_prev.append(chunk)
            budget -= chunk_len

        selected_prev = list(reversed(selected_prev))  # восстанавливаем порядок ASC

        logger.debug(
            f"  Якорь [{source}] line {line_start} section='{anchor_section[:50]}'\n"
            f"    preceding: {len(selected_prev)} чанков"
            + (" (граница раздела)" if section_boundary_hit else " (лимит символов)")
        )

        # Trim following по символьному бюджету
        selected_next: list[Document] = []
        budget = after_chars
        for chunk in next_candidates:             # от ближайшего к далёкому
            chunk_len = len(chunk.page_content)
            if budget <= 0:
                break
            selected_next.append(chunk)
            budget -= chunk_len

        if source not in source_pieces:
            source_pieces[source] = {}
        pieces_map = source_pieces[source]

        for piece in [anchor] + selected_prev + selected_next:
            ls = piece.metadata.get("line_start", 0)
            if ls not in pieces_map:
                pieces_map[ls] = piece

    # Склеиваем смежные куски каждого файла в непрерывные группы
    merged_docs: list[Document] = []
    total_groups = 0
    total_pieces = 0

    for source, pieces_map in source_pieces.items():
        pieces = sorted(pieces_map.values(), key=lambda d: d.metadata.get("line_start", 0))

        groups: list[list[Document]] = []
        current_group: list[Document] = []
        current_end: int = 0

        for piece in pieces:
            ls = piece.metadata.get("line_start", 0)
            le = piece.metadata.get("line_end", ls + 1)
            if not current_group:
                current_group = [piece]
                current_end = le
            elif ls <= current_end + 1:
                current_group.append(piece)
                current_end = max(current_end, le)
            else:
                groups.append(current_group)
                current_group = [piece]
                current_end = le
        if current_group:
            groups.append(current_group)

        for group in groups:
            merged_content = "\n".join(p.page_content for p in group)
            merged_meta = {
                "source":     source,
                "section":    group[0].metadata.get("section", ""),
                "chunk_type": "merged_group",
                "line_start": group[0].metadata.get("line_start", 0),
                "line_end":   group[-1].metadata.get("line_end", 0),
                "merge_size": len(group),
            }
            merged_docs.append(Document(page_content=merged_content, metadata=merged_meta))
            total_pieces += len(group)

        total_groups += len(groups)

    logger.info(
        f"Обогащение соседними чанками завершено\n"
        f"  Якорей: {len(seen_anchor_keys)}\n"
        f"  Бюджет контекста: ←{before_chars} / →{after_chars} символов\n"
        f"  Файлов: {len(source_pieces)}\n"
        f"  Merged-групп: {total_groups} (из {total_pieces} кусков)\n"
        f"  Non-positional (без изменений): {len(non_positional)}"
    )

    _get_llm_logger().log_event(
        "enrich_neighbors",
        f"Anchors: {len(seen_anchor_keys)}, groups: {total_groups}, pieces: {total_pieces}\n"
        f"Budget: before={before_chars}, after={after_chars}\n"
        + "\n".join(
            f"  [{d.metadata.get('source','')}] "
            f"lines {d.metadata.get('line_start','')}–{d.metadata.get('line_end','')} "
            f"({d.metadata.get('merge_size',1)} chunks)\n"
            f"  {d.page_content[:120].replace(chr(10), ' | ')}"
            for d in merged_docs[:10]
        )
    )

    return non_positional + merged_docs


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
                # Clean Pandoc artifacts from file header before comparing
                # e.g. "Серверы СУБД (#_Ref150262981)" → "Серверы СУБД"
                clean_header_text = _clean_header_text(header_text)
                if clean_header_text.lower() == target_header.lower():
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

    _get_llm_logger().log_event(
        "enrich_sections",
        f"Enriched {fetched}/{len(sections_to_fetch)} sections from source files:\n"
        + "\n".join(
            f"  [{d.metadata.get('source','')}] {d.metadata.get('section','')[:60]}\n"
            f"  {d.page_content[:200].replace(chr(10), ' | ')}"
            for d in extra_docs
        )
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
        elif chunk_type == "merged_group":
            ls = doc.metadata.get("line_start", "")
            le = doc.metadata.get("line_end", "")
            size = doc.metadata.get("merge_size", "")
            header = f"[{src}]" + (f" — {section}" if section else "") + f" [строки {ls}–{le}, {size} чанков]"
        else:
            header = f"[{src}]" + (f" — {section}" if section else "")

        chunk = f"{header}\n{doc.page_content}"
        chunk_len = len(chunk)

        if settings.max_context_chars > 0 and total_chars + chunk_len > settings.max_context_chars:
            remaining = settings.max_context_chars - total_chars
            if remaining > len(header) + 100:
                # Обрезаем чанк до оставшегося бюджета вместо полного пропуска
                truncated_chunk = chunk[:remaining] + "\n...[обрезано]"
                context_parts.append(truncated_chunk)
                total_chars += len(truncated_chunk)
                logger.debug(
                    f"Чанк [{src}] обрезан: {chunk_len:,} → {remaining:,} символов"
                )
            truncated_at = i + 1
            break

        context_parts.append(chunk)
        total_chars += chunk_len

    if truncated_at is not None:
        logger.warning(
            f"Контекст обрезан: использовано {truncated_at} из {len(ordered_docs)} чанков "
            f"({total_chars:,} / {settings.max_context_chars:,} символов)"
        )

    context = "\n\n---\n\n".join(context_parts)
    used_count = truncated_at if truncated_at is not None else len(ordered_docs)
    sources = list({doc.metadata.get("source", "?") for doc in ordered_docs[:used_count]})

    history_text = memory.format_for_prompt() if memory and not memory.is_empty() else ""
    history_block = f"\n{history_text}\n" if history_text else ""

    prompt = ChatPromptTemplate.from_template(_SYNTHESIS_PROMPT)
    chain = prompt | llm | StrOutputParser()
    with _get_llm_logger().record("synthesize_answer") as rec:
        rendered = prompt.format(context=context, question=question, history=history_block)
        rec.set_request(rendered)
        answer = chain.invoke({"context": context, "question": question, "history": history_block})
        rec.set_response(answer)

    answer = _strip_think_tags(answer)
    return answer, sources


# ---------------------------------------------------------------------------
# Главный метод агента
# ---------------------------------------------------------------------------

def agent_ask(
    vectorstore: ClickHouseVectorStore,
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
    2.5 rerank_docs     — cross-encoder переранжирует чанки (временно отключён)
    3.  enrich_with_neighbor_chunks — для каждого найденного чанка добавляет ±5 соседних
        по line_start из того же файла; позволяет не терять контекст из таблиц и списков
    3.5 enrich_with_full_sections — читает полный контент секции из .md файла (временно отключён)
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

    # Шаг 2: поиск (итерация 1)
    docs = multi_retrieve(vectorstore, analysis, knowledge_dir, forced_regex=forced_regex)

    # Шаг 2.5: LLM-reranking — отбираем лучшие якоря до обогащения
    docs = llm_rerank_docs(llm, question, docs, top_n=settings.reranker_top_n)
    section_refs: list = []

    # Шаг 3: обогащение соседними чанками
    docs = enrich_with_neighbor_chunks(docs, vectorstore)

    # Шаг 3.5: обогащение полными секциями из исходных файлов (временно отключён)
    # docs, section_refs = enrich_with_full_sections(docs, knowledge_dir)

    iterations = 1
    all_docs = docs

    # Шаг 1.5 → 2 → 3 (итерация 2): LLM оценивает результат и при необходимости
    # выполняет второй поиск по переформулированным запросам
    evaluation = evaluate_search_results(llm, question, all_docs)

    if evaluation.needs_more and evaluation.new_search_queries:
        logger.info(
            f"Запускаем итерацию 2\n"
            f"  Запросы: {evaluation.new_search_queries}\n"
            f"  Термины: {evaluation.new_exact_terms}"
        )
        # Формируем новый план из оценки LLM
        analysis2 = QueryAnalysis(
            query_type=evaluation.new_query_type,
            key_terms=evaluation.new_exact_terms,
            search_queries=evaluation.new_search_queries,
            exact_terms=evaluation.new_exact_terms,
            regex_patterns=[],
            reasoning=evaluation.reasoning,
        )
        docs2 = multi_retrieve(vectorstore, analysis2, knowledge_dir, forced_regex=[])
        docs2 = enrich_with_neighbor_chunks(docs2, vectorstore)

        # Объединяем с дедупликацией по content[:200]
        seen_fps: set[str] = {d.page_content[:200] for d in all_docs}
        new_docs = [d for d in docs2 if d.page_content[:200] not in seen_fps]
        all_docs = all_docs + new_docs
        iterations = 2

        logger.info(
            f"Итерация 2 завершена\n"
            f"  Новых уникальных чанков: {len(new_docs)}\n"
            f"  Итого чанков: {len(all_docs)}"
        )

    # Шаг 4: финальный синтез по всем найденным данным
    answer, sources = synthesize_answer(llm, question, all_docs, memory=memory)

    if memory is not None:
        memory.add(question, answer)
        logger.debug(f"Memory: добавлен обмен, всего в буфере: {len(memory._turns)}")

    full_section_count = sum(1 for d in all_docs if d.metadata.get("chunk_type") == "full_section")

    logger.info(
        f"Агентный ответ готов\n"
        f"  Итераций поиска: {iterations}\n"
        f"  Итого чанков: {len(all_docs)}\n"
        f"  Источников: {len(sources)}\n"
        f"  Файлы: {', '.join(sources)}"
    )
    return AgentAnswer(
        question=question,
        analysis=analysis,
        retrieved_chunks=len(all_docs),
        enriched_sections=full_section_count,
        iterations=iterations,
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
    iters_label = f" | итераций={result.iterations}" if result.iterations > 1 else ""
    print(f"[Анализ] тип={result.analysis.query_type} | чанков={result.retrieved_chunks} | секций={result.enriched_sections}{iters_label}")
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
    
    # Настраиваем логирование в файл + консоль
    _setup_logging("rag_agent")

    parser = argparse.ArgumentParser(description="Agentic RAG-чат по документации СОИБ КЦОИ")
    parser.add_argument("question", nargs="*", help="Вопрос (если не указан — интерактивный режим)")
    parser.add_argument("--ips", metavar="FILE", nargs="?", const="", help="Извлечь все IP из документов (опционально: путь к файлу)")
    args = parser.parse_args()

    logger.info(
        f"Запуск Agentic RAG\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  ClickHouse:  {settings.clickhouse_host}:{settings.clickhouse_port}"
        f" → {settings.clickhouse_database}.{settings.clickhouse_table}"
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

