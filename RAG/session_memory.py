"""Cross-session memory for RAG agent, stored in ClickHouse.

Saves successful Q&A pairs so future sessions can reuse navigation hints
(which sections to read first, which tool calls worked well).

━━━ Criteria for saving ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. completeness score ≥ min_score (default 4/5) — answer confirmed complete
  2. tool_calls_count ≥ min_tool_calls (default 2)  — non-trivial question
  3. cosine similarity to any existing entry < dedup_sim (default 0.92)
     — prevents saving near-duplicate questions

━━━ What is stored ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  question            — original user question
  question_embedding  — bge-m3 vector (for semantic recall)
  answer              — best final answer from the agent
  sources             — JSON: ["Приложение И.md", ...]
  key_sections        — JSON: [{"source": ..., "section": ...}, ...]
                        sections that were explicitly read (get_section_content calls)
  effective_tools     — JSON: [{"name": ..., "args": {...}}, ...]
                        tool calls from the round that achieved score ≥ 4
  rounds              — how many search rounds were needed
  score               — final completeness score (1–5)
  tool_calls_count    — total tool calls across all rounds

━━━ How it is used ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Before round 1: semantic search finds similar past questions.
  If similarity ≥ recall_sim (default 0.80): a compact hint is prepended
  to the agent's first query:
    • which source files and sections proved useful last time
    • which tool calls worked well
  The agent still searches the live KB — hints only shorten discovery.

━━━ Browsing and editing in ClickHouse ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SELECT id, created_at, question, score, rounds FROM soib_kcoi_v2.agent_memory
  ORDER BY created_at DESC;

  ALTER TABLE soib_kcoi_v2.agent_memory DELETE WHERE id = '...';

  SELECT question, answer FROM soib_kcoi_v2.agent_memory
  WHERE positionCaseInsensitive(question, 'ПО') > 0;
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_DB_SQL = "CREATE DATABASE IF NOT EXISTS {database}"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {database}.{table}
(
    id                UUID    DEFAULT generateUUIDv4(),
    created_at        DateTime64(3) DEFAULT now64(),
    question          String,
    question_embedding Array(Float32),
    answer            String,
    sources           String,
    key_sections      String,
    effective_tools   String,
    rounds            UInt8,
    score             UInt8,
    tool_calls_count  UInt16
)
ENGINE = MergeTree()
ORDER BY (created_at, id)
SETTINGS index_granularity = 8192
"""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class MemoryEntry(BaseModel):
    """A single saved Q&A memory entry (returned from recall)."""

    id: str = ""
    created_at: str = ""
    question: str
    answer: str
    sources: list[str] = Field(default_factory=list)
    key_sections: list[dict[str, str]] = Field(
        default_factory=list,
        description='List of {"source": ..., "section": ...} dicts',
    )
    effective_tools: list[dict[str, Any]] = Field(
        default_factory=list,
        description='List of {"name": ..., "args": {...}} dicts',
    )
    rounds: int = 1
    score: int = 0
    tool_calls_count: int = 0
    similarity: float = Field(default=0.0, description="Cosine similarity to the query (0–1)")


# ---------------------------------------------------------------------------
# SessionMemory
# ---------------------------------------------------------------------------

class SessionMemory:
    """Cross-session Q&A memory backed by ClickHouse.

    Args:
        client:           clickhouse_connect Client instance.
        embedding:        Embeddings model (same as used for KB chunks).
        database:         ClickHouse database name (same as chunks DB).
        table:            Table name for memory (default: agent_memory).
        min_score:        Minimum completeness score to save (default 4).
        min_tool_calls:   Minimum tool calls to consider non-trivial (default 2).
        recall_sim:       Minimum similarity to inject a hint (default 0.80).
        dedup_sim:        Maximum similarity allowed for saving without skip (default 0.92).
    """

    def __init__(
        self,
        client: Any,
        embedding: Embeddings,
        database: str,
        table: str = "agent_memory",
        min_score: int = 4,
        min_tool_calls: int = 2,
        recall_sim: float = 0.80,
        dedup_sim: float = 0.92,
    ) -> None:
        self._client   = client
        self._embedding = embedding
        self._db        = database
        self._tbl       = table
        self.min_score       = min_score
        self.min_tool_calls  = min_tool_calls
        self.recall_sim      = recall_sim
        self.dedup_sim       = dedup_sim
        self._ensure_table()

    # ── Schema ────────────────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        """Create database and table if they don't exist."""
        self._client.command(_CREATE_DB_SQL.format(database=self._db))
        self._client.command(
            _CREATE_TABLE_SQL.format(database=self._db, table=self._tbl)
        )
        logger.debug(f"Memory table {self._db}.{self._tbl} ensured")

    # ── Stats ─────────────────────────────────────────────────────────────

    def count(self) -> int:
        """Return number of entries in the memory table."""
        result = self._client.query(
            f"SELECT count() FROM {self._db}.{self._tbl}"
        )
        return int(result.first_row[0])

    # ── Recall ────────────────────────────────────────────────────────────

    def recall(self, question: str, top_k: int = 3) -> list[MemoryEntry]:
        """Find similar past Q&A entries by semantic similarity.

        Returns entries with similarity ≥ recall_sim, sorted by similarity desc.
        Returns empty list if the memory table is empty.

        Args:
            question: Current user question to match against.
            top_k:    Maximum number of results to return.
        """
        if self.count() == 0:
            return []

        q_emb = self._embedding.embed_query(question)
        # Fetch top_k * 3 candidates then filter — ClickHouse can't filter
        # on an alias inside WHERE, so we use HAVING equivalent via subquery
        sql = f"""
            SELECT
                toString(id)         AS id,
                toString(created_at) AS created_at,
                question, answer, sources, key_sections, effective_tools,
                rounds, score, tool_calls_count,
                1 - cosineDistance(question_embedding, {{qemb:Array(Float32)}}) AS sim
            FROM {self._db}.{self._tbl}
            ORDER BY sim DESC
            LIMIT {{k:UInt32}}
        """
        result = self._client.query(
            sql, parameters={"qemb": q_emb, "k": top_k * 4}
        )

        entries: list[MemoryEntry] = []
        for row in result.result_rows:
            (id_, ts, q, ans, src_json, sec_json, tools_json,
             rounds, score, tc, sim) = row
            if float(sim) < self.recall_sim:
                continue
            entries.append(MemoryEntry(
                id=str(id_),
                created_at=str(ts),
                question=q,
                answer=ans,
                sources=json.loads(src_json) if src_json else [],
                key_sections=json.loads(sec_json) if sec_json else [],
                effective_tools=json.loads(tools_json) if tools_json else [],
                rounds=int(rounds),
                score=int(score),
                tool_calls_count=int(tc),
                similarity=round(float(sim), 3),
            ))

        logger.info(
            f"Memory recall: '{question[:60]}' → {len(entries)} похожих записей "
            f"(порог {self.recall_sim:.0%})"
        )
        return entries[:top_k]

    # ── Deduplication check ───────────────────────────────────────────────

    def _is_duplicate(self, q_emb: list[float]) -> bool:
        """Return True if a very similar question already exists in memory."""
        if self.count() == 0:
            return False
        sql = f"""
            SELECT 1 - cosineDistance(question_embedding, {{qemb:Array(Float32)}}) AS sim
            FROM {self._db}.{self._tbl}
            ORDER BY sim DESC
            LIMIT 1
        """
        rows = self._client.query(sql, parameters={"qemb": q_emb}).result_rows
        return bool(rows) and float(rows[0][0]) >= self.dedup_sim

    # ── Save ──────────────────────────────────────────────────────────────

    def save(
        self,
        question: str,
        answer: str,
        sources: list[str],
        key_sections: list[dict[str, str]],
        effective_tools: list[dict[str, Any]],
        rounds: int,
        score: int,
        tool_calls_count: int,
    ) -> bool:
        """Save a Q&A pair to memory if it passes all criteria.

        Criteria (all must be true):
          1. score >= min_score
          2. tool_calls_count >= min_tool_calls
          3. no near-duplicate entry (similarity < dedup_sim)

        Returns:
            True if saved, False if skipped.
        """
        if score < self.min_score:
            logger.debug(
                f"Memory: skip (score {score} < {self.min_score}) — '{question[:60]}'"
            )
            return False

        if tool_calls_count < self.min_tool_calls:
            logger.debug(
                f"Memory: skip (tool_calls {tool_calls_count} < {self.min_tool_calls})"
            )
            return False

        q_emb = self._embedding.embed_query(question)

        if self._is_duplicate(q_emb):
            logger.info(
                f"Memory: skip — near-duplicate exists for: '{question[:80]}'"
            )
            return False

        self._client.insert(
            f"{self._db}.{self._tbl}",
            [[
                q_emb,
                question,
                answer,
                json.dumps(sources, ensure_ascii=False),
                json.dumps(key_sections, ensure_ascii=False),
                json.dumps(effective_tools, ensure_ascii=False),
                rounds,
                score,
                tool_calls_count,
            ]],
            column_names=[
                "question_embedding", "question", "answer",
                "sources", "key_sections", "effective_tools",
                "rounds", "score", "tool_calls_count",
            ],
        )
        logger.info(
            f"Memory: saved '{question[:80]}'\n"
            f"  score={score}, rounds={rounds}, tool_calls={tool_calls_count}\n"
            f"  key_sections={len(key_sections)}, effective_tools={len(effective_tools)}"
        )
        return True


# ---------------------------------------------------------------------------
# Hint formatting (for injection into agent's first query)
# ---------------------------------------------------------------------------

_HINT_SEP = "━" * 52


def format_memory_hint(entries: list[MemoryEntry]) -> str:
    """Format recalled entries as a compact navigation hint for the agent.

    The hint is prepended to the agent's round-1 query so it knows which
    sections and tool calls are likely to be productive for this question.
    The agent is explicitly told to NOT blindly reuse the old answer.

    Args:
        entries: List of MemoryEntry sorted by similarity descending.

    Returns:
        Formatted hint string, or empty string if no entries.
    """
    if not entries:
        return ""

    parts: list[str] = [
        _HINT_SEP,
        "💡 ПОДСКАЗКА ИЗ ДОЛГОСРОЧНОЙ ПАМЯТИ",
        "Похожие вопросы уже задавались. Используй как ориентир, но ищи в KB свежие данные.",
    ]

    for i, e in enumerate(entries, 1):
        parts.append(f"\n[{i}] Похожий вопрос (сходство {e.similarity:.0%}): {e.question[:120]}")
        parts.append(f"    Найдено за {e.rounds} раунд(а), оценка {e.score}/5")

        if e.key_sections:
            parts.append("    Разделы которые сработали:")
            for s in e.key_sections[:4]:
                src = s.get("source", "?")
                sec = s.get("section", "?")
                parts.append(f"      → [{src}] — {sec}")

        if e.effective_tools:
            tool_strs = [
                f'{t["name"]}({json.dumps(t.get("args", {}), ensure_ascii=False)[:80]})'
                for t in e.effective_tools[:5]
            ]
            parts.append(f"    Запросы: {'; '.join(tool_strs)}")

    parts += [
        "",
        "⚠️  Не копируй старый ответ напрямую — данные могли измениться.",
        _HINT_SEP,
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"\[([^\[\]]+\.md)\] — ([^\n\[{]+)")


def extract_key_sections(tool_outputs: list[str]) -> list[dict[str, str]]:
    """Extract unique (source, section) pairs from tool output strings.

    Prioritises sections accessed via get_section_content calls
    (already present in outputs as '[file] — section' headers).

    Args:
        tool_outputs: List of raw tool output strings.

    Returns:
        Unique list of {"source": ..., "section": ...} dicts (max 10).
    """
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for text in tool_outputs:
        for m in _SECTION_RE.finditer(text):
            src = m.group(1).strip()
            sec = m.group(2).strip().rstrip(" >-")
            key = (src, sec)
            if key not in seen and sec:
                seen.add(key)
                result.append({"source": src, "section": sec})
    return result[:10]


def extract_effective_tools(
    round_calls: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Convert round_calls list to structured dicts for storage.

    Args:
        round_calls: List of (tool_name, args_json_str) from the winning round.

    Returns:
        List of {"name": ..., "args": {...}} dicts (max 8).
    """
    result: list[dict[str, Any]] = []
    for name, args_str in round_calls[:8]:
        try:
            args = json.loads(args_str)
        except Exception:
            args = {"raw": args_str[:100]}
        result.append({"name": name, "args": args})
    return result

