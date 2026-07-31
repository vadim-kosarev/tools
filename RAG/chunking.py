"""Shared chunking primitives for document splitters (markdown, docx).

Both `md_splitter` and `docx_splitter` produce the same chunk contract, so the
metadata model, the size-splitting algorithm and the table-row expansion live
here instead of being duplicated per format.

Chunk types produced by splitters:

  ""                Prose size-chunk (fragment of a paragraph/list/cell block)
  "paragraph_full"  Full prose block as-is (before size splitting)
  "table_row"       One data row:
                      content       = JSON object {"column": "value", ...}
                      table_headers = JSON array of header strings (column order)
  "table_full"      Full table rendered as text
  "table_raw"       Unparseable table stored verbatim

Storing a row as a column-to-value object makes it self-describing: the column
names end up in the embedded text and in exact-search matches, and reading a row
back needs no join with the header list.

``chunk_index`` is sequential **within** the same (section, chunk_type) scope of
one source document; `ChunkIndexer` owns those counters.
"""
from __future__ import annotations

import json
import re
import uuid

from langchain_core.documents import Document
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pandoc artifact cleanup
# ---------------------------------------------------------------------------

_MD_LINK_ANCHOR_RE = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")  # [text](#anchor) -> text
_MD_LINK_RE        = re.compile(r"\[([^\]]+)\]\([^)]*\)")   # [text](url)     -> text
_PANDOC_ANCHOR_RE  = re.compile(r"\(#[^)]+\)")              # (#_Ref...)      -> ""
_PANDOC_ATTR_RE    = re.compile(r"\{[^}]+\}")               # {#id .class}    -> ""


def clean_text(text: str) -> str:
    """Strip Pandoc-generated anchor links and block attributes from text."""
    text = _MD_LINK_ANCHOR_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _PANDOC_ANCHOR_RE.sub("", text)
    text = _PANDOC_ATTR_RE.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Metadata model
# ---------------------------------------------------------------------------

class ChunkMeta(BaseModel):
    """Metadata attached to every indexed chunk (write side).

    Mirrors the ClickHouse column set. `line_start`/`line_end` are 1-based and
    format-specific: source file lines for markdown, body block ordinals for docx.
    """

    source:        str
    section:       str
    chunk_type:    str
    line_start:    int
    line_end:      int
    chunk_index:   int
    guid:          str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_headers: str = ""

    def to_metadata(self) -> dict:
        """Render as a LangChain Document.metadata dict (empty headers omitted)."""
        data = self.model_dump()
        if not data["table_headers"]:
            data.pop("table_headers")
        return data


class ChunkIndexer:
    """Allocates sequential ``chunk_index`` values per (section, chunk_type)."""

    def __init__(self, source_name: str) -> None:
        self._source = source_name
        self._counters: dict[tuple[str, str], int] = {}

    @property
    def source(self) -> str:
        return self._source

    def next_index(self, section: str, chunk_type: str) -> int:
        """Return the next index for the scope and consume it."""
        key = (section, chunk_type)
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def reserve(self, section: str, chunk_type: str, count: int) -> int:
        """Reserve `count` consecutive indices; returns the first one."""
        key = (section, chunk_type)
        first = self._counters.get(key, 0) + 1
        self._counters[key] = first + count - 1
        return first

    def meta(
        self,
        section: str,
        chunk_type: str,
        line_start: int,
        line_end: int,
        chunk_index: int | None = None,
        table_headers: str = "",
    ) -> dict:
        """Build a Document.metadata dict, allocating chunk_index when omitted."""
        idx = chunk_index if chunk_index is not None else self.next_index(section, chunk_type)
        return ChunkMeta(
            source=self._source,
            section=section,
            chunk_type=chunk_type,
            line_start=line_start,
            line_end=line_end,
            chunk_index=idx,
            table_headers=table_headers,
        ).to_metadata()


# ---------------------------------------------------------------------------
# Size-bounded prose splitting
# ---------------------------------------------------------------------------

def split_text_by_size(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text into size-bounded chunks with paragraph-aware overlap."""
    separators = ["\n\n", "\n", " ", ""]
    chunks: list[str] = []

    def _split(t: str, seps: list[str]) -> None:
        if len(t) <= chunk_size:
            if t.strip():
                chunks.append(t)
            return
        sep = seps[0] if seps else ""
        parts = t.split(sep) if sep else list(t)
        current = ""
        for part in parts:
            candidate = current + (sep if current else "") + part
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current.strip():
                    chunks.append(current)
                overlap_start = max(0, len(current) - chunk_overlap)
                current = current[overlap_start:] + (sep if current else "") + part
                if len(current) > chunk_size and len(seps) > 1:
                    _split(current, seps[1:])
                    current = ""
        if current.strip():
            chunks.append(current)

    _split(text, separators)
    return chunks


# ---------------------------------------------------------------------------
# Table expansion
# ---------------------------------------------------------------------------

def normalize_headers(headers: list[str], fallback_prefix: str = "Колонка") -> list[str]:
    """Make a header list usable as object keys: non-empty and unique.

    Merged header cells in Word repeat the same text across columns and empty
    cells are common, so mapping cells onto raw headers would silently collapse
    a row into fewer values. Empty headers become "Колонка N", repeats get a
    numeric suffix; the column count is always preserved.
    """
    result: list[str] = []
    seen: dict[str, int] = {}

    for position, header in enumerate(headers, start=1):
        name = (header or "").strip() or f"{fallback_prefix} {position}"
        count = seen.get(name, 0) + 1
        seen[name] = count
        result.append(name if count == 1 else f"{name} ({count})")

    return result


def row_to_object(headers: list[str], cells: list[str]) -> dict[str, str]:
    """Map a row's cells onto column names, padding or truncating to fit."""
    padded = (cells + [""] * max(0, len(headers) - len(cells)))[: len(headers)]
    return dict(zip(headers, padded))


def table_rows_to_documents(
    headers: list[str],
    data_rows: list[list[str]],
    indexer: ChunkIndexer,
    section: str,
    line_start: int,
    line_end: int,
) -> list[Document]:
    """Create one Document per table data row.

    page_content is a JSON object mapping column name to cell value; the
    `table_headers` metadata keeps the column order for rendering the table back.
    Headers are normalised first, so no column is lost to a duplicate or empty name.
    """
    columns = normalize_headers(headers)
    headers_json = json.dumps(columns, ensure_ascii=False)
    first_index = indexer.reserve(section, "table_row", len(data_rows))

    docs: list[Document] = []
    for row_idx, row_cells in enumerate(data_rows):
        docs.append(Document(
            page_content=json.dumps(row_to_object(columns, row_cells), ensure_ascii=False),
            metadata=indexer.meta(
                section, "table_row", line_start, line_end,
                chunk_index=first_index + row_idx,
                table_headers=headers_json,
            ),
        ))
    return docs


def prose_to_documents(
    text: str,
    indexer: ChunkIndexer,
    section: str,
    line_start: int,
    line_end: int,
    chunk_size: int,
    chunk_overlap: int,
) -> list[Document]:
    """Expand a prose block into one `paragraph_full` doc plus size-split chunks.

    The full block serves context retrieval, the size-split fragments serve
    precise retrieval; both point at the same source location.
    """
    cleaned = clean_text(text)
    if not cleaned:
        return []

    docs: list[Document] = [Document(
        page_content=cleaned,
        metadata=indexer.meta(section, "paragraph_full", line_start, line_end),
    )]

    fragments = ([cleaned] if len(cleaned) <= chunk_size
                 else split_text_by_size(cleaned, chunk_size, chunk_overlap))
    for fragment in fragments:
        docs.append(Document(
            page_content=fragment,
            metadata=indexer.meta(section, "", line_start, line_end),
        ))
    return docs


class SectionStack:
    """Tracks the current heading path and renders it as a breadcrumb."""

    def __init__(self) -> None:
        self._levels: dict[int, str] = {}

    def push(self, level: int, title: str) -> None:
        """Set the heading at `level`, dropping any deeper levels."""
        self.reset(level)
        self._levels[level] = title

    def reset(self, level: int) -> None:
        """Drop the heading at `level` and everything below it.

        Used for service headings (a table of contents, for example) that open a
        level but must not appear in the breadcrumb of the content that follows.
        """
        self._levels = {lvl: t for lvl, t in self._levels.items() if lvl < level}

    def breadcrumb(self) -> str:
        return " > ".join(self._levels[lvl] for lvl in sorted(self._levels))
