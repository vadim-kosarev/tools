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
import re
from collections import defaultdict
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
        source=meta['source'],
        section=meta['section'],
        chunk_type=meta['chunk_type'],
        line_start=meta['line_start'],
        line_end=meta['line_end'],
        chunk_index=meta['chunk_index'],
        table_headers=meta.get('table_headers')
    )


def _doc_to_chunk_result(doc: Document) -> ChunkResult:
    """Конвертер Document → ChunkResult"""
    return ChunkResult(
        content=doc.page_content,
        metadata=_doc_to_chunk_metadata(doc.metadata)
    )


def _docs_to_chunk_results(docs: list[Document]) -> list[ChunkResult]:
    """Конвертер списка Document → список ChunkResult"""
    return [_doc_to_chunk_result(doc) for doc in docs]


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
# Заголовочный regex (для чтения секций из .md файлов)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_MD_LINK_ANCHOR_RE = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")
_PANDOC_ANCHOR_RE  = re.compile(r"\(#[^)]+\)")
_PANDOC_ATTR_RE    = re.compile(r"\{[^}]+\}")


def _clean_header_text(text: str) -> str:
    """Removes Pandoc-generated anchor links from heading text."""
    text = _MD_LINK_ANCHOR_RE.sub(r"\1", text)
    text = _PANDOC_ANCHOR_RE.sub("", text)
    text = _PANDOC_ATTR_RE.sub("", text)
    return text.strip()


def read_full_section(knowledge_dir: Path, source_file: str, section_breadcrumb: str) -> str | None:
    """
    Reads the full text of a section from a .md source file by breadcrumb path.

    Finds the heading matching the last breadcrumb component and collects all
    content until the next heading of equal or higher level.
    Returns None if file or section is not found.
    """
    matches = list(knowledge_dir.glob(f"**/{source_file}"))
    if not matches:
        return None
    md_file = matches[0]
    try:
        text = md_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning(f"Не удалось прочитать {source_file}: {exc}")
        return None

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
            header_text = _clean_header_text(m.group(2).strip())
            if not in_section:
                if header_text.lower() == target_header.lower():
                    in_section = True
                    section_level = level
                    collected.append(line)
            else:
                if level <= section_level:
                    break
                collected.append(line)
        elif in_section:
            collected.append(line)

    return "".join(collected).strip() if collected else None


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
            f"ORDER BY source, section LIMIT 500"
        )
        result = client.query(sql, parameters={"src": source_file})
    else:
        sql = (
            f"SELECT DISTINCT source, section "
            f"FROM {db}.{tbl} FINAL "
            f"ORDER BY source, section LIMIT 2000"
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
    where = ["positionCaseInsensitive(section, {sec:String}) > 0",
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

    # ── Pydantic schemas for tool inputs ──────────────────────────────

    class SemanticSearchInput(BaseModel):
        query: str = Field(description="Query text for semantic similarity search (in Russian or English)")
        top_k: int = Field(default=semantic_top_k, description="Number of results to return", ge=1, le=50)
        chunk_type: str = Field(
            default="",
            description="Filter by chunk type: '' (prose chunks, default), 'table_row' (table rows), 'table_full' (full tables), or empty string for prose only"
        )
        source: Optional[str] = Field(default=None, description="Optional: filter by source filename (e.g. 'servers.md')")
        section: Optional[str] = Field(default=None, description="Optional: filter by section name/breadcrumb substring")

    class ExactSearchInput(BaseModel):
        substring: str = Field(description="Exact substring to search (case-insensitive)")
        limit: int = Field(default=exact_limit, description="Max results", ge=1, le=200)
        chunk_type: str = Field(
            default="",
            description="Filter by chunk type: '' (prose chunks, default), 'table_row' (table rows), 'table_full' (full tables)"
        )
        source: Optional[str] = Field(default=None, description="Optional: filter by source filename (e.g. 'servers.md')")
        section: Optional[str] = Field(default=None, description="Optional: filter by section name/breadcrumb substring")

    class ExactSearchInFileInput(BaseModel):
        substring: str = Field(description="Exact substring to search (case-insensitive)")
        source_file: str = Field(description="Source filename to search in (e.g. 'servers.md')")
        limit: int = Field(default=exact_limit, description="Max results", ge=1, le=200)
        chunk_type: Optional[str] = Field(
            default=None,
            description="Filter by chunk type: 'table_row', 'table_full', '' (prose), None (all)",
        )

    class ExactSearchInFileSectionInput(BaseModel):
        substring: str = Field(description="Exact substring to search (case-insensitive)")
        source_file: str = Field(description="Source filename to search in (e.g. 'servers.md')")
        section: str = Field(description="Section name or breadcrumb substring to search in")
        limit: int = Field(default=exact_limit, description="Max results", ge=1, le=200)
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
        limit: int = Field(default=exact_limit, description="Max results", ge=1, le=200)
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
        max_results: int = Field(default=regex_max_results, description="Max matches to return", ge=1, le=200)

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
            min_items=1,
            max_items=50
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
        query: str = Field(
            description="User query phrase (for section name matching)"
        )
        exact_terms: list[str] = Field(
            description="List of exact terms to search in content (for content matching)",
            default_factory=list
        )
        limit: int = Field(
            default=50,
            description="Maximum number of sections to return in results",
            ge=1,
            le=200
        )
        source: Optional[str] = Field(default=None, description="Optional: filter by source filename")

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
        Semantic similarity search in the knowledge base using vector embeddings (bge-m3).
        Best for: conceptual questions, 'what is X', 'how does Y work', broad topic search.
        Returns top-K most semantically similar text chunks from ClickHouse.
        By default searches only in prose chunks (chunk_type=""), not in full tables.
        Metadata includes: source file, section breadcrumb, line_start for context expansion.

        Optional filters:
        - chunk_type: filter by chunk type (default: "" for prose only)
        - source: limit search to specific file (e.g. 'servers.md')
        - section: limit search to specific section substring
        
        Returns:
            SearchChunksResult with query, list of chunks with metadata, and total_found count
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
        
        # Поиск с фильтром по chunk_type
        docs = vectorstore.clone().similarity_search(
            query, k=top_k, chunk_type=chunk_type, source=source, section=section
        )
        
        # Конвертация в структурированный результат и дедупликация
        chunks = _deduplicate_chunks(_docs_to_chunk_results(docs))
        result = SearchChunksResult(
            query=query,
            chunks=chunks,
            total_found=len(chunks)
        )
        
        # Логирование
        if rec:
            rec.set_response(f"Найдено {len(chunks)} чанков\n\n{result.model_dump_json(indent=2)}")
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
        Case-insensitive exact substring search in knowledge base content (positionCaseInsensitive).
        Best for: specific terms, abbreviations, system names, section titles.
        By default searches only in prose chunks (chunk_type=""), not in full tables.
        Use chunk_type='table_row' to search only within table data (each row = separate chunk).
        Use chunk_type='table_full' to get complete tables.
        Each result includes source file, section breadcrumb, and line_start for neighbor expansion.

        Optional filters:
        - chunk_type: filter by chunk type (default: "" for prose only)
        - source: limit search to specific file (e.g. 'servers.md')
        - section: limit search to specific section substring

        When both source and section are provided, performs highly targeted search
        (equivalent to exact_search_in_file_section).
        
        Returns:
            SearchChunksResult with substring as query, list of chunks, and total_found count
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
            rec.set_response(f"Найдено {len(chunks)} чанков\n\n{result.model_dump_json(indent=2)}")
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
        Case-insensitive exact substring search within a specific file.
        Best for: focused search when you know the exact file name, narrowing down results
        to a specific document, avoiding noise from other files.
        Use list_sources first to find available filenames.
        Returns chunks from the specified file only, sorted by line number.
        Each result includes section breadcrumb and line_start for neighbor expansion.
        
        Returns:
            SearchChunksResult with substring as query, chunks from specified file, total_found count
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
            rec.set_response(f"Найдено {len(chunks)} чанков в {source_file}\n\n{result.model_dump_json(indent=2)}")
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
        Case-insensitive exact substring search within a specific section of a specific file.
        Best for: highly targeted search when you know both the file and section,
        finding specific data in known documentation structure (e.g. IP in 'Servers > Database').
        Use list_sections first to find exact section names.
        Returns chunks matching file + section + substring, sorted by line number.
        Most precise search tool — minimal noise, maximum relevance.
        
        Returns:
            SearchChunksResult with substring as query, chunks from specified section, total_found count
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
            rec.set_response(f"Найдено {len(chunks)} чанков в [{source_file}] -> {section}\n\n{result.model_dump_json(indent=2)}")
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
        Multi-term exact search ranked by the number of matching terms per chunk.
        By default searches only in prose chunks (chunk_type=""), not in full tables.

        Searches all given terms simultaneously across the knowledge base.
        Each chunk is assigned a match_count = number of terms found in its content.
        Results are sorted: ALL terms matched first, then most terms, then fewer.

        Use this as the FIRST exact-search step when you have multiple key terms —
        it finds the most relevant chunks (highest term coverage) in a single call.
        For terms not covered by the top results, follow up with individual exact_search.

        Returns results grouped by match_count (coverage) with chunks sorted by relevance.
        
        Optional filters:
        - chunk_type: filter by chunk type (default: "" for prose only)
        - source: limit search to specific file (e.g. 'servers.md')
        - section: limit search to specific section substring
        
        Returns:
            MultiTermSearchResult with terms, chunks_by_coverage dict, total_chunks, and max_coverage
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
            rec.set_response(f"Найдено {result.total_chunks} чанков\n\n{result.model_dump_json(indent=2)}")
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
        Find all unique sections (source + section) containing exact substring matches.
        
        Best for: discovering which sections contain specific terms before diving deep.
        Returns a list of (source, section) pairs with match counts, sorted by relevance.
        Use this to identify the most relevant sections, then use exact_search or
        get_section_content to get detailed information.
        
        This scans up to 'limit' chunks and returns ALL unique sections found within
        that scan. For example, with limit=100 searching "PostgreSQL", you'll get
        all sections that mention PostgreSQL within the first 100 matching chunks.
        
        Optional filters:
        - source: limit search to specific file
        - chunk_type: filter by chunk type
        
        Returns:
            SearchSectionsResult with substring as query, list of sections with counts, total_found
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
            rec.set_response(f"Найдено {result.total_found} разделов\n\n{result.model_dump_json(indent=2)}")
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
        Two-stage search strategy for finding relevant sections:
        
        STAGE 1: Search by section NAME - finds sections whose title matches the query
        STAGE 2: Search by CONTENT - finds sections containing exact terms
        
        Results are combined and prioritized:
        - Sections matching by NAME are marked with match_type="NAME"
        - Sections matching by CONTENT have match_type="CONTENT"
        - Duplicates are merged with combined metadata
        
        Best for: comprehensive section discovery matching both title and content.
        Use this as the PRIMARY search tool when you need to find all relevant sections.
        
        Args:
            query: User query phrase (searched in section names)
            exact_terms: List of exact terms to search in content (optional)
            limit: Maximum number of sections to return (default 50)
            source: Optional source file filter
        
        Returns:
            SearchSectionsResult with query, top-N sections prioritized by NAME > CONTENT, counts
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
            rec.set_response(f"Найдено {result.total_found}, возвращено топ-{result.returned_count}\n\n{result.model_dump_json(indent=2)}")
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
        Regex pattern search directly in source .md files with surrounding context lines.
        Best for: IP addresses, port numbers, VLAN IDs, document codes, subnet masks, any structured patterns.
        Each match includes file name, line number, matched text, and context lines around the match.

        Returns:
            RegexSearchResult with pattern, list of matches with context, and total_matches count
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
            rec.set_response(f"total_matches={result.total_matches}\n\n{result.model_dump_json(indent=2)}")
        logger.info(f"regex_search '{pattern}': {result.total_matches} совпадений")

        return result

    @tool(args_schema=ReadTableInput)
    def read_table(section: str, source_file: Optional[str] = None, limit: int = 50) -> TableResult:
        """
        Read table rows from a specific section of the knowledge base.
        Returns structured table rows with columns as dict for easy processing.
        Best for: structured data, IP tables, server lists, VLAN assignments, software versions.
        Use list_sections first to find the exact section name if needed.
        If source_file is provided, only that file is searched.
        
        Returns:
            TableResult with section_query, list of rows with columns, and total_rows count
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
            rec.set_response(f"Найдено {result.total_rows} строк\n\n{result.model_dump_json(indent=2)}")
        logger.info(f"read_table '{section}': {result.total_rows} записей")
        
        return result

    @tool(args_schema=GetSectionContentInput)
    def get_section_content(source_file: str, section: str) -> SectionContent:
        """
        Read the full text content of a specific section directly from the source .md file.
        Best for: reading complete sections that may be split across many chunks, full tables,
        numbered lists, code blocks that need to be read in full context.
        Returns the entire section text including all subsections.
        Use list_sections to find exact section and file names first.
        
        Returns:
            SectionContent with source, section name, line numbers (approximate), and full content
        """
        logger.debug(f"Tool get_section_content: [{source_file}] '{section}'")
        rec = _db_request("DB:get_section_content", f"source_file={source_file!r}\nsection={section!r}")
        
        # Чтение полного раздела из .md файла
        content = read_full_section(knowledge_dir, source_file, section)
        
        if content is None:
            # Если не найдено - возвращаем пустой результат (можно бросить исключение или вернуть пустой)
            rows = _query_sections(vectorstore, source_file)
            similar = [s for _, s in rows if section.lower() in s.lower()][:5]
            hint = (
                f"Похожие разделы: {'; '.join(similar)}" if similar
                else "Используйте list_sections для поиска разделов."
            )
            error_msg = f"Раздел '{section}' не найден в файле '{source_file}'. {hint}"
            
            # Возвращаем пустой SectionContent с сообщением об ошибке
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
        
        # Попытка определить номера строк (приблизительно) через запрос к БД
        # Ищем любой чанк из этого раздела чтобы узнать line_start
        client = vectorstore.clone()._client
        db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
        sql = f"""
            SELECT MIN(line_start) as start, MAX(line_end) as end
            FROM {db}.{tbl} FINAL
            WHERE source = {{src:String}} AND section = {{sec:String}}
        """
        query_result =client.query(sql, parameters={"src": source_file, "sec": section})
        line_start, line_end = 0, 0
        if query_result.result_rows:
            line_start = query_result.result_rows[0][0] or 0
            line_end = query_result.result_rows[0][1] or 0
        
        # Конвертация в структурированный результат
        result = SectionContent(
            source=source_file,
            section=section,
            line_start=line_start,
            line_end=line_end,
            content=content
        )
        
        # Логирование
        if rec:
            rec.set_response(f"{len(content)} символов\n\n{result.model_dump_json(indent=2)}")
        logger.info(f"get_section_content: [{source_file}] '{section}' — {len(content)} символов")
        
        return result

    @tool(args_schema=ListSectionsInput)
    def list_sections(source_file: Optional[str] = None) -> SectionsTree:
        """
        List all sections (breadcrumb paths H1 > H2 > ...) in the knowledge base.
        Best for: discovering available content, finding the exact section name before using
        get_section_content or read_table, understanding document structure.
        Optionally filter by source_file to see sections from one document only.
        Use list_sources first to get file names.
        
        Returns:
            SectionsTree with optional source filter, list of sections with chunk counts, and total count
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
        Get neighboring chunks around a specific chunk in the same source file.
        Best for: expanding context when a found chunk is incomplete or cut off.
        'source' and 'line_start' values come from metadata of previous search results.
        Returns up to 'before' chunks before and 'after' chunks after the anchor position.
        
        Args:
            source: Source filename
            line_start: Line position of anchor chunk
            before: Number of chunks to get before anchor
            after: Number of chunks to get after anchor
            include_anchor: If True (default), also returns the anchor chunk itself
        
        Returns:
            NeighborChunksResult with anchor_chunk (optional), chunks_before list, and chunks_after list
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
            rec.set_response(f"Найдено {total_chunks} чанков (якорь: {'да' if anchor_chunk else 'нет'})\n\n{result.model_dump_json(indent=2)}")
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
        Get specific chunks by their indices from a particular source file and section.
        Best for: retrieving specific chunks when you know exact indices from previous search results.
        Useful for fetching referenced chunks or building context from known positions.
        
        Args:
            source: Source filename (e.g. 'servers.md')
            section: Section name or breadcrumb path
            chunk_indices: List of chunk indices to retrieve (e.g. [0, 1, 5])
        
        Returns:
            SearchChunksResult with the requested chunks
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
            rec.set_response(f"Найдено {len(chunks)} чанков\n\n{result.model_dump_json(indent=2)}")
        logger.info(
            f"get_chunks_by_index: {len(chunks)} чанков из [{source}] {section}, "
            f"indices={chunk_indices}"
        )
        
        return result

    @tool
    def list_sources() -> SourcesList:
        """
        List all source documents in the knowledge base with their chunk counts.
        Best for: discovering what files are available, finding the right document to query,
        understanding the overall scope of the knowledge base.
        Always start here if you don't know which files contain the needed information.
        
        Returns:
            SourcesList with list of sources, total sources count, and total chunks count
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
        List all unique (source, section_name) pairs from the knowledge base.
        Section name is extracted as the last subsection (last part after ' > ').
        Best for: getting a complete list of available sections before planning,
        avoiding assumptions about non-existent documentation sections.
        Should be called at the beginning of query processing to understand available content.

        Returns:
            SearchSectionsResult with query="", list of all unique sections, and totals
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
        "read_table": "Чтение строк таблицы по названию раздела",
        "get_section_content": "Полный текст раздела из .md файла",
        "list_sections": "Список разделов документации",
        "get_neighbor_chunks": "Соседние чанки вокруг найденного фрагмента",
        "get_chunks_by_index": "Получить конкретные чанки по индексам (source, section, chunk_indices[])",
        "list_sources": "Список файлов в базе знаний",
        "list_all_sections": "Все уникальные пары (source, section)",
    }



