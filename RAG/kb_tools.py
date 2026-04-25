"""
LangChain Tools для доступа к базе знаний в ClickHouse.

Инструменты:
  semantic_search           — семантический поиск по эмбеддингам
  exact_search              — точный поиск по одной подстроке (positionCaseInsensitive)
  multi_term_exact_search   — точный поиск по списку терминов с ранжированием по покрытию
  regex_search              — regex-поиск по исходным .md файлам с контекстом
  read_table                — чтение строк таблицы по разделу
  get_section_content       — полный текст раздела из исходного .md файла
  list_sections             — дерево разделов базы знаний (по файлу или всей KB)
  get_neighbor_chunks       — соседние чанки вокруг якоря по line_start
  list_sources              — список файлов в базе знаний с количеством чанков

Использование:
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

    class ExactSearchInput(BaseModel):
        substring: str = Field(description="Exact substring to search (case-insensitive)")
        limit: int = Field(default=exact_limit, description="Max results", ge=1, le=200)
        chunk_type: Optional[str] = Field(
            default=None,
            description="Filter by chunk type: 'table_row' (table data rows), "
                        "'table_full' (full tables), '' (prose chunks), None (all types)",
        )

    class MultiTermExactSearchInput(BaseModel):
        terms: list[str] = Field(
            description=(
                "List of substrings to search simultaneously (case-insensitive). "
                "Each chunk is scored by how many terms it contains. "
                "Results are ranked: chunks matching ALL terms first, then most terms, "
                "then fewer — so the most relevant results always appear at the top."
            )
        )
        limit: int = Field(default=exact_limit, description="Max results", ge=1, le=200)
        chunk_type: Optional[str] = Field(
            default=None,
            description="Filter by chunk type: 'table_row', 'table_full', '' (prose), None (all)",
        )

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

    # ── Tool implementations ──────────────────────────────────────────

    @tool(args_schema=SemanticSearchInput)
    def semantic_search(query: str, top_k: int = semantic_top_k) -> str:
        """
        Semantic similarity search in the knowledge base using vector embeddings (bge-m3).
        Best for: conceptual questions, 'what is X', 'how does Y work', broad topic search.
        Returns top-K most semantically similar text chunks from ClickHouse.
        Metadata includes: source file, section breadcrumb, line_start for context expansion.
        """
        logger.debug(f"Tool semantic_search: query='{query[:80]}', top_k={top_k}")
        rec = _db_request("DB:semantic_search", f"query={query!r}\ntop_k={top_k}")
        docs = vectorstore.clone().similarity_search(query, k=top_k)
        result = _fmt_docs(docs)
        if rec:
            rec.set_response(f"Найдено {len(docs)} чанков\n\n{result[:3000]}")
        logger.info(f"semantic_search '{query[:60]}': {len(docs)} чанков")
        return result

    @tool(args_schema=ExactSearchInput)
    def exact_search(substring: str, limit: int = exact_limit, chunk_type: Optional[str] = None) -> str:
        """
        Case-insensitive exact substring search in knowledge base content (positionCaseInsensitive).
        Best for: specific terms, abbreviations, system names, section titles.
        Use chunk_type='table_row' to search only within table data (each row = separate chunk).
        Use chunk_type='table_full' to get complete tables.
        Use chunk_type='' for prose text chunks only.
        Each result includes source file, section breadcrumb, and line_start for neighbor expansion.
        """
        logger.debug(f"Tool exact_search: substring='{substring}', chunk_type={chunk_type!r}")
        rec = _db_request(
            "DB:exact_search",
            f"substring={substring!r}\nlimit={limit}\nchunk_type={chunk_type!r}",
        )
        docs = vectorstore.clone().exact_search(substring, limit=limit, chunk_type=chunk_type)
        result = _fmt_docs(docs)
        if rec:
            rec.set_response(f"Найдено {len(docs)} чанков\n\n{result[:3000]}")
        logger.info(f"exact_search '{substring}': {len(docs)} чанков")
        return result

    @tool(args_schema=MultiTermExactSearchInput)
    def multi_term_exact_search(
        terms: list[str],
        limit: int = exact_limit,
        chunk_type: Optional[str] = None,
    ) -> str:
        """
        Multi-term exact search ranked by the number of matching terms per chunk.

        Searches all given terms simultaneously across the knowledge base.
        Each chunk is assigned a match_count = number of terms found in its content.
        Results are sorted: ALL terms matched first, then most terms, then fewer.

        Use this as the FIRST exact-search step when you have multiple key terms —
        it finds the most relevant chunks (highest term coverage) in a single call.
        For terms not covered by the top results, follow up with individual exact_search.

        Returns results grouped by match_count with a coverage header per group.
        """
        logger.debug(f"Tool multi_term_exact_search: terms={terms}, chunk_type={chunk_type!r}")
        rec = _db_request(
            "DB:multi_term_exact_search",
            f"terms={terms}\nlimit={limit}\nchunk_type={chunk_type!r}",
        )
        scored: list[tuple] = vectorstore.clone().multi_term_exact_search(
            terms=terms, limit=limit, chunk_type=chunk_type
        )

        if not scored:
            msg = f"По терминам {terms} ничего не найдено."
            if rec:
                rec.set_response(msg)
            return msg

        total_terms = len(terms)
        # Group docs by match_count for display
        from collections import defaultdict as _dd
        groups: dict[int, list] = _dd(list)
        for doc, cnt in scored:
            groups[cnt].append(doc)

        parts: list[str] = [
            f"Найдено {len(scored)} чанков по {total_terms} терминам: {terms}\n"
        ]
        for cnt in sorted(groups.keys(), reverse=True):
            label = f"✅ ВСЕ {cnt}/{total_terms} терминов" if cnt == total_terms else f"🔸 {cnt}/{total_terms} терминов"
            parts.append(f"── {label} ({len(groups[cnt])} чанков) ──")
            parts.append(_fmt_docs(groups[cnt]))

        out = "\n".join(parts)
        if rec:
            rec.set_response(f"Найдено {len(scored)} чанков\n\n{out[:3000]}")
        logger.info(
            f"multi_term_exact_search {terms}: {len(scored)} чанков  "
            f"(max coverage {max(groups)}/{total_terms})"
        )
        return out


    @tool(args_schema=RegexSearchInput)
    def regex_search(pattern: str, max_results: int = regex_max_results) -> str:
        """
        Regex pattern search directly in source .md files with surrounding context lines.
        Best for: IP addresses, port numbers, VLAN IDs, document codes, subnet masks, any structured patterns.
        Each match includes file name, line number, matched text, and 5 context lines around the match.
        """
        logger.debug(f"Tool regex_search: pattern='{pattern}'")
        rec = _db_request("DB:regex_search", f"pattern={pattern!r}\nmax_results={max_results}")
        result = _kb_regex_search(pattern, knowledge_dir)
        if result.total_matches == 0:
            text = f"По паттерну '{pattern}' совпадений не найдено."
            if rec:
                rec.set_response(text)
            return text
        parts: list[str] = [f"Найдено совпадений: {result.total_matches}"]
        for m in result.matches[:max_results]:
            parts.append(f"[{m.file}] строка {m.line_number}: {m.match}\n{m.context}")
        if result.total_matches > max_results:
            parts.append(f"... и ещё {result.total_matches - max_results} совпадений (увеличьте max_results).")
        out = "\n\n---\n\n".join(parts)
        if rec:
            rec.set_response(f"total_matches={result.total_matches}\n\n{out[:3000]}")
        logger.info(f"regex_search '{pattern}': {result.total_matches} совпадений")
        return out

    @tool(args_schema=ReadTableInput)
    def read_table(section: str, source_file: Optional[str] = None, limit: int = 50) -> str:
        """
        Read table rows from a specific section of the knowledge base.
        Returns rows as 'Column: value' pairs for easy reading.
        Best for: structured data, IP tables, server lists, VLAN assignments, software versions.
        Use list_sections first to find the exact section name if needed.
        If source_file is provided, only that file is searched.
        """
        logger.debug(f"Tool read_table: section='{section}', source_file={source_file!r}")
        rec = _db_request(
            "DB:read_table",
            f"section={section!r}\nsource_file={source_file!r}\nlimit={limit}",
        )
        docs = _query_table_chunks(vectorstore, section, source_file, limit)
        if not docs:
            msg = f"Таблицы в разделе '{section}' не найдены. Проверьте название через list_sections."
            if rec:
                rec.set_response(msg)
            return msg
        result = _fmt_docs(docs, max_content_chars=2000)
        if rec:
            rec.set_response(f"Найдено {len(docs)} строк\n\n{result[:3000]}")
        logger.info(f"read_table '{section}': {len(docs)} записей")
        return result

    @tool(args_schema=GetSectionContentInput)
    def get_section_content(source_file: str, section: str) -> str:
        """
        Read the full text content of a specific section directly from the source .md file.
        Best for: reading complete sections that may be split across many chunks, full tables,
        numbered lists, code blocks that need to be read in full context.
        Returns the entire section text including all subsections.
        Use list_sections to find exact section and file names first.
        """
        logger.debug(f"Tool get_section_content: [{source_file}] '{section}'")
        rec = _db_request("DB:get_section_content", f"source_file={source_file!r}\nsection={section!r}")
        content = read_full_section(knowledge_dir, source_file, section)
        if content is None:
            rows = _query_sections(vectorstore, source_file)
            similar = [s for _, s in rows if section.lower() in s.lower()][:5]
            hint = (
                f"Похожие разделы: {'; '.join(similar)}" if similar
                else "Используйте list_sections для поиска разделов."
            )
            msg = f"Раздел '{section}' не найден в файле '{source_file}'. {hint}"
            if rec:
                rec.set_response(msg)
            return msg
        result = f"[{source_file}] — {section}\n\n{content}"
        if rec:
            rec.set_response(f"{len(content)} символов\n\n{result[:3000]}")
        logger.info(f"get_section_content: [{source_file}] '{section}' — {len(content)} символов")
        return result

    @tool(args_schema=ListSectionsInput)
    def list_sections(source_file: Optional[str] = None) -> str:
        """
        List all sections (breadcrumb paths H1 > H2 > ...) in the knowledge base.
        Best for: discovering available content, finding the exact section name before using
        get_section_content or read_table, understanding document structure.
        Optionally filter by source_file to see sections from one document only.
        Use list_sources first to get file names.
        """
        logger.debug(f"Tool list_sections: source_file={source_file!r}")
        rec = _db_request("DB:list_sections", f"source_file={source_file!r}")
        rows = _query_sections(vectorstore, source_file)
        if not rows:
            msg = "Разделы не найдены."
            if rec:
                rec.set_response(msg)
            return msg

        by_source: dict[str, list[str]] = defaultdict(list)
        for src, sec in rows:
            if sec:
                by_source[src].append(sec)

        lines: list[str] = []
        for src, sections in sorted(by_source.items()):
            lines.append(f"\n📄 {src}:")
            for sec in sections[:60]:
                lines.append(f"   • {sec}")
            if len(sections) > 60:
                lines.append(f"   ... и ещё {len(sections) - 60} разделов")

        total = sum(len(v) for v in by_source.values())
        result = f"Структура базы знаний ({total} разделов):" + "".join(lines)
        if rec:
            rec.set_response(f"Итого разделов: {total} из {len(by_source)} файлов\n\n{result[:2000]}")
        logger.info(f"list_sections: {total} разделов из {len(by_source)} файлов")
        return result

    @tool(args_schema=GetNeighborChunksInput)
    def get_neighbor_chunks(source: str, line_start: int, before: int = 5, after: int = 5) -> str:
        """
        Get neighboring chunks around a specific chunk in the same source file.
        Best for: expanding context when a found chunk is incomplete or cut off.
        'source' and 'line_start' values come from metadata of previous search results.
        Returns up to 'before' chunks before and 'after' chunks after the anchor position.
        """
        logger.debug(f"Tool get_neighbor_chunks: [{source}] line {line_start}, before={before}, after={after}")
        rec = _db_request(
            "DB:get_neighbor_chunks",
            f"source={source!r}\nline_start={line_start}\nbefore={before}\nafter={after}",
        )
        docs = vectorstore.clone().get_neighbor_chunks(source, line_start, before=before, after=after)
        result = _fmt_docs(docs, max_content_chars=1500)
        if rec:
            rec.set_response(f"Найдено {len(docs)} соседних чанков\n\n{result[:3000]}")
        logger.info(f"get_neighbor_chunks: {len(docs)} соседних чанков вокруг [{source}] line {line_start}")
        return result

    @tool
    def list_sources() -> str:
        """
        List all source documents in the knowledge base with their chunk counts.
        Best for: discovering what files are available, finding the right document to query,
        understanding the overall scope of the knowledge base.
        Always start here if you don't know which files contain the needed information.
        """
        logger.debug("Tool list_sources")
        rec = _db_request("DB:list_sources", "GROUP BY source ORDER BY source")
        db, tbl = vectorstore._cfg.database, vectorstore._cfg.table
        sql = (
            f"SELECT source, count() AS cnt "
            f"FROM {db}.{tbl} FINAL "
            f"GROUP BY source ORDER BY source"
        )
        result = vectorstore.clone()._client.query(sql)
        rows = result.result_rows
        if not rows:
            msg = "База знаний пуста."
            if rec:
                rec.set_response(msg)
            return msg
        total_chunks = sum(r[1] for r in rows)
        lines: list[str] = [f"Файлов в базе знаний: {len(rows)} (чанков всего: {total_chunks})\n"]
        for src, cnt in rows:
            lines.append(f"  📄 {src}: {cnt} чанков")
        out = "\n".join(lines)
        if rec:
            rec.set_response(out)
        logger.info(f"list_sources: {len(rows)} источников")
        return out

    return [
        semantic_search,
        exact_search,
        multi_term_exact_search,
        regex_search,
        read_table,
        get_section_content,
        list_sections,
        get_neighbor_chunks,
        list_sources,
    ]

