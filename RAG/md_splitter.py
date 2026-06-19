"""Markdown document splitter using markdown-it-py for structural parsing.

Produces LangChain Documents with proper section > subsection breadcrumbs:

  chunk_type=""             Prose size-chunk (paragraph/list/code fragment)
  chunk_type="paragraph_full"  Full prose block as-is (before size splitting)
  chunk_type="table_row"    One data row:
                              content       = JSON array of cell values, e.g. ["v1","v2"]
                              table_headers = JSON array of header strings, e.g. ["h1","h2"]
  chunk_type="table_full"   Full table raw text as-is (pipe or grid)
                              table_headers = JSON array of header strings
  chunk_type="table_raw"    Unparseable table stored verbatim

Each Document carries a unique ``guid`` (UUID4) in its metadata.
``chunk_index`` is sequential **within** the same (source, section, chunk_type) scope.

Supports:
  - GFM pipe tables  (| col | col |) via markdown-it-py AST
  - Grid/RST tables  (+----+----+)   via fallback line-based parser
  - Pandoc anchor/attribute artifacts stripped from all text:
      [text](#_Ref...)  →  text
      (#_Ref...)        →  (removed)
      {#id .class}      →  (removed)
"""
from __future__ import annotations

import re
import json
import uuid
import logging
from pathlib import Path

from langchain_core.documents import Document
from markdown_it import MarkdownIt
from markdown_it.token import Token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pandoc artifact cleanup
# ---------------------------------------------------------------------------

_MD_LINK_ANCHOR_RE = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")  # [text](#anchor) → text
_MD_LINK_RE        = re.compile(r"\[([^\]]+)\]\([^)]*\)")   # [text](url)     → text
_PANDOC_ANCHOR_RE  = re.compile(r"\(#[^)]+\)")               # (#_Ref...)      → ""
_PANDOC_ATTR_RE    = re.compile(r"\{[^}]+\}")                # {#id .class}    → ""


def _clean_text(text: str) -> str:
    """Strip Pandoc-generated anchor links and block attributes from text."""
    text = _MD_LINK_ANCHOR_RE.sub(r"\1", text)  # [text](#anchor) → text
    text = _MD_LINK_RE.sub(r"\1", text)          # [text](url)     → text
    text = _PANDOC_ANCHOR_RE.sub("", text)       # (#_Ref...)      → ""
    text = _PANDOC_ATTR_RE.sub("", text)         # {#id .class}    → ""
    return text.strip()


# ---------------------------------------------------------------------------
# Inline token rendering
# ---------------------------------------------------------------------------

def _render_inline(token: Token) -> str:
    """Extract plain text from an inline token (recursing into children)."""
    if not token.children:
        return token.content or ""
    parts: list[str] = []
    for child in token.children:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type == "softbreak":
            parts.append(" ")
    return "".join(parts)


# ---------------------------------------------------------------------------
# GFM pipe-table parsing from markdown-it token stream
# ---------------------------------------------------------------------------

def _parse_pipe_table_tokens(
    table_tokens: list[Token],
) -> tuple[list[str], list[list[str]]]:
    """Extract (headers, data_rows) from a slice of table_open … table_close tokens."""
    headers: list[str] = []
    data_rows: list[list[str]] = []
    in_head = False
    in_body = False
    current_row: list[str] = []

    for tok in table_tokens:
        if tok.type == "thead_open":
            in_head = True
        elif tok.type == "thead_close":
            in_head = False
        elif tok.type == "tbody_open":
            in_body = True
        elif tok.type == "tbody_close":
            in_body = False
        elif tok.type == "tr_open":
            current_row = []
        elif tok.type == "tr_close":
            if in_head:
                headers = current_row
            elif in_body:
                data_rows.append(current_row)
            current_row = []
        elif tok.type == "inline" and tok.children is not None:
            cell_text = _clean_text(_render_inline(tok))
            current_row.append(cell_text)

    return headers, data_rows


# ---------------------------------------------------------------------------
# Grid / RST table fallback parser (+----+----+)
# ---------------------------------------------------------------------------

_GRID_FIRST_LINE_RE = re.compile(r"^\+[-=+]+\+\s*$")


def _is_grid_table(text: str) -> bool:
    """Return True if text looks like a grid/RST table (starts with +---+)."""
    first_line = text.lstrip().split("\n")[0].rstrip()
    return bool(_GRID_FIRST_LINE_RE.match(first_line))


def _parse_grid_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """Parse RST/grid table (+----+) into (headers, data_rows).

    Supports multi-line cells (concatenated with space).
    Strips Pandoc artifacts from cell values.
    """
    sep_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("+") and "-" in line
    ]
    if len(sep_indices) < 2:
        return [], []

    first_sep = lines[sep_indices[0]].rstrip()
    col_starts = [i for i, c in enumerate(first_sep) if c == "+"]
    if len(col_starts) < 2:
        return [], []

    col_ranges: list[tuple[int, int]] = [
        (col_starts[j] + 1, col_starts[j + 1])
        for j in range(len(col_starts) - 1)
    ]

    def _extract_cells(row_lines: list[str]) -> list[str]:
        cells = [""] * len(col_ranges)
        for rline in row_lines:
            if not rline.lstrip().startswith("|"):
                continue
            padded = rline.rstrip()
            for ci, (start, end) in enumerate(col_ranges):
                if start < len(padded):
                    part = padded[start:end].strip()
                    if part:
                        cells[ci] = (cells[ci] + " " + part).strip()
        return cells

    headers: list[str] = []
    data_rows: list[list[str]] = []

    for block_idx in range(len(sep_indices) - 1):
        block_start = sep_indices[block_idx] + 1
        block_end = sep_indices[block_idx + 1]
        cells = _extract_cells(lines[block_start:block_end])
        if not any(cells):
            continue
        if not headers:
            headers = [_clean_text(c) for c in cells]
        elif cells != headers:
            data_rows.append([_clean_text(c) for c in cells])

    return headers, data_rows


# ---------------------------------------------------------------------------
# Document creation helpers
# ---------------------------------------------------------------------------

def _table_to_docs(
    headers: list[str],
    data_rows: list[list[str]],
    source_name: str,
    breadcrumb: str,
    line_start: int = 0,
    line_end: int = 0,
    chunk_index_start: int = 0,
) -> list[Document]:
    """Create one Document per table data row.

    page_content format:
        JSON array of cell values for this row, e.g. ["v1", "v2", "v3"].
        All special characters (newlines, quotes, etc.) are escaped by json.dumps.

    table_headers metadata:
        JSON array of column header strings, e.g. ["h1", "h2", "h3"].

    Args:
        line_start:        1-based first line of the table block in source file.
        line_end:          1-based last line (exclusive) of the table block.
        chunk_index_start: chunk_index for the first row (incremented per row).
    """
    headers_json = json.dumps(headers, ensure_ascii=False)
    docs: list[Document] = []

    for row_idx, row_cells in enumerate(data_rows):
        # Normalise row length to match header count
        padded = (row_cells + [""] * max(0, len(headers) - len(row_cells)))[: len(headers)]
        content_json = json.dumps(padded, ensure_ascii=False)

        docs.append(Document(
            page_content=content_json,
            metadata={
                "source":        source_name,
                "section":       breadcrumb,
                "chunk_type":    "table_row",
                "table_headers": headers_json,
                "line_start":    line_start,
                "line_end":      line_end,
                "chunk_index":   chunk_index_start + row_idx,
                "guid":          str(uuid.uuid4()),
            },
        ))

    return docs


def _split_text_by_size(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
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
# Shared markdown-it instance (GFM tables enabled)
# ---------------------------------------------------------------------------

_md = MarkdownIt("commonmark").enable("table")


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def split_md_file(
    md_file: Path,
    chunk_size: int = 1500,
    chunk_overlap: int = 150,
) -> list[Document]:
    """Parse a Markdown file into LangChain Documents using markdown-it-py.

    For each content block two complementary representations are stored:

    Prose blocks:
        chunk_type=""             size-split fragment (for precise retrieval)
        chunk_type="paragraph_full"  full block as-is  (for context retrieval)

    Tables (parsed successfully):
        chunk_type="table_row"    one Document per data row  (precise lookup)
        chunk_type="table_full"   full table raw text         (context/summary)

    Tables (unparseable):
        chunk_type="table_raw"    full table text stored verbatim

    section metadata = "H1 > H2 > H3" breadcrumb.
    Pandoc artifacts ([text](#_Ref...), {#id .class}) are stripped from
    heading text and table cell values before indexing.

    Args:
        md_file:       Path to the .md source file.
        chunk_size:    Maximum characters per prose chunk (default 1500).
        chunk_overlap: Overlap in characters between prose chunks (default 150).

    Returns:
        List of LangChain Document objects ready for embedding.
    """
    source_text  = md_file.read_text(encoding="utf-8", errors="replace")
    source_lines = source_text.splitlines()
    source_name  = md_file.name

    tokens = _md.parse(source_text)

    # heading level → cleaned heading text; shallower levels clear deeper ones
    heading_stack: dict[int, str] = {}

    def _breadcrumb() -> str:
        return " > ".join(heading_stack[lvl] for lvl in sorted(heading_stack))

    # Per (section, chunk_type) sequential counters within the document (for chunk_index)
    _type_counters: dict[tuple[str, str], int] = {}

    def _next_index(section: str, chunk_type: str) -> int:
        key = (section, chunk_type)
        _type_counters[key] = _type_counters.get(key, 0) + 1
        return _type_counters[key]

    def _make_meta(
        bc: str,
        chunk_type: str,
        line_start: int,
        line_end: int,
        chunk_index: int | None = None,
        table_headers: str = "",
    ) -> dict:
        """Build metadata dict with positional fields (1-based line numbers)."""
        idx = chunk_index if chunk_index is not None else _next_index(bc, chunk_type)
        meta: dict = {
            "source":      source_name,
            "section":     bc,
            "chunk_type":  chunk_type,
            # Convert 0-based token.map lines to 1-based file line numbers
            "line_start":  line_start + 1,
            "line_end":    line_end,      # exclusive end, so last line = line_end
            "chunk_index": idx,
            "guid":        str(uuid.uuid4()),
        }
        if table_headers:
            meta["table_headers"] = table_headers
        return meta

    docs: list[Document] = []
    i = 0

    while i < len(tokens):
        tok = tokens[i]

        # ── Headings ──────────────────────────────────────────────────────────
        if tok.type == "heading_open":
            level = int(tok.tag[1])                    # "h2" → 2
            raw_text = _render_inline(tokens[i + 1])   # inline token
            clean = _clean_text(raw_text)
            heading_stack = {k: v for k, v in heading_stack.items() if k < level}
            heading_stack[level] = clean
            i += 3  # heading_open + inline + heading_close
            continue

        # ── GFM pipe tables ───────────────────────────────────────────────────
        if tok.type == "table_open":
            # Collect all tokens until matching table_close
            j, depth = i + 1, 1
            while j < len(tokens):
                if tokens[j].type == "table_open":
                    depth += 1
                elif tokens[j].type == "table_close":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1

            table_tokens = tokens[i: j + 1]
            headers, data_rows = _parse_pipe_table_tokens(table_tokens)
            bc = _breadcrumb()
            ls, le = (tok.map[0], tok.map[1]) if tok.map else (0, 0)
            raw_block = "\n".join(source_lines[ls:le]) if tok.map else ""

            if headers and data_rows:
                # table_full — full raw table for context retrieval
                if raw_block.strip():
                    docs.append(Document(
                        page_content=raw_block,
                        metadata=_make_meta(bc, "table_full", ls, le,
                                            table_headers=json.dumps(headers, ensure_ascii=False)),
                    ))
                # table_row — one doc per data row for precise lookup
                # chunk_index for rows starts after table_full counter
                row_key = (bc, "table_row")
                row_start_idx = _type_counters.get(row_key, 0) + 1
                row_docs = _table_to_docs(
                    headers, data_rows, source_name, bc,
                    line_start=ls + 1, line_end=le,
                    chunk_index_start=row_start_idx - 1,
                )
                _type_counters[row_key] = row_start_idx - 1 + len(row_docs)
                docs.extend(row_docs)
                logger.debug(
                    f"[{source_name}] pipe-table '{bc[:60]}': "
                    f"{len(data_rows)} rows → {len(row_docs)} docs + table_full"
                )
            else:
                if raw_block.strip():
                    docs.append(Document(
                        page_content=raw_block,
                        metadata=_make_meta(bc, "table_raw", ls, le),
                    ))
                    logger.debug(f"[{source_name}] pipe-table unparseable → table_raw, bc='{bc[:60]}'")

            i = j + 1
            continue

        # ── All other block tokens (paragraphs, lists, code, html …) ─────────
        # nesting >= 0: opening (+1) or self-closing (0) blocks carry .map
        if tok.nesting >= 0 and tok.map:
            ls, le = tok.map
            raw_block = "\n".join(source_lines[ls:le]).strip()
            bc = _breadcrumb()

            if raw_block:
                if _is_grid_table(raw_block):
                    # Grid/RST table not recognised by markdown-it
                    block_lines = raw_block.splitlines()
                    headers, data_rows = _parse_grid_table(block_lines)
                    if headers and data_rows:
                        # table_full — full raw table for context retrieval
                        docs.append(Document(
                            page_content=raw_block,
                            metadata=_make_meta(bc, "table_full", ls, le,
                                                table_headers=json.dumps(headers, ensure_ascii=False)),
                        ))
                        # table_row — one doc per data row for precise lookup
                        row_key = (bc, "table_row")
                        row_start_idx = _type_counters.get(row_key, 0) + 1
                        row_docs = _table_to_docs(
                            headers, data_rows, source_name, bc,
                            line_start=ls + 1, line_end=le,
                            chunk_index_start=row_start_idx - 1,
                        )
                        _type_counters[row_key] = row_start_idx - 1 + len(row_docs)
                        docs.extend(row_docs)
                        logger.debug(
                            f"[{source_name}] grid-table '{bc[:60]}': "
                            f"{len(data_rows)} rows → {len(row_docs)} docs + table_full"
                        )
                    else:
                        docs.append(Document(
                            page_content=raw_block,
                            metadata=_make_meta(bc, "table_raw", ls, le),
                        ))
                        logger.debug(f"[{source_name}] grid-table unparseable → table_raw")
                else:
                    # Prose: paragraph_full + size-split chunks
                    clean_block = _clean_text(raw_block)
                    if not clean_block:
                        i += 1
                        continue
                    # paragraph_full — full block for context retrieval
                    docs.append(Document(
                        page_content=clean_block,
                        metadata=_make_meta(bc, "paragraph_full", ls, le),
                    ))
                    # size-split chunks for precise retrieval
                    if len(clean_block) <= chunk_size:
                        docs.append(Document(
                            page_content=clean_block,
                            metadata=_make_meta(bc, "", ls, le),
                        ))
                    else:
                        for sub in _split_text_by_size(clean_block, chunk_size, chunk_overlap):
                            docs.append(Document(
                                page_content=sub,
                                metadata=_make_meta(bc, "", ls, le),
                            ))

        i += 1

    logger.debug(f"{source_name}: {len(docs)} chunks total")
    return docs

