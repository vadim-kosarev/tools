"""Text normalization utilities for embedding generation.

normalize_for_embedding() converts raw stored content into clean meaningful
text before it is passed to an embedding model:

  - table_row JSON array  → cell values joined with space
  - Markdown table markup → removed (|, +, ---, ===)
  - Newlines / tabs       → single space
  - Punctuation           → removed (replaced with space)
  - IP addresses          → preserved with their dots (192.168.1.1/24 kept as-is)
  - Multiple spaces       → collapsed to one
"""
from __future__ import annotations

import re
import json

# Matches IPv4 with optional CIDR prefix (e.g. 10.0.0.1, 192.168.0.0/24)
_IP_RE = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?\b')

# Markdown table separator lines: |---|---| or +---+---+ or ===...
_MD_TABLE_SEP_RE = re.compile(r'^[\s|+\-=:]+$', re.MULTILINE)

# Markdown pipe / grid delimiters
_MD_PIPE_RE = re.compile(r'[|+]')

# Punctuation: anything that is not a word character (Unicode), space, or
# the IP placeholder underscore — will be replaced with a space.
_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)


def normalize_for_embedding(text: str) -> str:
    """Return a clean, embedding-friendly representation of stored content.

    Handles three content formats transparently:
      - JSON array (table_row): cell values joined with space
      - Raw markdown table (table_full / table_raw): markup stripped
      - Plain prose text: whitespace / punctuation normalised

    IP addresses (e.g. 10.0.0.1, 192.168.0.0/24) are preserved intact.

    Args:
        text: Raw page_content value as stored in the chunks table.

    Returns:
        Normalised plain-text string ready for embedding.
    """
    # ── 1. Unpack JSON cell array (table_row) ─────────────────────────────────
    stripped = text.strip()
    if stripped.startswith('['):
        try:
            cells = json.loads(stripped)
            if isinstance(cells, list):
                text = ' '.join(str(c) for c in cells if c)
        except (json.JSONDecodeError, TypeError):
            pass  # fall through to generic handling

    # ── 2. Remove markdown table separator lines (|---|, +---+, ====) ─────────
    text = _MD_TABLE_SEP_RE.sub(' ', text)

    # ── 3. Remove markdown pipe/grid cell delimiters ──────────────────────────
    text = _MD_PIPE_RE.sub(' ', text)

    # ── 4. Normalise whitespace (newlines, tabs → space) ─────────────────────
    text = text.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')

    # ── 5. Protect IP addresses with placeholders before removing punctuation ─
    ip_map: dict[str, str] = {}
    def _protect_ip(m: re.Match) -> str:
        ip = m.group(0)
        placeholder = f'__IP{len(ip_map)}__'
        ip_map[placeholder] = ip
        return placeholder

    text = _IP_RE.sub(_protect_ip, text)

    # ── 6. Remove punctuation (replace with space, keep word chars + spaces) ──
    text = _PUNCT_RE.sub(' ', text)

    # ── 7. Restore IP addresses ───────────────────────────────────────────────
    for placeholder, ip in ip_map.items():
        text = text.replace(placeholder, ip)

    # ── 8. Collapse multiple spaces ───────────────────────────────────────────
    text = re.sub(r' {2,}', ' ', text).strip()

    return text

