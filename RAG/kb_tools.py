"""
LangChain Tools для доступа к базе знаний в ClickHouse.

Централизованный реестр инструментов:
  ALL_TOOLS                — полный список всех инструментов
  AGENT_SELECTABLE_TOOLS   — инструменты для автоматического выбора агентом

Инструменты:
  semantic_search            — семантический поиск по эмбеддингам
  exact_search               — точный поиск по одной подстроке (positionCaseInsensitive)
  exact_search_in_file       — точный поиск в конкретном файле
  exact_search_in_file_section — точный поиск в конкретном разделе файла
  multi_term_exact_search    — точный поиск по списку терминов с ранжированием по покрытию
  find_sections_by_term      — поиск разделов содержащих термин (возвращает список source+section)
  find_relevant_sections     — двухэтапный поиск: по названию раздела + по терминам в содержимом
  regex_search               — regex-поиск по исходным .md файлам с контекстом
  find_abbreviation_expansion — поиск расшифровки аббревиатур (КЦОИ -> К* Ц* О* И*)
  read_table                 — чтение строк таблицы по разделу
  get_section_content        — полный текст раздела из исходного .md файла
  list_sections              — дерево разделов базы знаний (по файлу или всей KB)
  get_neighbor_chunks        — соседние чанки вокруг якоря по line_start
  list_sources               — список файлов в базе знаний с количеством чанков
  list_all_sections          — уникальные пары (source, last_section_name) для всех разделов

Использование:
    from kb_tools import create_kb_tools, ALL_TOOLS, AGENT_SELECTABLE_TOOLS
    tools = create_kb_tools(vectorstore, knowledge_dir)
    agent = create_tool_calling_agent(llm, tools, prompt)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from clickhouse_store import ClickHouseVectorStore
from llm_call_logger import LlmCallLogger
from rag_chat import regex_search as _kb_regex_search

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic модели для результатов инструментов
# ---------------------------------------------------------------------------

class ChunkMetadata(BaseModel):
    """Метаданные чанка"""
    chunk_id: str = Field(description="Уникальный идентификатор чанка (UUID)")
    source: str = Field(description="Имя файла")
    section: str = Field(description="Путь раздела через ' > '")
    chunk_type: str = Field(description="Тип чанка: table_row, table_full, или '' (проза)")
    line_start: int = Field(description="Начальная строка в файле")
    line_end: int = Field(description="Конечная строка в файле")
    chunk_index: int = Field(description="Индекс чанка")
    table_headers: Optional[str] = Field(default=None, description="Заголовки таблицы (если есть)")


class ChunkResult(BaseModel):
    """Результат поиска чанков"""
    content: str = Field(description="Содержимое чанка")
    metadata: ChunkMetadata = Field(description="Метаданные чанка")
    score: Optional[float] = Field(
        default=None, 
        description="Оценка релевантности (distance для semantic search, coverage для multi-term)"
    )


class ScoredChunkResult(BaseModel):
    """Чанк с оценкой релевантности"""
    content: str = Field(description="Содержимое чанка")
    metadata: ChunkMetadata = Field(description="Метаданные чанка")
    score: float = Field(description="Оценка релевантности (distance или match_count)")


class SectionInfo(BaseModel):
    """Информация о разделе"""
    source: str = Field(description="Имя файла")
    section: str = Field(description="Название раздела")
    match_count: int = Field(description="Количество упоминаний/чанков")
    match_type: Optional[str] = Field(default=None, description="Тип совпадения: NAME или CONTENT")


class SearchChunksResult(BaseModel):
    """Результат поиска чанков"""
    query: str = Field(description="Исходный запрос")
    chunks: list[ChunkResult] = Field(description="Найденные чанки")
    total_found: int = Field(description="Всего найдено чанков")


class SearchSectionsResult(BaseModel):
    """Результат поиска разделов"""
    query: str = Field(description="Исходный запрос")
    sections: list[SectionInfo] = Field(description="Найденные разделы")
    total_found: int = Field(description="Всего найдено разделов")
    returned_count: int = Field(description="Возвращено разделов (с учётом limit)")


class MultiTermSearchResult(BaseModel):
    """Результат мультитермового поиска"""
    terms: list[str] = Field(description="Искомые термины")
    chunks_by_coverage: dict[int, list[ChunkResult]] = Field(
        description="Чанки сгруппированные по количеству найденных терминов"
    )
    total_chunks: int = Field(description="Всего найдено чанков")
    max_coverage: int = Field(description="Максимальное покрытие терминов")


class RegexMatch(BaseModel):
    """Одно совпадение regex"""
    file: str = Field(description="Имя файла")
    line_number: int = Field(description="Номер строки")
    matched_text: str = Field(description="Совпавший текст")
    context_before: list[str] = Field(description="Строки контекста до")
    matched_line: str = Field(description="Строка с совпадением")
    context_after: list[str] = Field(description="Строки контекста после")


class RegexSearchResult(BaseModel):
    """Результат regex поиска"""
    pattern: str = Field(description="Regex паттерн")
    matches: list[RegexMatch] = Field(description="Найденные совпадения")
    total_matches: int = Field(description="Всего совпадений")


class AbbreviationExpansionItem(BaseModel):
    """Одна найденная расшифровка аббревиатуры с чанком"""
    expansion: str = Field(description="Текст расшифровки")
    chunk: ChunkResult = Field(description="Чанк в котором найдена расшифровка")


class AbbreviationExpansionResult(BaseModel):
    """Результат поиска расшифровки аббревиатуры"""
    abbreviation: str = Field(description="Исходная аббревиатура")
    expansions: list[AbbreviationExpansionItem] = Field(description="Найденные расшифровки с чанками")
    total_found: int = Field(description="Всего найдено расшифровок")
    pattern_used: str = Field(description="Использованный regex паттерн")


class TableRow(BaseModel):
    """Строка таблицы"""
    source: str = Field(description="Имя файла")
    section: str = Field(description="Раздел с таблицей")
    line_start: int = Field(description="Начальная строка")
    columns: dict[str, str] = Field(description="Колонки и значения")


class TableResult(BaseModel):
    """Результат чтения таблицы"""
    section_query: str = Field(description="Запрос раздела")
    rows: list[TableRow] = Field(description="Строки таблицы")
    total_rows: int = Field(description="Всего строк")


class SectionContent(BaseModel):
    """Полный контент раздела"""
    source: str = Field(description="Имя файла")
    section: str = Field(description="Название раздела")
    line_start: int = Field(description="Начальная строка")
    line_end: int = Field(description="Конечная строка")
    content: str = Field(description="Полный текст раздела")


class SectionTreeNode(BaseModel):
    """Узел дерева разделов"""
    section: str = Field(description="Название раздела")
    chunks_count: int = Field(description="Количество чанков в разделе")


class SectionsTree(BaseModel):
    """Дерево разделов"""
    source: Optional[str] = Field(default=None, description="Имя файла (если фильтр применён)")
    sections: list[SectionTreeNode] = Field(description="Разделы")
    total_sections: int = Field(description="Всего разделов")


class NeighborChunksResult(BaseModel):
    """Результат получения соседних чанков"""
    anchor_line: int = Field(description="line_start якорного чанка")
    anchor_chunk: Optional["ChunkResult"] = Field(default=None, description="Сам якорный чанк (если include_anchor=True)")
    chunks_before: list[ChunkResult] = Field(description="Чанки до якоря")
    chunks_after: list[ChunkResult] = Field(description="Чанки после якоря")


class SourceInfo(BaseModel):
    """Информация о файле-источнике"""
    source: str = Field(description="Имя файла")
    chunks_count: int = Field(description="Количество чанков")


class SourcesList(BaseModel):
    """Список файлов-источников"""
    sources: list[SourceInfo] = Field(description="Файлы")
    total_sources: int = Field(description="Всего файлов")
    total_chunks: int = Field(description="Всего чанков")


# ---------------------------------------------------------------------------
# Реестр инструментов
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

# Полный список всех доступных инструментов
ALL_TOOLS = [
    "semantic_search",
    "exact_search",
    "exact_search_in_file",
    "exact_search_in_file_section",
    "multi_term_exact_search",
    "find_sections_by_term",
    "find_relevant_sections",
    "regex_search",
    "find_abbreviation_expansion",
    "read_table",
    "get_section_content",
    "list_sections",
    "get_neighbor_chunks",
    "list_sources",
    "list_all_sections",
]

# Инструменты, доступные для автоматического выбора агентом
# (исключаем служебные инструменты, которые вызываются явно)
AGENT_SELECTABLE_TOOLS = [
    "semantic_search",
    "exact_search",
    "exact_search_in_file",
    "exact_search_in_file_section",
    "multi_term_exact_search",
    "find_sections_by_term",
    "find_relevant_sections",
    "regex_search",
    "find_abbreviation_expansion",
    "read_table",
    "get_section_content",
    "list_sections",
    "get_neighbor_chunks",
]

# ---------------------------------------------------------------------------
# Helper функции для конвертации Document → структурированные результаты
# ---------------------------------------------------------------------------

def _doc_to_chunk_metadata(meta: dict) -> ChunkMetadata:
    """Конвертер метаданных Document → ChunkMetadata"""
    return ChunkMetadata(
        chunk_id=meta['chunk_id'],
        source=meta['source'],
        section=meta['section'],
        chunk_type=meta['chunk_type'],
        line_start=meta['line_start'],
        line_end=meta['line_end'],
        chunk_index=meta['chunk_index'],
        table_headers=meta.get('table_headers')
    )


def _doc_to_chunk_result(doc: Document, score: Optional[float] = None) -> ChunkResult:
    """Конвертер Document → ChunkResult"""
    return ChunkResult(
        content=doc.page_content,
        metadata=_doc_to_chunk_metadata(doc.metadata),
        score=score
    )


def _docs_to_chunk_results(docs: list[Document]) -> list[ChunkResult]:
    """Конвертер списка Document → список ChunkResult"""
    return [_doc_to_chunk_result(doc) for doc in docs]


def _docs_with_scores_to_chunk_results(docs_scores: list[tuple[Document, float]]) -> list[ChunkResult]:
    """Конвертер списка (Document, score) → список ChunkResult с scores"""
    return [_doc_to_chunk_result(doc, score) for doc, score in docs_scores]


def _doc_to_scored_chunk(doc: Document, score: float) -> ScoredChunkResult:
    """Конвертер Document + score → ScoredChunkResult"""
    return ScoredChunkResult(
        content=doc.page_content,
        metadata=_doc_to_chunk_metadata(doc.metadata),
        score=score
    )


def _doc_to_table_row(doc: Document) -> TableRow:
    """Конвертер Document → TableRow для table_row чанков"""
    # Парсим content в формате "Column: value"
    columns = {}
    for line in doc.page_content.split('\n'):
        if ':' in line:
            key, val = line.split(':', 1)
            columns[key.strip()] = val.strip()
    
    return TableRow(
        source=doc.metadata['source'],
        section=doc.metadata['section'],
        line_start=doc.metadata['line_start'],
        columns=columns
    )


def _deduplicate_chunks(chunks: list[ChunkResult]) -> list[ChunkResult]:
    """
    Дедупликация чанков по уникальной комбинации (source, section, chunk_index).
    Сохраняет первое вхождение для каждой уникальной комбинации.
    
    Args:
        chunks: Список чанков (может содержать дубликаты)
    
    Returns:
        Список уникальных чанков в исходном порядке
    """
    seen = set()
    unique_chunks = []
    
    for chunk in chunks:
        key = (
            chunk.metadata.source,
            chunk.metadata.section,
            chunk.metadata.chunk_index
        )
        if key not in seen:
            seen.add(key)
            unique_chunks.append(chunk)
    
    return unique_chunks

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _fmt_docs(docs: list[Document], max_content_chars: int = 800) -> str:
    """Formats a list of Documents into a readable text block for LLM consumption."""
    if not docs:
        return "Ничего не найдено."
    parts: list[str] = []
    for i, doc in enumerate(docs, 1):
        src       = doc.metadata.get("source", "?")
        section   = doc.metadata.get("section", "")
        chunk_type = doc.metadata.get("chunk_type", "")
        ls        = doc.metadata.get("line_start", "")

        content = doc.page_content
        # Для table_row — раскрываем JSON в читаемые пары "Столбец: значение"
        if chunk_type == "table_row":
            try:
                headers = json.loads(doc.metadata.get("table_headers", "[]"))
                cells   = json.loads(content)
                pairs   = "; ".join(f"{h}: {v}" for h, v in zip(headers, cells) if v)
                content = pairs or content
            except Exception:
                pass

        if len(content) > max_content_chars:
            content = content[:max_content_chars] + "\n...[обрезано]"

        line_info = f" (line {ls})" if ls else ""
        header    = f"[{i}] [{src}]{(' — ' + section) if section else ''}{line_info}"
        parts.append(f"{header}\n{content}")

    return "\n\n---\n\n".join(parts)


def _query_sections(
    vectorstore: ClickHouseVectorStore,
    source_file: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Returns distinct (source, section) pairs from ClickHouse.

    Uses a cloned client per call to support concurrent tool execution
    (LangGraph runs multiple tools in parallel via concurrent.futures).
    """
    # Clone to get a dedicated HTTP connection — original client is not thread-safe
    client = vectorstore.clone()._client
    db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
    if source_file:
        sql = (
            f"SELECT DISTINCT source, section "
            f"FROM {db}.{tbl} FINAL "
            f"WHERE source = {{src:String}} "
            f"ORDER BY source, section LIMIT 1000"
        )
        result = client.query(sql, parameters={"src": source_file})
    else:
        sql = (
            f"SELECT DISTINCT source, section "
            f"FROM {db}.{tbl} FINAL "
            f"ORDER BY source, section LIMIT 1000"
        )
        result = client.query(sql)
    return [(r[0], r[1]) for r in result.result_rows]


def _query_table_chunks(
    vectorstore: ClickHouseVectorStore,
    section_substring: str,
    source_file: Optional[str],
    limit: int,
) -> list[Document]:
    """Queries table_row and table_full chunks matching a section substring.

    Uses a cloned client per call to support concurrent tool execution.
    """
    client = vectorstore.clone()._client
    db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
    where = ["positionCaseInsensitiveUTF8(section, {sec:String}) > 0",
             "chunk_type IN ('table_row', 'table_full')"]
    params: dict = {"sec": section_substring, "lim": limit}
    if source_file:
        where.append("source = {src:String}")
        params["src"] = source_file

    sql = (
        f"SELECT source, section, chunk_type, table_headers, content, "
        f"       line_start, line_end, chunk_index "
        f"FROM {db}.{tbl} FINAL "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY source, section, line_start, chunk_index "
        f"LIMIT {{lim:UInt32}}"
    )
    result = client.query(sql, parameters=params)
    docs: list[Document] = []
    for row in result.result_rows:
        src, sec, ct, th, content, ls, le, ci = row
        meta: dict = {
            "source": src, "section": sec, "chunk_type": ct,
            "line_start": int(ls), "line_end": int(le), "chunk_index": int(ci),
        }
        if th:
            meta["table_headers"] = th
        docs.append(Document(page_content=content, metadata=meta))
    return docs


# ---------------------------------------------------------------------------
# Pydantic-схемы аргументов инструментов (модульный уровень)
# ---------------------------------------------------------------------------
# Defaults совпадают с параметрами-умолчаниями create_kb_tools().
# Схемы живут здесь — не внутри create_kb_tools — чтобы CLI мог валидировать
# аргументы ДО подключения к БД (lazy init pattern).

_SEMANTIC_TOP_K = 10
_EXACT_LIMIT = 30
_REGEX_MAX = 50


class SemanticSearchInput(BaseModel):
    query: str = Field(description="Query text for semantic similarity search (in Russian or English)")
    top_k: int = Field(default=_SEMANTIC_TOP_K, description="Number of results to return", ge=1, le=50)
    chunk_type: str = Field(
        default="",
        description="Filter by chunk type: '' (prose chunks, default), 'table_row' (table rows), 'table_full' (full tables), or empty string for prose only"
    )
    source: Optional[str] = Field(default=None, description="Optional: filter by source filename (e.g. 'servers.md')")
    section: Optional[str] = Field(default=None, description="Optional: filter by section name/breadcrumb substring")


class ExactSearchInput(BaseModel):
    substring: str = Field(description="Exact substring to search (case-insensitive)")
    limit: int = Field(default=_EXACT_LIMIT, description="Max results", ge=1, le=200)
    chunk_type: str = Field(
        default="",
        description="Filter by chunk type: '' (prose chunks, default), 'table_row' (table rows), 'table_full' (full tables)"
    )
    source: Optional[str] = Field(default=None, description="Optional: filter by source filename (e.g. 'servers.md')")
    section: Optional[str] = Field(default=None, description="Optional: filter by section name/breadcrumb substring")


class ExactSearchInFileInput(BaseModel):
    substring: str = Field(description="Exact substring to search (case-insensitive)")
    source_file: str = Field(description="Source filename to search in (e.g. 'servers.md')")
    limit: int = Field(default=_EXACT_LIMIT, description="Max results", ge=1, le=200)
    chunk_type: Optional[str] = Field(
        default=None,
        description="Filter by chunk type: 'table_row', 'table_full', '' (prose), None (all)",
    )


class ExactSearchInFileSectionInput(BaseModel):
    substring: str = Field(description="Exact substring to search (case-insensitive)")
    source_file: str = Field(description="Source filename to search in (e.g. 'servers.md')")
    section: str = Field(description="Section name or breadcrumb substring to search in")
    limit: int = Field(default=_EXACT_LIMIT, description="Max results", ge=1, le=200)
    chunk_type: Optional[str] = Field(
        default=None,
        description="Filter by chunk type: 'table_row', 'table_full', '' (prose), None (all)",
    )


class MultiTermExactSearchInput(BaseModel):
    terms: list[str] = Field(
        description=(
            "List of UNIQUE substrings to search simultaneously (case-insensitive). "
            "Each chunk is scored by how many terms it contains. "
            "Results are ranked: chunks matching ALL terms first, then most terms, "
            "then fewer — so the most relevant results always appear at the top. "
            "NOTE: Duplicate terms will be automatically removed."
        )
    )
    limit: int = Field(default=_EXACT_LIMIT, description="Max results", ge=1, le=200)
    chunk_type: str = Field(
        default="",
        description="Filter by chunk type: '' (prose chunks, default), 'table_row', 'table_full'"
    )
    source: Optional[str] = Field(default=None, description="Optional: filter by source filename (e.g. 'servers.md')")
    section: Optional[str] = Field(default=None, description="Optional: filter by section name/breadcrumb substring")


class RegexSearchInput(BaseModel):
    pattern: str = Field(
        description=r"Regex pattern to search in source .md files. "
                    r"Examples: r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}' for IPs, "
                    r"r'порт\s*:?\s*\d+' for ports, r'vlan\s*:?\s*\d+' for VLANs."
    )
    max_results: int = Field(default=_REGEX_MAX, description="Max matches to return", ge=1, le=200)


class FindAbbreviationExpansionInput(BaseModel):
    abbreviation: str = Field(
        description="Abbreviation in CAPITAL LETTERS to find expansion for. Supports letters and digits (e.g. 'КЦОИ', 'RAM', 'API', 'AK47', 'T34')"
    )
    max_results: int = Field(default=_REGEX_MAX, description="Max matches to return", ge=1, le=200)


class ReadTableInput(BaseModel):
    section: str = Field(
        description="Section breadcrumb substring to find tables in (e.g. 'Серверы СУБД', 'Сетевое оборудование')"
    )
    source_file: Optional[str] = Field(default=None, description="Filter by source filename (e.g. 'servers.md')")
    limit: int = Field(default=50, description="Max rows to return", ge=1, le=500)


class GetSectionContentInput(BaseModel):
    source_file: str = Field(description="Source .md filename (e.g. 'Общее описание системы.md')")
    section: str = Field(
        description="Section name or breadcrumb path (last component is used for matching). "
                    "Example: 'Серверы СУБД' or 'Общее описание > Серверы СУБД'"
    )


class ListSectionsInput(BaseModel):
    source_file: Optional[str] = Field(
        default=None,
        description="Filter by source filename. Pass None to list sections from all files.",
    )


class GetNeighborChunksInput(BaseModel):
    source: str = Field(description="Source filename (from previous search result metadata)")
    line_start: int = Field(description="line_start value of the anchor chunk", ge=1)
    before: int = Field(default=5, description="Number of chunks before the anchor", ge=0, le=30)
    after: int = Field(default=5, description="Number of chunks after the anchor", ge=0, le=30)


class GetChunksByIndexInput(BaseModel):
    source: str = Field(description="Source filename (e.g. 'servers.md')")
    section: str = Field(description="Section name or breadcrumb path")
    chunk_indices: list[int] = Field(
        description="List of chunk indices to retrieve (e.g. [0, 1, 5])",
        min_length=1,
        max_length=50
    )


class FindSectionsByTermInput(BaseModel):
    substring: str = Field(description="Exact substring to search (case-insensitive)")
    limit: int = Field(
        default=100,
        description="Max chunks to scan (all sections within this limit are returned)",
        ge=1,
        le=500
    )
    chunk_type: Optional[str] = Field(
        default=None,
        description="Filter by chunk type: 'table_row', 'table_full', '' (prose), None (all)",
    )
    source: Optional[str] = Field(default=None, description="Optional: filter by source filename")


class FindRelevantSectionsInput(BaseModel):
    query: str = Field(description="User query phrase (for section name matching)")
    exact_terms: list[str] = Field(
        description="List of exact terms to search in content (for content matching)",
        default_factory=list
    )
    limit: int = Field(default=50, description="Maximum number of sections to return in results", ge=1, le=200)
    source: Optional[str] = Field(default=None, description="Optional: filter by source filename")


# Реестр схем: tool_name → Input-класс. Используется для pre-init валидации в CLI.
TOOL_INPUT_SCHEMAS: dict[str, type[BaseModel] | None] = {
    "semantic_search":              SemanticSearchInput,
    "exact_search":                 ExactSearchInput,
    "exact_search_in_file":         ExactSearchInFileInput,
    "exact_search_in_file_section": ExactSearchInFileSectionInput,
    "multi_term_exact_search":      MultiTermExactSearchInput,
    "find_sections_by_term":        FindSectionsByTermInput,
    "find_relevant_sections":       FindRelevantSectionsInput,
    "regex_search":                 RegexSearchInput,
    "find_abbreviation_expansion":  FindAbbreviationExpansionInput,
    "read_table":                   ReadTableInput,
    "get_section_content":          GetSectionContentInput,
    "list_sections":                ListSectionsInput,
    "get_neighbor_chunks":          GetNeighborChunksInput,
    "get_chunks_by_index":          GetChunksByIndexInput,
    "list_sources":                 None,  # нет параметров
    "list_all_sections":            None,  # нет параметров
}


# ---------------------------------------------------------------------------
# Фабрика инструментов
# ---------------------------------------------------------------------------

def create_kb_tools(
    vectorstore: ClickHouseVectorStore,
    knowledge_dir: Path,
    semantic_top_k: int = 10,
    exact_limit: int = 30,
    regex_max_results: int = 50,
    llm_logger: LlmCallLogger | None = None,
) -> list[BaseTool]:
    """
    Creates LangChain tools for knowledge base access.

    Each tool captures vectorstore and knowledge_dir via closure.
    Vectorstore is cloned per call to ensure thread safety.

    Args:
        vectorstore:       Initialized ClickHouseVectorStore.
        knowledge_dir:     Path to the directory with source .md files.
        semantic_top_k:    Default number of results for semantic search.
        exact_limit:       Default result limit for exact search.
        regex_max_results: Max matches to return from regex search.
        llm_logger:        Optional LlmCallLogger for writing DB queries to file.

    Returns:
        List of ready-to-use LangChain BaseTool instances.
    """
    # Convenience shortcut — creates a _CallRecord and writes REQUEST immediately.
    # Returns the record so the caller can write RESPONSE later.
    # Returns None when logger is None or disabled (caller should check).
    def _db_request(step: str, request: str):
        if llm_logger is not None:
            rec = llm_logger.start_record(step)
            rec.set_request(request)
            return rec
        return None

    # ── Tool implementations ──────────────────────────────────────────

    @tool(args_schema=SemanticSearchInput)
    def semantic_search(
        query: str,
        top_k: int = semantic_top_k,
        chunk_type: str = "",
        source: Optional[str] = None,
        section: Optional[str] = None
    ) -> SearchChunksResult:
        """
        Семантический поиск по базе знаний с помощью векторных эмбеддингов (bge-m3).
        Лучше всего для: концептуальных вопросов, «что такое X», «как работает Y», широкого поиска по теме.
        Возвращает top-K наиболее семантически близких текстовых чанков из ClickHouse.
        По умолчанию ищет только в текстовых чанках (chunk_type=""), не в полных таблицах.
        Метаданные включают: исходный файл, breadcrumb раздела, line_start для расширения контекста.
        Каждый результат содержит score (косинусное расстояние) — релевантность (меньше = ближе).

        Необязательные фильтры:
        - chunk_type: фильтр по типу чанка (по умолчанию "" — только проза)
        - source: ограничить поиск конкретным файлом (например, 'servers.md')
        - section: ограничить поиск подстрокой названия раздела

        Возвращает:
            SearchChunksResult: запрос, список чанков с метаданными и score, и количество total_found
        """
        filter_info = []
        if chunk_type:
            filter_info.append(f"chunk_type={chunk_type!r}")
        if source:
            filter_info.append(f"file={source}")
        if section:
            filter_info.append(f"section~{section}")
        filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""

        logger.debug(f"Tool semantic_search: query='{query[:80]}'{filter_str}, top_k={top_k}")
        rec = _db_request("DB:semantic_search", f"query={query!r}\ntop_k={top_k}{filter_str}")
        
        # Поиск с фильтром по chunk_type (получаем scores)
        docs_scores = vectorstore.clone().similarity_search_with_score(
            query, k=top_k, chunk_type=chunk_type, source=source, section=section
        )
        
        # Конвертация в структурированный результат с scores и дедупликация
        chunks = _deduplicate_chunks(_docs_with_scores_to_chunk_results(docs_scores))
        result = SearchChunksResult(
            query=query,
            chunks=chunks,
            total_found=len(chunks)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(f"semantic_search '{query[:60]}'{filter_str}: {len(chunks)} чанков")
        
        return result

    @tool(args_schema=ExactSearchInput)
    def exact_search(
        substring: str,
        limit: int = exact_limit,
        chunk_type: str = "",
        source: Optional[str] = None,
        section: Optional[str] = None
    ) -> SearchChunksResult:
        """
        Точный регистронезависимый поиск подстроки в содержимом базы знаний (positionCaseInsensitiveUTF8).
        Лучше всего для: конкретных терминов, аббревиатур, названий систем, заголовков разделов.
        По умолчанию ищет только в текстовых чанках (chunk_type=""), не в полных таблицах.
        chunk_type='table_row' — искать только в данных таблиц (каждая строка = отдельный чанк).
        chunk_type='table_full' — получить таблицы целиком.
        Каждый результат содержит исходный файл, breadcrumb раздела и line_start для расширения контекста.

        Необязательные фильтры:
        - chunk_type: фильтр по типу чанка (по умолчанию "" — только проза)
        - source: ограничить поиск конкретным файлом (например, 'servers.md')
        - section: ограничить поиск подстрокой названия раздела

        Если заданы и source, и section, выполняется максимально точечный поиск
        (эквивалент exact_search_in_file_section).

        Возвращает:
            SearchChunksResult: подстрока как запрос, список чанков и количество total_found
        """
        filter_info = []
        if source:
            filter_info.append(f"file={source}")
        if section:
            filter_info.append(f"section~{section}")
        filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""

        logger.debug(f"Tool exact_search: substring='{substring}'{filter_str}, chunk_type={chunk_type!r}")
        rec = _db_request(
            "DB:exact_search",
            f"substring={substring!r}\nlimit={limit}\nchunk_type={chunk_type!r}{filter_str}",
        )
        
        # Поиск
        docs = vectorstore.clone().exact_search(
            substring, limit=limit, chunk_type=chunk_type, source=source, section=section
        )
        
        # Конвертация в структурированный результат и дедупликация
        chunks = _deduplicate_chunks(_docs_to_chunk_results(docs))
        result = SearchChunksResult(
            query=substring,
            chunks=chunks,
            total_found=len(chunks)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(f"exact_search '{substring}'{filter_str}: {len(chunks)} чанков")
        
        return result

    @tool(args_schema=ExactSearchInFileInput)
    def exact_search_in_file(
        substring: str,
        source_file: str,
        limit: int = exact_limit,
        chunk_type: Optional[str] = None
    ) -> SearchChunksResult:
        """
        Точный регистронезависимый поиск подстроки в пределах конкретного файла.
        Лучше всего для: сфокусированного поиска, когда известно точное имя файла; сужения
        результатов до одного документа и отсечения шума из других файлов.
        Сначала используйте list_sources, чтобы узнать доступные имена файлов.
        Возвращает чанки только из указанного файла, отсортированные по номеру строки.
        Каждый результат содержит breadcrumb раздела и line_start для расширения контекста.

        Возвращает:
            SearchChunksResult: подстрока как запрос, чанки из указанного файла, количество total_found
        """
        logger.debug(f"Tool exact_search_in_file: substring='{substring}', file='{source_file}'")
        rec = _db_request(
            "DB:exact_search_in_file",
            f"substring={substring!r}\nsource_file={source_file!r}\nlimit={limit}\nchunk_type={chunk_type!r}",
        )
        
        # Поиск
        docs = vectorstore.clone().exact_search_in_file(
            substring, source_file=source_file, limit=limit, chunk_type=chunk_type
        )
        
        # Конвертация и дедупликация
        chunks = _deduplicate_chunks(_docs_to_chunk_results(docs))
        result = SearchChunksResult(
            query=substring,
            chunks=chunks,
            total_found=len(chunks)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(f"exact_search_in_file '{substring}' in {source_file}: {len(chunks)} чанков")
        
        return result

    @tool(args_schema=ExactSearchInFileSectionInput)
    def exact_search_in_file_section(
        substring: str,
        source_file: str,
        section: str,
        limit: int = exact_limit,
        chunk_type: Optional[str] = None
    ) -> SearchChunksResult:
        """
        Точный регистронезависимый поиск подстроки в конкретном разделе конкретного файла.
        Лучше всего для: максимально точечного поиска, когда известны и файл, и раздел;
        поиска конкретных данных в известной структуре документации (например, IP в 'Servers > Database').
        Сначала используйте list_sections, чтобы узнать точные названия разделов.
        Возвращает чанки, совпавшие по файлу + разделу + подстроке, отсортированные по номеру строки.
        Самый точный поисковый инструмент — минимум шума, максимум релевантности.

        Возвращает:
            SearchChunksResult: подстрока как запрос, чанки из указанного раздела, количество total_found
        """
        logger.debug(
            f"Tool exact_search_in_file_section: substring='{substring}', "
            f"file='{source_file}', section='{section}'"
        )
        rec = _db_request(
            "DB:exact_search_in_file_section",
            f"substring={substring!r}\nsource_file={source_file!r}\nsection={section!r}\n"
            f"limit={limit}\nchunk_type={chunk_type!r}",
        )
        
        # Поиск
        docs = vectorstore.clone().exact_search_in_file_section(
            substring, source_file, section, limit=limit, chunk_type=chunk_type
        )
        
        # Конвертация и дедупликация
        chunks = _deduplicate_chunks(_docs_to_chunk_results(docs))
        result = SearchChunksResult(
            query=substring,
            chunks=chunks,
            total_found=len(chunks)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"exact_search_in_file_section '{substring}' в [{source_file}] -> {section}: {len(chunks)} чанков"
        )
        
        return result

    @tool(args_schema=MultiTermExactSearchInput)
    def multi_term_exact_search(
        terms: list[str],
        limit: int = exact_limit,
        chunk_type: str = "",
        source: Optional[str] = None,
        section: Optional[str] = None
    ) -> MultiTermSearchResult:
        """
        Многотерминный точный поиск с ранжированием по числу совпавших терминов в чанке.
        По умолчанию ищет только в текстовых чанках (chunk_type=""), не в полных таблицах.

        Ищет все переданные термины одновременно по всей базе знаний.
        Каждому чанку присваивается match_count = число найденных в нём терминов.
        Результаты сортируются: сначала чанки со ВСЕМИ терминами, затем с большим их числом, затем с меньшим.

        Используйте это как ПЕРВЫЙ шаг точного поиска, когда есть несколько ключевых терминов —
        за один вызов находит наиболее релевантные чанки (с максимальным покрытием терминов).
        Для терминов, не покрытых верхними результатами, добавьте отдельные вызовы exact_search.

        Возвращает результаты, сгруппированные по match_count (покрытию), с чанками по релевантности.

        Необязательные фильтры:
        - chunk_type: фильтр по типу чанка (по умолчанию "" — только проза)
        - source: ограничить поиск конкретным файлом (например, 'servers.md')
        - section: ограничить поиск подстрокой названия раздела

        Возвращает:
            MultiTermSearchResult: термины, словарь chunks_by_coverage, total_chunks и max_coverage
        """
        # Дедупликация терминов (удаление повторяющихся)
        unique_terms = list(dict.fromkeys(terms))  # Сохраняет порядок
        if len(unique_terms) < len(terms):
            logger.warning(
                f"multi_term_exact_search: удалены дубликаты терминов. "
                f"Было: {len(terms)}, стало: {len(unique_terms)}"
            )
        
        filter_info = []
        if chunk_type:
            filter_info.append(f"chunk_type={chunk_type!r}")
        if source:
            filter_info.append(f"file={source}")
        if section:
            filter_info.append(f"section~{section}")
        filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""
        
        logger.debug(f"Tool multi_term_exact_search: terms={unique_terms}{filter_str}")
        rec = _db_request(
            "DB:multi_term_exact_search",
            f"terms={unique_terms}\nlimit={limit}\nchunk_type={chunk_type!r}{filter_str}",
        )
        
        # Поиск с уникальными терминами
        scored: list[tuple] = vectorstore.clone().multi_term_exact_search(
            terms=unique_terms, limit=limit, chunk_type=chunk_type, source=source, section=section
        )

        # Группировка по coverage с дедупликацией
        from collections import defaultdict as _dd
        groups: dict[int, list[ChunkResult]] = _dd(list)
        for doc, cnt in scored:
            chunk = _doc_to_chunk_result(doc)
            groups[cnt].append(chunk)

        # Дедупликация каждой группы
        for cnt in groups:
            groups[cnt] = _deduplicate_chunks(groups[cnt])

        # Конвертация в структурированный результат
        total_after_dedup = sum(len(chunks) for chunks in groups.values())
        result = MultiTermSearchResult(
            terms=unique_terms,  # Возвращаем уникальные термины
            chunks_by_coverage=dict(groups),  # convert defaultdict to dict
            total_chunks=total_after_dedup,
            max_coverage=max(groups.keys()) if groups else 0
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"multi_term_exact_search {unique_terms}{filter_str}: {result.total_chunks} чанков  "
            f"(max coverage {result.max_coverage}/{len(unique_terms)})"
        )
        
        return result


    @tool(args_schema=FindSectionsByTermInput)
    def find_sections_by_term(
        substring: str,
        limit: int = 100,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None
    ) ->SearchSectionsResult:
        """
        Найти все уникальные разделы (source + section), содержащие точные совпадения подстроки.

        Лучше всего для: выяснения, в каких разделах встречаются конкретные термины, перед углублением.
        Возвращает список пар (source, section) со счётчиками совпадений, отсортированный по релевантности.
        Используйте, чтобы выявить наиболее релевантные разделы, затем берите детали через exact_search
        или get_section_content.

        Сканирует до 'limit' чанков и возвращает ВСЕ уникальные разделы, найденные в пределах
        этого сканирования. Например, при limit=100 и поиске "PostgreSQL" вы получите все разделы,
        упоминающие PostgreSQL, в пределах первых 100 совпавших чанков.

        Необязательные фильтры:
        - source: ограничить поиск конкретным файлом
        - chunk_type: фильтр по типу чанка

        Возвращает:
            SearchSectionsResult: подстрока как запрос, список разделов со счётчиками, total_found
        """
        filter_info = []
        if source:
            filter_info.append(f"file={source}")
        filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""
        
        logger.debug(
            f"Tool find_sections_by_term: substring='{substring}'{filter_str}, "
            f"limit={limit}, chunk_type={chunk_type!r}"
        )
        rec = _db_request(
            "DB:find_sections_by_term",
            f"substring={substring!r}\nlimit={limit}\nchunk_type={chunk_type!r}{filter_str}",
        )
        
        # Поиск разделов
        sections = vectorstore.clone().exact_search_sections(
            substring, limit=limit, chunk_type=chunk_type, source=source
        )
        
        # Конвертация в структурированный результат
        section_infos = [
            SectionInfo(
                source=src,
                section=sec,
                match_count=cnt,
                match_type="CONTENT"
            )
            for src, sec, cnt in sections
        ]
        
        result = SearchSectionsResult(
            query=substring,
            sections=section_infos,
            total_found=len(section_infos),
            returned_count=len(section_infos)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"find_sections_by_term '{substring}'{filter_str}: "
            f"{result.total_found} разделов"
        )
        
        return result


    @tool(args_schema=FindRelevantSectionsInput)
    def find_relevant_sections(
        query: str,
        exact_terms: list[str] = None,
        limit: int = 50,
        source: Optional[str] = None
    ) -> SearchSectionsResult:
        """
        Двухэтапная стратегия поиска релевантных разделов:

        ЭТАП 1: поиск по НАЗВАНИЮ раздела — находит разделы, чей заголовок совпадает с запросом.
        ЭТАП 2: поиск по СОДЕРЖИМОМУ — находит разделы, содержащие точные термины.

        Результаты объединяются и приоритизируются:
        - разделы, совпавшие по НАЗВАНИЮ, помечаются match_type="NAME";
        - разделы, совпавшие по СОДЕРЖИМОМУ, — match_type="CONTENT";
        - дубликаты объединяются с общими метаданными.

        Лучше всего для: всестороннего поиска разделов по заголовку и содержимому одновременно.
        Используйте как ОСНОВНОЙ инструмент, когда нужно найти все релевантные разделы.

        Аргументы:
            query: фраза запроса пользователя (ищется в названиях разделов)
            exact_terms: список точных терминов для поиска в содержимом (необязательно)
            limit: максимальное число возвращаемых разделов (по умолчанию 50)
            source: необязательный фильтр по имени файла

        Возвращает:
            SearchSectionsResult: запрос, топ-N разделов с приоритетом NAME > CONTENT, счётчики
        """
        if exact_terms is None:
            exact_terms = []
        
        filter_info = []
        if source:
            filter_info.append(f"file={source}")
        filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""
        
        logger.debug(
            f"Tool find_relevant_sections: query='{query}'{filter_str}, "
            f"exact_terms={exact_terms}, limit={limit}"
        )
        rec = _db_request(
            "DB:find_relevant_sections",
            f"query={query!r}\nexact_terms={exact_terms}\nlimit={limit}{filter_str}",
        )
        
        # STAGE 1: Search by section NAME
        name_sections = vectorstore.clone().find_sections_by_name(query, source=source)
        
        # STAGE 2: Search by CONTENT (exact terms)
        content_sections: list[tuple[str, str, int]] = []
        if exact_terms:
            for term in exact_terms:
                term_sections = vectorstore.clone().exact_search_sections(
                    term, limit=500, chunk_type=None, source=source
                )
                content_sections.extend(term_sections)
        
        # Merge results: (source, section) -> metadata
        from collections import defaultdict as _dd
        section_map: dict[tuple[str, str], dict] = {}
        
        # Add name matches
        for src, sec, chunk_count in name_sections:
            key = (src, sec)
            section_map[key] = {
                "match_type": "NAME",
                "chunk_count": chunk_count,
                "term_counts": {}
            }
        
        # Add/merge content matches
        for src, sec, match_count in content_sections:
            key = (src, sec)
            if key in section_map:
                # Already found by name - add term info
                section_map[key]["term_counts"][src] = section_map[key]["term_counts"].get(src, 0) + match_count
            else:
                # New section from content search
                section_map[key] = {
                    "match_type": "CONTENT",
                    "chunk_count": 0,
                    "term_counts": {src: match_count}
                }
        
        # Sort all sections by priority: NAME first, then by term count
        all_sections = []
        for (src, sec), meta in section_map.items():
            priority = 0 if meta["match_type"] == "NAME" else 1
            term_count = sum(meta["term_counts"].values())
            match_count = meta["chunk_count"] if meta["match_type"] == "NAME" else term_count
            all_sections.append((priority, -term_count, src, sec, meta["match_type"], match_count))
        
        all_sections.sort()
        
        # Apply LIMIT - take only top N sections
        top_sections = all_sections[:limit]
        
        # Конвертация в структурированный результат
        section_infos = [
            SectionInfo(
                source=src,
                section=sec,
                match_count=match_count,
                match_type=match_type
            )
            for priority, neg_count, src, sec, match_type, match_count in top_sections
        ]
        
        result = SearchSectionsResult(
            query=query,
            sections=section_infos,
            total_found=len(section_map),
            returned_count=len(section_infos)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"find_relevant_sections '{query}'{filter_str}: "
            f"найдено {result.total_found} разделов, возвращено топ-{result.returned_count} "
            f"({len(name_sections)} по названию, "
            f"{len(set((s, sec) for s, sec, _ in content_sections))} по содержимому)"
        )
        
        return result


    @tool(args_schema=RegexSearchInput)
    def regex_search(pattern: str, max_results: int = regex_max_results) -> RegexSearchResult:
        """
        Поиск по regex-паттерну напрямую в исходных .md-файлах с выводом окружающих строк контекста.
        Лучше всего для: IP-адресов, номеров портов, VLAN ID, кодов документов, масок подсетей,
        любых структурированных паттернов.
        Каждое совпадение содержит имя файла, номер строки, найденный текст и строки контекста вокруг.

        Возвращает:
            RegexSearchResult: паттерн, список совпадений с контекстом и количество total_matches
        """
        logger.debug(f"Tool regex_search: pattern='{pattern}'")
        rec = _db_request("DB:regex_search", f"pattern={pattern!r}\nmax_results={max_results}")

        # Поиск (возвращает объект с matches и total_matches)
        search_result = _kb_regex_search(pattern, knowledge_dir)

        # Конвертация в структурированный результат
        # Предполагаем что _kb_regex_search возвращает объект с полями file, line_number, match, context
        regex_matches = [
            RegexMatch(
                file=m.file,
                line_number=m.line_number,
                matched_text=m.match,
                context_before=[],  # Парсим context если нужно
                matched_line=m.match,
                context_after=[]
            )
            for m in search_result.matches[:max_results]
        ]

        result = RegexSearchResult(
            pattern=pattern,
            matches=regex_matches,
            total_matches=search_result.total_matches
        )

        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(f"regex_search '{pattern}': {result.total_matches} совпадений")

        return result

    def _build_abbreviation_pattern(abbreviation: str) -> str:
        r"""
        Создает regex паттерн для поиска расшифровки аббревиатуры.

        Логика:
        - Каждая буква аббревиатуры -> начало слова + буквы того же алфавита
        - Цифры -> точное совпадение (как есть)
        - Между элементами могут быть пробелы и знаки препинания
        - Поддержка русских (кириллица) и английских букв + цифры

        Примеры:
            КЦОИ -> К[а-яё]*\s+Ц[а-яё]*\s+О[а-яё]*\s+И[а-яё]*
            RAM  -> R[a-z]*\s+A[a-z]*\s+M[a-z]*
            API  -> A[a-z]*\s+P[a-z]*\s+I[a-z]*
            AK47 -> A[a-z]*\s+K[a-z]*\s+47
            T34  -> T[a-z]*\s+34
        """
        if not abbreviation:
            raise ValueError("Abbreviation cannot be empty")

        # Строим паттерн: обрабатываем каждый символ
        parts = []
        i = 0
        while i < len(abbreviation):
            char = abbreviation[i]
            
            # Проверяем, это цифра?
            if char.isdigit():
                # Собираем все подряд идущие цифры
                digit_sequence = char
                i += 1
                while i < len(abbreviation) and abbreviation[i].isdigit():
                    digit_sequence += abbreviation[i]
                    i += 1
                # Цифры идут как есть - точное совпадение
                parts.append(digit_sequence)
            elif char.isalpha():
                # Определяем алфавит для этой буквы
                upper_char = char.upper()
                if 'А' <= upper_char <= 'Я' or upper_char == 'Ё':
                    # Кириллица
                    letter_class = r'[а-яё]'
                elif 'A' <= upper_char <= 'Z':
                    # Латиница
                    letter_class = r'[a-z]'
                else:
                    raise ValueError(f"Unsupported character in abbreviation: {char}")
                
                # Буква + буквы того же алфавита
                parts.append(f"{upper_char}{letter_class}*")
                i += 1
            else:
                # Неподдерживаемый символ
                raise ValueError(f"Unsupported character in abbreviation: {char}")

        # Соединяем через \s+ (один или более пробелов)
        pattern = r'\s+'.join(parts)

        logger.debug(f"Generated pattern for '{abbreviation}': {pattern}")
        return pattern

    @tool(args_schema=FindAbbreviationExpansionInput)
    def find_abbreviation_expansion(
        abbreviation: str,
        max_results: int = regex_max_results
    ) -> AbbreviationExpansionResult:
        """
        Найти расшифровки аббревиатур в базе знаний.

        Ищет последовательности слов, где каждое слово начинается с соответствующей буквы
        аббревиатуры. Работает и с кириллическими, и с латинскими аббревиатурами.
        Поддерживает цифры в аббревиатурах (например, AK47, T34).

        Лучше всего для: поиска полных названий сокращений (RAM, API, AK47 и т.п.).

        Примеры:
            КЦОИ -> находит расшифровки с чанками
            RAM  -> находит ["Random Access Memory"] с чанками
            API  -> находит ["Application Programming Interface"] с чанками
            AK47 -> находит ["Автомат Калашникова 47"] с чанками
            T34  -> находит ["Танк 34"] с чанками

        Инструмент автоматически строит regex-паттерн:
            - буквы -> слова, начинающиеся с этих букв (без учёта регистра);
            - цифры -> точное совпадение (как есть).

        Возвращает:
            AbbreviationExpansionResult: аббревиатура, список расшифровок с чанками, общее число и паттерн
        """
        logger.debug(f"Tool find_abbreviation_expansion: abbreviation='{abbreviation}'")

        try:
            # Генерируем regex паттерн
            pattern = _build_abbreviation_pattern(abbreviation)
        except ValueError as e:
            logger.error(f"Invalid abbreviation '{abbreviation}': {e}")
            # Возвращаем пустой результат
            return AbbreviationExpansionResult(
                abbreviation=abbreviation,
                expansions=[],
                total_found=0,
                pattern_used=""
            )

        rec = _db_request(
            "DB:find_abbreviation_expansion",
            f"abbreviation={abbreviation!r}\ngenerated_pattern={pattern!r}\nmax_results={max_results}"
        )

        # Используем существующий regex_search из rag_chat
        search_result = _kb_regex_search(pattern, knowledge_dir)

        # Словарь: expansion -> список (file, line_number)
        expansions_map = {}
        for match in search_result.matches[:max_results * 3]:  # Берем больше для дедупликации
            # Очищаем текст от лишних пробелов и нормализуем
            expansion = " ".join(match.match.split())
            if expansion:
                if expansion not in expansions_map:
                    expansions_map[expansion] = []
                expansions_map[expansion].append((match.file, match.line_number))

        # Для каждой уникальной расшифровки находим чанк в vectorstore
        expansion_items = []
        for expansion, locations in sorted(expansions_map.items())[:max_results]:
            # Берем первое местоположение
            source_file, line_num = locations[0]
            
            # Ищем чанк в vectorstore по точному совпадению расшифровки
            # Используем exact_search с фильтром по файлу
            docs = vectorstore.clone().exact_search(
                expansion,
                limit=5,  # Берем несколько, выберем наиболее подходящий
                chunk_type="",  # Только prose chunks
                source=source_file,
                section=None
            )
            
            # Ищем чанк который содержит нужную строку или ближайший по line_start
            best_doc = None
            min_distance = float('inf')
            
            for doc in docs:
                # Проверяем, что чанк содержит искомую расшифровку
                if expansion.lower() in doc.page_content.lower():
                    line_start = doc.metadata.get('line_start', 0)
                    distance = abs(line_start - line_num)
                    if distance < min_distance:
                        min_distance = distance
                        best_doc = doc
            
            # Если не нашли точного совпадения, берем первый
            if best_doc is None and docs:
                best_doc = docs[0]
            
            if best_doc:
                # Конвертируем в ChunkResult
                chunk = ChunkResult(
                    content=best_doc.page_content,
                    metadata=ChunkMetadata(
                        chunk_id=best_doc.metadata.get('chunk_id', ''),
                        source=best_doc.metadata.get('source', ''),
                        section=best_doc.metadata.get('section', ''),
                        chunk_type=best_doc.metadata.get('chunk_type', ''),
                        line_start=best_doc.metadata.get('line_start', 0),
                        line_end=best_doc.metadata.get('line_end', 0),
                        chunk_index=best_doc.metadata.get('chunk_index', 0),
                        table_headers=best_doc.metadata.get('table_headers')
                    ),
                    score=None
                )
                
                expansion_items.append(AbbreviationExpansionItem(
                    expansion=expansion,
                    chunk=chunk
                ))

        result = AbbreviationExpansionResult(
            abbreviation=abbreviation,
            expansions=expansion_items,
            total_found=len(expansion_items),
            pattern_used=pattern
        )

        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"find_abbreviation_expansion '{abbreviation}': {result.total_found} уникальных расшифровок с чанками"
        )

        return result

    @tool(args_schema=ReadTableInput)
    def read_table(section: str, source_file: Optional[str] = None, limit: int = 50) -> TableResult:
        """
        Прочитать строки таблицы из конкретного раздела базы знаний.
        Возвращает структурированные строки таблицы со столбцами в виде словаря для удобной обработки.
        Лучше всего для: структурированных данных — таблиц IP, списков серверов, назначений VLAN,
        версий ПО.
        При необходимости сначала используйте list_sections, чтобы узнать точное название раздела.
        Если задан source_file, поиск идёт только по этому файлу.

        Возвращает:
            TableResult: section_query, список строк со столбцами и количество total_rows
        """
        logger.debug(f"Tool read_table: section='{section}', source_file={source_file!r}")
        rec = _db_request(
            "DB:read_table",
            f"section={section!r}\nsource_file={source_file!r}\nlimit={limit}",
        )
        
        # Поиск табличных чанков
        docs = _query_table_chunks(vectorstore, section, source_file, limit)
        
        # Конвертация в структурированный результат
        rows = [_doc_to_table_row(doc) for doc in docs]
        result = TableResult(
            section_query=section,
            rows=rows,
            total_rows=len(rows)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(f"read_table '{section}': {result.total_rows} записей")
        
        return result

    @tool(args_schema=GetSectionContentInput)
    def get_section_content(source_file: str, section: str) -> SectionContent:
        """
        Прочитать полный текст конкретного раздела.
        Лучше всего для: чтения целых разделов, которые могут быть разбиты на множество чанков,
        полных таблиц, нумерованных списков, блоков кода, которые нужно видеть в полном контексте.
        Возвращает весь текст раздела, включая подразделы.
        Сначала используйте list_sections, чтобы узнать точные имена раздела и файла.

        Returns:
            SectionContent with source, section name, line numbers, and full content
        """
        logger.debug(f"Tool get_section_content: [{source_file}] '{section}'")
        rec = _db_request("DB:get_section_content", f"source_file={source_file!r}\nsection={section!r}")

        client = vectorstore.clone()._client
        db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
        # table_row excluded: each parsed table is already present as table_full
        sql = f"""
            SELECT content, line_start, line_end
            FROM {db}.{tbl} FINAL
            WHERE source = {{src:String}}
              AND section = {{sec:String}}
              AND chunk_type != 'table_row'
            ORDER BY line_start, chunk_index
        """
        query_result = client.query(sql, parameters={"src": source_file, "sec": section})

        if not query_result.result_rows:
            rows = _query_sections(vectorstore, source_file)
            similar = [s for _, s in rows if section.lower() in s.lower()][:5]
            hint = (
                f"Похожие разделы: {'; '.join(similar)}" if similar
                else "Используйте list_sections для поиска разделов."
            )
            error_msg = f"Раздел '{section}' не найден в файле '{source_file}'. {hint}"
            result = SectionContent(
                source=source_file,
                section=section,
                line_start=0,
                line_end=0,
                content=error_msg
            )
            if rec:
                rec.set_response(error_msg)
            logger.warning(error_msg)
            return result

        line_start = query_result.result_rows[0][1]
        line_end = query_result.result_rows[-1][2]
        content = "\n\n".join(row[0] for row in query_result.result_rows if row[0].strip())

        result = SectionContent(
            source=source_file,
            section=section,
            line_start=line_start,
            line_end=line_end,
            content=content
        )

        if rec:
            rec.set_response(f"{len(content)} символов\n\n{result.model_dump_json(indent=2)}")
        logger.info(f"get_section_content: [{source_file}] '{section}' — {len(content)} символов")

        return result

    @tool(args_schema=ListSectionsInput)
    def list_sections(source_file: Optional[str] = None) -> SectionsTree:
        """
        Список всех разделов (breadcrumb-пути H1 > H2 > ...) в базе знаний.
        Лучше всего для: обзора доступного содержимого, выяснения точного названия раздела перед
        использованием get_section_content или read_table, понимания структуры документов.
        Необязательно фильтровать по source_file, чтобы увидеть разделы только одного документа.
        Сначала используйте list_sources, чтобы узнать имена файлов.

        Возвращает:
            SectionsTree: необязательный фильтр по источнику, список разделов со счётчиками чанков и общее число
        """
        logger.debug(f"Tool list_sections: source_file={source_file!r}")
        rec = _db_request("DB:list_sections", f"source_file={source_file!r}")
        
        # Запрос разделов
        rows = _query_sections(vectorstore, source_file)
        
        # Подсчёт чанков для каждого раздела
        # rows содержит (source, section) пары, нужно получить count
        # Сделаем дополнительный запрос для подсчёта чанков
        client = vectorstore.clone()._client
        db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
        
        # Получаем count для каждой пары (source, section)
        section_counts = {}
        for src, sec in rows:
            if not sec:
                continue
            key = (src, sec)
            if key not in section_counts:
                sql = f"""
                    SELECT count() AS cnt
                    FROM {db}.{tbl} FINAL
                    WHERE source = {{src:String}} AND section = {{sec:String}}
                """
                result_query = client.query(sql, parameters={"src": src, "sec": sec})
                if result_query.result_rows:
                    section_counts[key] = result_query.result_rows[0][0]
                else:
                    section_counts[key] = 0
        
        # Конвертация в структурированный результат
        section_nodes = [
            SectionTreeNode(
                section=sec,
                chunks_count=section_counts.get((src, sec), 0)
            )
            for src, sec in rows if sec
        ]
        
        result = SectionsTree(
            source=source_file,
            sections=section_nodes,
            total_sections=len(section_nodes)
        )
        
        # Логирование
        if rec:
            rec.set_response(f"Итого разделов: {result.total_sections}\n\n{result.model_dump_json(indent=2)}")
        logger.info(f"list_sections: {result.total_sections} разделов")
        
        return result

    @tool(args_schema=GetNeighborChunksInput)
    def get_neighbor_chunks(
        source: str, 
        line_start: int, 
        before: int = 5, 
        after: int = 5,
        include_anchor: bool = True
    ) -> NeighborChunksResult:
        """
        Получить соседние чанки вокруг конкретного чанка в том же исходном файле.
        Лучше всего для: расширения контекста, когда найденный чанк неполон или обрезан.
        Значения 'source' и 'line_start' берутся из метаданных предыдущих результатов поиска.
        Возвращает до 'before' чанков перед и 'after' чанков после позиции якоря.

        Аргументы:
            source: имя исходного файла
            line_start: позиция (строка) якорного чанка
            before: сколько чанков взять перед якорем
            after: сколько чанков взять после якоря
            include_anchor: если True (по умолчанию), вернуть и сам якорный чанк

        Возвращает:
            NeighborChunksResult: anchor_chunk (необязательно), список chunks_before и список chunks_after
        """
        logger.debug(
            f"Tool get_neighbor_chunks: [{source}] line {line_start}, "
            f"before={before}, after={after}, include_anchor={include_anchor}"
        )
        rec = _db_request(
            "DB:get_neighbor_chunks",
            f"source={source!r}\nline_start={line_start}\nbefore={before}\nafter={after}\ninclude_anchor={include_anchor}",
        )
        
        # Получение соседних чанков (возвращается в порядке: prev + next, БЕЗ якоря)
        vs_clone = vectorstore.clone()
        docs = vs_clone.get_neighbor_chunks(source, line_start, before=before, after=after)
        
        # Получение якорного чанка (если нужно)
        anchor_chunk = None
        if include_anchor:
            # Запрашиваем якорный чанк отдельно
            anchor_docs = vs_clone.exact_search(
                substring="",  # пустая подстрока найдет все чанки
                limit=1,
                chunk_type=None,
                source=source,
                section=None
            )
            # Фильтруем по line_start
            for doc in anchor_docs:
                # Нужен более точный запрос - используем прямой SQL
                pass
            
            # Альтернативный подход: используем similarity_search с фильтром
            # Но лучше сделать прямой SQL запрос
            query = f"""
                SELECT source, section, chunk_type, table_headers, content,
                       line_start, line_end, chunk_index
                FROM {vs_clone._cfg.database}.{vs_clone._cfg.table} FINAL
                WHERE source = %(src)s
                  AND line_start = %(ls)s
                LIMIT 1
            """
            result = vs_clone._client.query(
                query,
                parameters={"src": source, "ls": line_start}
            )
            if result.result_rows:
                row = result.result_rows[0]
                src, sec, ct, th, content, ls, le, ci = row
                meta = {
                    "source": src,
                    "section": sec,
                    "chunk_type": ct,
                    "line_start": int(ls),
                    "line_end": int(le),
                    "chunk_index": int(ci),
                }
                if th:
                    meta["table_headers"] = th
                anchor_doc = Document(page_content=content, metadata=meta)
                anchor_chunk = _doc_to_chunk_result(anchor_doc)
        
        # Разделение на before и after
        chunks_before = []
        chunks_after = []
        for doc in docs:
            if doc.metadata['line_start'] < line_start:
                chunks_before.append(_doc_to_chunk_result(doc))
            elif doc.metadata['line_start'] > line_start:
                chunks_after.append(_doc_to_chunk_result(doc))
        
        # Конвертация в структурированный результат
        result = NeighborChunksResult(
            anchor_line=line_start,
            anchor_chunk=anchor_chunk,
            chunks_before=chunks_before,
            chunks_after=chunks_after
        )
        
        # Логирование
        total_chunks = len(chunks_before) + len(chunks_after)
        if anchor_chunk:
            total_chunks += 1
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"get_neighbor_chunks: {total_chunks} чанков вокруг [{source}] line {line_start} "
            f"(якорь: {'включен' if anchor_chunk else 'не включен'})"
        )
        
        return result

    @tool(args_schema=GetChunksByIndexInput)
    def get_chunks_by_index(
        source: str,
        section: str,
        chunk_indices: list[int]
    ) -> SearchChunksResult:
        """
        Получить конкретные чанки по их индексам из заданного исходного файла и раздела.
        Лучше всего для: извлечения конкретных чанков, когда известны точные индексы из предыдущих результатов.
        Полезно для подгрузки упомянутых чанков или сборки контекста из известных позиций.

        Аргументы:
            source: имя исходного файла (например, 'servers.md')
            section: название раздела или breadcrumb-путь
            chunk_indices: список индексов чанков (например, [0, 1, 5])

        Возвращает:
            SearchChunksResult с запрошенными чанками
        """
        logger.debug(
            f"Tool get_chunks_by_index: [{source}] section='{section}', "
            f"indices={chunk_indices}"
        )
        rec = _db_request(
            "DB:get_chunks_by_index",
            f"source={source!r}\nsection={section!r}\nchunk_indices={chunk_indices!r}",
        )
        
        # Прямой SQL запрос для получения чанков по индексам
        vs_clone = vectorstore.clone()
        db, tbl = vs_clone._cfg.database, vs_clone._cfg.table
        placeholders = ','.join(['%s'] * len(chunk_indices))
        query = f"""
            SELECT content, source, section, chunk_type, table_headers,
                   line_start, line_end, chunk_index
            FROM {db}.{tbl} FINAL
            WHERE source = %s 
              AND section = %s
              AND chunk_index IN ({placeholders})
            ORDER BY chunk_index
        """
        
        params = [source, section] + list(chunk_indices)
        result = vs_clone._client.query(query, params)
        
        # Конвертация результатов в Document и затем в ChunkResult
        docs = []
        for row in result.result_rows:
            content, src, sec, ct, th, ls, le, ci = row
            meta = {
                'source': src,
                'section': sec,
                'chunk_type': ct,
                'line_start': int(ls),
                'line_end': int(le),
                'chunk_index': int(ci),
            }
            if th:
                meta['table_headers'] = th
            docs.append(Document(page_content=content, metadata=meta))
        
        # Конвертация в ChunkResult
        chunks = _docs_to_chunk_results(docs)
        
        # Результат
        result = SearchChunksResult(
            query=f"chunks by index in [{source}] {section}",
            chunks=chunks,
            total_found=len(chunks)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(
            f"get_chunks_by_index: {len(chunks)} чанков из [{source}] {section}, "
            f"indices={chunk_indices}"
        )
        
        return result

    @tool
    def list_sources() -> SourcesList:
        """
        Список всех исходных документов в базе знаний с числом их чанков.
        Лучше всего для: обзора доступных файлов, выбора нужного документа для запроса,
        понимания общего охвата базы знаний.
        Всегда начинайте отсюда, если не знаете, в каких файлах находится нужная информация.

        Возвращает:
            SourcesList: список источников, общее число источников и общее число чанков
        """
        logger.debug("Tool list_sources")
        rec = _db_request("DB:list_sources", "GROUP BY source ORDER BY source")
        db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
        sql = (
            f"SELECT source, count() AS cnt "
            f"FROM {db}.{tbl} FINAL "
            f"GROUP BY source ORDER BY source"
        )
        query_result = vectorstore.clone()._client.query(sql)
        rows = query_result.result_rows
        
        # Конвертация в структурированный результат
        source_infos = [
            SourceInfo(
                source=src,
                chunks_count=cnt
            )
            for src, cnt in rows
        ]
        
        result = SourcesList(
            sources=source_infos,
            total_sources=len(source_infos),
            total_chunks=sum(info.chunks_count for info in source_infos)
        )
        
        # Логирование
        if rec:
            rec.set_response(result.model_dump_json(indent=2))
        logger.info(f"list_sources: {result.total_sources} источников")
        
        return result

    @tool
    def list_all_sections() -> SearchSectionsResult:
        """
        Список всех уникальных пар (source, section_name) из базы знаний.
        Название раздела берётся как последний подраздел (часть после последнего ' > ').
        Лучше всего для: получения полного списка доступных разделов перед планированием,
        чтобы не делать предположений о несуществующих разделах документации.
        Стоит вызывать в начале обработки запроса, чтобы понять доступное содержимое.

        Возвращает:
            SearchSectionsResult: query="", список всех уникальных разделов и итоги
        """
        logger.debug("Tool list_all_sections")
        rec = _db_request("DB:list_all_sections", "DISTINCT source, section -> parse last subsection")

        # Получение всех разделов
        rows = _query_sections(vectorstore, source_file=None)

        # Извлекаем последнюю часть после " > " из section
        unique_pairs: set[tuple[str, str]] = set()
        for src, sec in rows:
            if sec:
                # Берём последнюю часть после " > "
                last_section = sec.split(" > ")[-1].strip()
                unique_pairs.add((src, last_section))

        # Конвертация в структурированный результат
        section_infos = [
            SectionInfo(
                source=src,
                section=last_sec,
                match_count=1,  # каждая уникальная пара встречается один раз в списке
                match_type=None
            )
            for src, last_sec in sorted(unique_pairs)
        ]

        result = SearchSectionsResult(
            query="",  # list_all_sections не имеет query
            sections=section_infos,
            total_found=len(section_infos),
            returned_count=len(section_infos)
        )

        # Логирование
        if rec:
            rec.set_response(f"Итого уникальных пар: {result.total_found}\n\n{result.model_dump_json(indent=2)}")
        logger.info(f"list_all_sections: {result.total_found} уникальных пар")

        return result

    return [
        semantic_search,
        exact_search,
        exact_search_in_file,
        exact_search_in_file_section,
        multi_term_exact_search,
        find_sections_by_term,
        find_relevant_sections,
        regex_search,
        find_abbreviation_expansion,
        read_table,
        get_section_content,
        list_sections,
        get_neighbor_chunks,
        get_chunks_by_index,
        list_sources,
        list_all_sections,
    ]


def get_tool_registry() -> dict[str, str]:
    """
    Возвращает реестр инструментов с описаниями для использования в промптах.
    
    Returns:
        Словарь {tool_name: description}
    """
    return {
        "semantic_search": "Семантический поиск по эмбеддингам (концептуальные вопросы)",
        "exact_search": "Точный поиск по подстроке (термины, названия, коды)",
        "exact_search_in_file": "Точный поиск в конкретном файле",
        "exact_search_in_file_section": "Точный поиск в конкретном разделе файла",
        "multi_term_exact_search": "Поиск по нескольким терминам с ранжированием (автоудаление дубликатов)",
        "find_sections_by_term": "Поиск разделов содержащих термин (возвращает список source+section)",
        "find_relevant_sections": "Двухэтапный поиск: по названию раздела + по терминам в содержимом",
        "regex_search": "Поиск по regex-паттернам (IP, порты, VLAN)",
        "find_abbreviation_expansion": "Поиск расшифровки аббревиатур (КЦОИ, RAM, API)",
        "read_table": "Чтение строк таблицы по названию раздела",
        "get_section_content": "Полный текст раздела (сборка из чанков ClickHouse)",
        "list_sections": "Список разделов документации",
        "get_neighbor_chunks": "Соседние чанки вокруг найденного фрагмента",
        "get_chunks_by_index": "Получить конкретные чанки по индексам (source, section, chunk_indices[])",
        "list_sources": "Список файлов в базе знаний",
        "list_all_sections": "Все уникальные пары (source, section)",
    }


# ---------------------------------------------------------------------------
# CLI Interface
# ---------------------------------------------------------------------------

def _cli_list_tools(tools: list[BaseTool]) -> None:
    """Выводит краткий список инструментов компактно"""
    
    # Собираем данные
    rows = []
    for tool in tools:
        # Получаем имена параметров
        param_names = []
        if hasattr(tool, 'args_schema') and tool.args_schema:
            schema = tool.args_schema
            if hasattr(schema, 'model_fields'):
                for field_name, field_info in schema.model_fields.items():
                    required = field_info.is_required()
                    if required:
                        param_names.append(field_name)
                    else:
                        param_names.append(f"[{field_name}]")
        
        params_str = ', '.join(param_names) if param_names else '-'
        rows.append((tool.name, params_str))
    
    if not rows:
        print("Нет доступных инструментов")
        return
    
    # Вычисляем максимальную ширину названия для выравнивания
    max_name_len = max(len(row[0]) for row in rows)
    
    # Выводим список
    for name, params in rows:
        print(f"{name:<{max_name_len}}  {params}")
    
    print(f"\nВсего: {len(rows)} | Детали: python kb_tools.py help <инструмент>")


def _cli_help_tool(tools: list[BaseTool], tool_name: str) -> None:
    """Выводит детальную справку по инструменту"""
    # Находим инструмент
    tool = next((t for t in tools if t.name == tool_name), None)
    if not tool:
        print(f"Ошибка: инструмент '{tool_name}' не найден", file=__import__('sys').stderr)
        print(f"Доступные инструменты: {', '.join(t.name for t in tools)}", file=__import__('sys').stderr)
        __import__('sys').exit(1)
    
    print("=" * 80)
    print(f"Инструмент: {tool.name}")
    print("=" * 80)
    print()
    print("Описание:")
    print(f"  {tool.description}")
    print()
    
    # Детальная информация о параметрах
    if hasattr(tool, 'args_schema') and tool.args_schema:
        schema = tool.args_schema
        if hasattr(schema, 'model_fields'):
            print("Параметры:")
            print()
            for field_name, field_info in schema.model_fields.items():
                field_type = field_info.annotation
                # Упрощенное отображение типа
                type_str = str(field_type).replace("typing.", "").replace("<class '", "").replace("'>", "")
                required = field_info.is_required()
                default = field_info.default if not required else None
                
                # Статус обязательности
                if required:
                    req_str = "✓ обязательный"
                else:
                    req_str = f"○ опциональный (default={default})"
                
                print(f"  {field_name}={type_str}")
                print(f"    {req_str}")
                if field_info.description:
                    # Форматируем описание с отступом
                    desc_lines = field_info.description.split('\n')
                    for line in desc_lines:
                        print(f"    {line}")
                print()
    
    print("=" * 80)
    print("Пример использования:")
    print(f"  python kb_tools.py run {tool_name} param=value ...")
    print("=" * 80)


def _cli_run_tool(tools: list[BaseTool], tool_name: str, args: dict) -> None:
    """Запускает инструмент с заданными аргументами"""
    # Находим инструмент
    tool = next((t for t in tools if t.name == tool_name), None)
    if not tool:
        print(f"Ошибка: инструмент '{tool_name}' не найден", file=__import__('sys').stderr)
        print(f"Доступные инструменты: {', '.join(t.name for t in tools)}", file=__import__('sys').stderr)
        __import__('sys').exit(1)
    
    try:
        # Вызываем инструмент
        result = tool.invoke(args)

        # Выводим результат в JSON
        if hasattr(result, 'model_dump'):
            print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
        elif hasattr(result, 'dict'):
            print(json.dumps(result.dict(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    except Exception as e:
        import sys
        from pydantic import ValidationError
        if isinstance(e, ValidationError):
            print(f"Ошибка параметров: {e}\n", file=sys.stderr)
            _cli_help_tool(tools, tool_name)
        else:
            print(f"Ошибка выполнения: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)


def main():
    """CLI интерфейс для kb_tools"""
    import argparse
    import sys
    from pathlib import Path
    
    parser = argparse.ArgumentParser(
        description="KB Tools CLI - инструменты для работы с базой знаний",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Команды:

  list                        Список всех инструментов с именами параметров
  run <tool> param=value...   Запустить инструмент с параметрами
  help [tool]                 Общий help или детальная справка по инструменту

Примеры:

  # Показать список инструментов
  python kb_tools.py list

  # Детальная справка по инструменту
  python kb_tools.py help exact_search

  # Вызвать инструмент
  python kb_tools.py run exact_search substring="КЦОИ" limit=10
  python kb_tools.py run semantic_search query="что такое RAG" top_k=5
  python kb_tools.py run find_abbreviation_expansion abbreviation="AK47"

Формат параметров: param=value (числа автоматически конвертируются)
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Команда')
    
    # Команда list
    list_parser = subparsers.add_parser('list', help='Список инструментов с именами параметров')
    
    # Команда help
    help_parser = subparsers.add_parser('help', help='Справка по инструменту')
    help_parser.add_argument('tool_name', nargs='?', help='Название инструмента (если не указан - общий help)')

    # Команда run
    run_parser = subparsers.add_parser('run', help='Запустить инструмент')
    run_parser.add_argument('tool_name', help='Название инструмента')
    run_parser.add_argument('params', nargs='*', help='Параметры в формате param=value')
    
    # Парсим аргументы
    if len(sys.argv) == 1:
        # Без аргументов - показываем help
        parser.print_help()
        sys.exit(0)
    
    args = parser.parse_args()
    
    # Если команда help без tool_name - показываем общий help
    if args.command == 'help' and (not hasattr(args, 'tool_name') or args.tool_name is None):
        parser.print_help()
        sys.exit(0)

    def _parse_params(raw_params: list[str]) -> dict:
        tool_args: dict = {}
        for param in raw_params:
            if '=' not in param:
                print(f"Предупреждение: пропущен параметр '{param}' (ожидается формат param=value)", file=sys.stderr)
                continue
            key, value = param.split('=', 1)
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    if isinstance(value, str) and (value.startswith('[') or value.startswith('{')):
                        try:
                            value = json.loads(value)
                        except Exception:
                            pass
            tool_args[key] = value
        return tool_args

    def _init_tools() -> list[BaseTool]:
        from rag_chat import build_vectorstore, settings
        print("Инициализация vectorstore...", file=sys.stderr)
        vectorstore = build_vectorstore()
        knowledge_dir = Path(settings.knowledge_dir)
        return create_kb_tools(vectorstore, knowledge_dir)

    # ── run: валидируем параметры ДО подключения к БД ───────────────
    if args.command == 'run':
        tool_name = args.tool_name
        tool_args = _parse_params(args.params)

        if tool_name not in TOOL_INPUT_SCHEMAS:
            print(f"Ошибка: неизвестный инструмент '{tool_name}'", file=sys.stderr)
            print(f"Доступные: {', '.join(TOOL_INPUT_SCHEMAS)}", file=sys.stderr)
            sys.exit(1)

        schema_cls = TOOL_INPUT_SCHEMAS[tool_name]
        if schema_cls is not None:
            from pydantic import ValidationError as _VE
            try:
                schema_cls.model_validate(tool_args)
            except _VE as e:
                print(f"Ошибка параметров:\n{e}\n", file=sys.stderr)
                # Показываем схему без подключения к БД
                print("=" * 80)
                print(f"Инструмент: {tool_name}")
                print("=" * 80)
                print("\nПараметры:\n")
                for field_name, field_info in schema_cls.model_fields.items():
                    type_str = str(field_info.annotation).replace("typing.", "").replace("<class '", "").replace("'>", "")
                    req_str = "обязательный" if field_info.is_required() else f"опциональный (default={field_info.default})"
                    print(f"  {field_name}={type_str}  [{req_str}]")
                    if field_info.description:
                        print(f"    {field_info.description}")
                    print()
                print(f"Использование: python kb_tools.py run {tool_name} param=value ...")
                sys.exit(1)

        # Параметры валидны — теперь подключаемся к БД
        try:
            tools = _init_tools()
        except Exception as e:
            print(f"Ошибка подключения к БД: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
        _cli_run_tool(tools, tool_name, tool_args)

    # ── list / help: сразу инициализируем ───────────────────────────
    elif args.command in ('list', 'help'):
        try:
            tools = _init_tools()
        except Exception as e:
            print(f"Ошибка инициализации: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)

        if args.command == 'list':
            _cli_list_tools(tools)
        else:
            _cli_help_tool(tools, args.tool_name)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()






