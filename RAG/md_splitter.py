"""Markdown document splitter using markdown-it-py for structural parsing.

Produces LangChain Documents with section > subsection breadcrumbs; the chunk
contract (types, metadata fields, chunk_index scoping) is defined in `chunking`
and shared with `docx_splitter`.

Supports:
  - GFM pipe tables  (| col | col |) via markdown-it-py AST
  - Grid/RST tables  (+----+----+)   via fallback line-based parser
  - Pandoc anchor/attribute artifacts stripped from all text:
      [text](#_Ref...)  ->  text
      (#_Ref...)        ->  (removed)
      {#id .class}      ->  (removed)

Positions: `line_start`/`line_end` are 1-based source file line numbers.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from langchain_core.documents import Document
from markdown_it import MarkdownIt
from markdown_it.token import Token

from chunking import (
    ChunkIndexer,
    SectionStack,
    clean_text,
    normalize_headers,
    prose_to_documents,
    table_rows_to_documents,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inline token rendering
# ---------------------------------------------------------------------------

def _skip_container(tokens: list[Token], open_index: int) -> int:
    """Index of the token closing the container opened at `open_index`.

    Used to consume a block (list, blockquote, paragraph) as a single unit so its
    text is not re-emitted for every nesting level inside it.
    """
    depth = 0
    for idx in range(open_index, len(tokens)):
        depth += tokens[idx].nesting
        if depth == 0:
            return idx
    return len(tokens) - 1


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
    """Extract (headers, data_rows) from a slice of table_open ... table_close tokens."""
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
            current_row.append(clean_text(_render_inline(tok)))

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
    # Separator rows: +----+----+ between data rows and +====+====+ under the header row
    sep_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("+") and ("-" in line or "=" in line)
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
            headers = [clean_text(c) for c in cells]
        elif cells != headers:
            data_rows.append([clean_text(c) for c in cells])

    return headers, data_rows


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

    For each content block two complementary representations are stored: a full
    block (`paragraph_full` / `table_full`) for context retrieval and fine-grained
    fragments (`""` / `table_row`) for precise retrieval. Unparseable tables are
    kept verbatim as `table_raw`.

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

    indexer = ChunkIndexer(source_name)
    sections = SectionStack()
    docs: list[Document] = []
    i = 0

    while i < len(tokens):
        tok = tokens[i]

        # -- Headings ---------------------------------------------------------
        if tok.type == "heading_open":
            level = int(tok.tag[1])                    # "h2" -> 2
            sections.push(level, clean_text(_render_inline(tokens[i + 1])))
            i += 3  # heading_open + inline + heading_close
            continue

        # -- GFM pipe tables --------------------------------------------------
        if tok.type == "table_open":
            j, depth = i + 1, 1
            while j < len(tokens):
                if tokens[j].type == "table_open":
                    depth += 1
                elif tokens[j].type == "table_close":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1

            headers, data_rows = _parse_pipe_table_tokens(tokens[i: j + 1])
            breadcrumb = sections.breadcrumb()
            ls, le = (tok.map[0], tok.map[1]) if tok.map else (0, 0)
            raw_block = "\n".join(source_lines[ls:le]) if tok.map else ""

            if headers and data_rows:
                columns = normalize_headers(headers)
                if raw_block.strip():
                    docs.append(Document(
                        page_content=raw_block,
                        metadata=indexer.meta(
                            breadcrumb, "table_full", ls + 1, le,
                            table_headers=json.dumps(columns, ensure_ascii=False),
                        ),
                    ))
                docs.extend(table_rows_to_documents(
                    columns, data_rows, indexer, breadcrumb, ls + 1, le,
                ))
                logger.debug(
                    f"[{source_name}] pipe-table '{breadcrumb[:60]}': {len(data_rows)} rows"
                )
            elif raw_block.strip():
                docs.append(Document(
                    page_content=raw_block,
                    metadata=indexer.meta(breadcrumb, "table_raw", ls + 1, le),
                ))
                logger.debug(f"[{source_name}] pipe-table unparseable -> table_raw")

            i = j + 1
            continue

        # -- All other block tokens (paragraphs, lists, code, html ...) --------
        # Only top-level blocks are indexed: a container is consumed whole and its
        # inner tokens are skipped, otherwise the same text would be emitted once
        # per nesting level (list -> item -> paragraph -> inline).
        if tok.map and tok.type != "inline" and tok.nesting >= 0:
            block_end = _skip_container(tokens, i) if tok.nesting == 1 else i
            ls, le = tok.map
            raw_block = "\n".join(source_lines[ls:le]).strip()
            breadcrumb = sections.breadcrumb()

            if raw_block and _is_grid_table(raw_block):
                headers, data_rows = _parse_grid_table(raw_block.splitlines())
                if headers and data_rows:
                    columns = normalize_headers(headers)
                    docs.append(Document(
                        page_content=raw_block,
                        metadata=indexer.meta(
                            breadcrumb, "table_full", ls + 1, le,
                            table_headers=json.dumps(columns, ensure_ascii=False),
                        ),
                    ))
                    docs.extend(table_rows_to_documents(
                        columns, data_rows, indexer, breadcrumb, ls + 1, le,
                    ))
                    logger.debug(
                        f"[{source_name}] grid-table '{breadcrumb[:60]}': {len(data_rows)} rows"
                    )
                else:
                    docs.append(Document(
                        page_content=raw_block,
                        metadata=indexer.meta(breadcrumb, "table_raw", ls + 1, le),
                    ))
                    logger.debug(f"[{source_name}] grid-table unparseable -> table_raw")
            elif raw_block:
                docs.extend(prose_to_documents(
                    raw_block, indexer, breadcrumb, ls + 1, le, chunk_size, chunk_overlap,
                ))

            i = block_end + 1
            continue

        i += 1

    logger.debug(f"{source_name}: {len(docs)} chunks total")
    return docs
