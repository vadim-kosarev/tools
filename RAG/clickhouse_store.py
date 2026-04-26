"""ClickHouse-backed vector store for RAG chunks.

Implements LangChain VectorStore interface over ClickHouse using
clickhouse-connect. Embeddings stored as Array(Float32); similarity
search done via cosineDistance function.

Table schema (created automatically):
    id            UUID                    -- random UUID (row identifier)
    source        String                  -- source .md filename
    section       String                  -- breadcrumb "H1 > H2 > H3"
    chunk_type    LowCardinality(String)  -- "", "table_row", "table_full", etc.
    table_headers String                  -- JSON array of header strings, e.g. ["h1","h2"] (table chunks only)
    content       String                  -- page_content
    embedding     Array(Float32)          -- embedding vector
    line_start    UInt32                  -- 1-based first line in source file
    line_end      UInt32                  -- last line (exclusive) in source file
    chunk_index   UInt32                  -- sequential index of this chunk_type in the document

Engine: ReplacingMergeTree ORDER BY (source, section, chunk_type, cityHash64(content))
    Deduplication key = (source, section, chunk_type, content hash).
    Duplicate chunks produce the same ORDER BY key, so ReplacingMergeTree
    collapses them on merge. All SELECT queries use FINAL to force immediate
    deduplication before merge occurs.

Embeddings:
    Both at index time and query time the text is passed through
    normalize_for_embedding() (see text_utils.py) before encoding:
      - JSON cell arrays (table_row) are unpacked to plain text
      - Markdown table delimiters stripped
      - Punctuation removed (IP addresses preserved)
      - Whitespace collapsed
    Raw original content is stored in the `content` column unchanged.
"""
from __future__ import annotations

import uuid
import logging
from typing import Any, Iterable, Optional

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from pydantic import BaseModel
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from text_utils import normalize_for_embedding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class ClickHouseStoreSettings(BaseModel):
    """Connection and table settings for ClickHouseVectorStore."""
    host:         str = "localhost"
    port:         int = 8123
    username:     str = "clickhouse"
    password:     str = "clickhouse"
    database:     str = "soib_kcoi_v2"
    table:        str = "chunks"
    pool_maxsize: int = 16   # urllib3 connection pool size; increase for many parallel tool calls

    model_config = {"env_prefix": "CLICKHOUSE_"}


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_DB_SQL = "CREATE DATABASE IF NOT EXISTS {database}"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {database}.{table}
(
    id            UUID,
    source        String,
    section       String,
    chunk_type    LowCardinality(String),
    table_headers String,
    content       String,
    embedding     Array(Float32),
    line_start    UInt32,
    line_end      UInt32,
    chunk_index   UInt32
)
ENGINE = ReplacingMergeTree()
ORDER BY (source, section, chunk_type, cityHash64(content))
"""

_DROP_TABLE_SQL = "DROP TABLE IF EXISTS {database}.{table}"


# ---------------------------------------------------------------------------
# ClickHouseVectorStore
# ---------------------------------------------------------------------------

class ClickHouseVectorStore(VectorStore):
    """LangChain VectorStore backed by ClickHouse.

    Supports:
        - Batch insert of LangChain Documents with embeddings
        - Semantic similarity search via cosineDistance
        - Metadata filtering by chunk_type (WHERE clause)
        - Exact substring search (positionCaseInsensitive)
        - Raw scan with optional filter (for diagnostic scripts)
    """

    def __init__(
        self,
        client: Client,
        embedding: Embeddings,
        cfg: ClickHouseStoreSettings,
    ) -> None:
        self._client = client
        self._embedding = embedding
        self._cfg = cfg

    # ── Schema management ────────────────────────────────────────────────────

    def create_table(self) -> None:
        """Create database and table if they don't exist."""
        self._client.command(_CREATE_DB_SQL.format(database=self._cfg.database))
        self._client.command(
            _CREATE_TABLE_SQL.format(database=self._cfg.database, table=self._cfg.table)
        )
        logger.debug(f"Table {self._cfg.database}.{self._cfg.table} ensured")

    def drop_table(self) -> None:
        """Drop the chunks table (for full reindex)."""
        self._client.command(
            _DROP_TABLE_SQL.format(database=self._cfg.database, table=self._cfg.table)
        )
        logger.info(f"Table {self._cfg.database}.{self._cfg.table} dropped")

    def count(self) -> int:
        """Return number of rows in the chunks table (deduplicated via FINAL)."""
        result = self._client.query(
            f"SELECT count() FROM {self._cfg.database}.{self._cfg.table} FINAL"
        )
        return int(result.first_row[0])

    # ── Insert ────────────────────────────────────────────────────────────────

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[list[dict]] = None,
        **kwargs: Any,
    ) -> list[str]:
        """Embed texts and insert into ClickHouse.

        Args:
            texts:     Iterable of page_content strings.
            metadatas: Optional list of metadata dicts (one per text).

        Returns:
            List of generated UUIDs for inserted rows.
        """
        texts_list = list(texts)
        if not texts_list:
            return []
        metadatas = metadatas or [{} for _ in texts_list]

        logger.debug(f"Embedding {len(texts_list)} texts for ClickHouse insert")
        norm_texts = [normalize_for_embedding(t) for t in texts_list]
        vectors = self._embedding.embed_documents(norm_texts)

        rows: list[list] = []
        ids: list[str] = []
        for text, meta, vec in zip(texts_list, metadatas, vectors):
            row_id = str(uuid.uuid4())
            ids.append(row_id)
            rows.append([
                row_id,
                meta.get("source", ""),
                meta.get("section", ""),
                meta.get("chunk_type", ""),
                meta.get("table_headers", ""),
                text,
                vec,
                int(meta.get("line_start", 0)),
                int(meta.get("line_end", 0)),
                int(meta.get("chunk_index", 0)),
            ])

        self._client.insert(
            f"{self._cfg.database}.{self._cfg.table}",
            rows,
            column_names=["id", "source", "section", "chunk_type", "table_headers",
                          "content", "embedding", "line_start", "line_end", "chunk_index"],
        )
        logger.debug(f"Inserted {len(rows)} rows into ClickHouse")
        return ids

    def add_documents(self, documents: list[Document], **kwargs: Any) -> list[str]:
        """Insert LangChain Documents into ClickHouse."""
        texts = [d.page_content for d in documents]
        metas = [d.metadata for d in documents]
        return self.add_texts(texts, metas, **kwargs)

    # ── Search ────────────────────────────────────────────────────────────────

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
        section: Optional[str] = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Return top-k Documents most similar to query (cosine distance).

        Args:
            query:      Natural language query string.
            k:          Number of results to return.
            chunk_type: Optional filter on chunk_type metadata field.
            source:     Optional source file filter.
            section:    Optional section substring filter.
        """
        docs_scores = self.similarity_search_with_score(
            query, k=k, chunk_type=chunk_type, source=source, section=section
        )
        return [doc for doc, _ in docs_scores]

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
        section: Optional[str] = None,
        **kwargs: Any,
    ) -> list[tuple[Document, float]]:
        """Return top-k (Document, distance) pairs for query.

        The query is normalised with normalize_for_embedding() before encoding
        so the embedding space matches the indexed content.
        """
        query_vec = self._embedding.embed_query(normalize_for_embedding(query))
        return self.similarity_search_by_vector_with_score(
            query_vec, k=k, chunk_type=chunk_type, source=source, section=section
        )

    def similarity_search_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
        section: Optional[str] = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search by pre-computed embedding vector."""
        return [
            doc for doc, _ in self.similarity_search_by_vector_with_score(
                embedding, k, chunk_type, source, section
            )
        ]

    def similarity_search_by_vector_with_score(
        self,
        embedding: list[float],
        k: int = 4,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
        section: Optional[str] = None,
    ) -> list[tuple[Document, float]]:
        """Core vector search: cosineDistance ORDER BY ASC LIMIT k."""
        where_clauses = []
        params: dict[str, Any] = {"query_vec": embedding, "k": k}
        
        if chunk_type is not None:
            where_clauses.append("chunk_type = {ct:String}")
            params["ct"] = chunk_type
        
        if source is not None:
            where_clauses.append("source = {src:String}")
            params["src"] = source
        
        if section is not None:
            where_clauses.append("positionCaseInsensitive(section, {sec:String}) > 0")
            params["sec"] = section
        
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index,
                   cosineDistance(embedding, {{query_vec:Array(Float32)}}) AS distance
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            {where}
            ORDER BY distance ASC
            LIMIT {{k:UInt32}}
        """
        result = self._client.query(sql, parameters=params)
        docs: list[tuple[Document, float]] = []
        for row in result.result_rows:
            source, section, chunk_type_val, table_headers, content, line_start, line_end, chunk_index, distance = row
            meta: dict = {
                "source":      source,
                "section":     section,
                "chunk_type":  chunk_type_val,
                "line_start":  int(line_start),
                "line_end":    int(line_end),
                "chunk_index": int(chunk_index),
            }
            if table_headers:
                meta["table_headers"] = table_headers
            docs.append((Document(page_content=content, metadata=meta), float(distance)))
        return docs

    def exact_search(
        self,
        substring: str,
        limit: int = 100,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
        section: Optional[str] = None,
    ) -> list[Document]:
        """Case-insensitive exact substring search using positionCaseInsensitive.

        Args:
            substring:  Text to search for in content.
            limit:      Maximum number of results.
            chunk_type: Optional chunk_type filter.
            source:     Optional source file filter.
            section:    Optional section substring filter.
        """
        where_clauses = ["positionCaseInsensitive(content, {sub:String}) > 0"]
        params: dict[str, Any] = {"sub": substring, "lim": limit}
        
        if chunk_type is not None:
            where_clauses.append("chunk_type = {ct:String}")
            params["ct"] = chunk_type
        
        if source is not None:
            where_clauses.append("source = {src:String}")
            params["src"] = source
        
        if section is not None:
            where_clauses.append("positionCaseInsensitive(section, {sec:String}) > 0")
            params["sec"] = section

        order_by = "ORDER BY line_start, chunk_index" if source else ""
        
        sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            WHERE {" AND ".join(where_clauses)}
            {order_by}
            LIMIT {{lim:UInt32}}
        """

        result = self._client.query(sql, parameters=params)
        docs: list[Document] = []
        for row in result.result_rows:
            source, section, chunk_type_val, table_headers, content, line_start, line_end, chunk_index = row
            meta: dict = {
                "source":      source,
                "section":     section,
                "chunk_type":  chunk_type_val,
                "line_start":  int(line_start),
                "line_end":    int(line_end),
                "chunk_index": int(chunk_index),
            }
            if table_headers:
                meta["table_headers"] = table_headers
            docs.append(Document(page_content=content, metadata=meta))
        return docs

    def exact_search_in_file(
        self,
        substring: str,
        source_file: str,
        limit: int = 100,
        chunk_type: Optional[str] = None,
    ) -> list[Document]:
        """Case-insensitive exact substring search within a specific file.

        Args:
            substring:   Text to search for in content.
            source_file: Source filename to search in.
            limit:       Maximum number of results.
            chunk_type:  Optional chunk_type filter.
        """
        where_clauses = [
            "positionCaseInsensitive(content, {sub:String}) > 0",
            "source = {file:String}"
        ]
        if chunk_type is not None:
            where_clauses.append("chunk_type = {ct:String}")

        sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            WHERE {" AND ".join(where_clauses)}
            ORDER BY line_start, chunk_index
            LIMIT {{lim:UInt32}}
        """
        params: dict[str, Any] = {"sub": substring, "file": source_file, "lim": limit}
        if chunk_type is not None:
            params["ct"] = chunk_type

        result = self._client.query(sql, parameters=params)
        docs: list[Document] = []
        for row in result.result_rows:
            source, section, chunk_type_val, table_headers, content, line_start, line_end, chunk_index = row
            meta: dict = {
                "source":      source,
                "section":     section,
                "chunk_type":  chunk_type_val,
                "line_start":  int(line_start),
                "line_end":    int(line_end),
                "chunk_index": int(chunk_index),
            }
            if table_headers:
                meta["table_headers"] = table_headers
            docs.append(Document(page_content=content, metadata=meta))
        return docs

    def exact_search_in_file_section(
        self,
        substring: str,
        source_file: str,
        section_substring: str,
        limit: int = 100,
        chunk_type: Optional[str] = None,
    ) -> list[Document]:
        """Case-insensitive exact substring search within a specific file and section.

        Args:
            substring:          Text to search for in content.
            source_file:        Source filename to search in.
            section_substring:  Section name or breadcrumb substring to match.
            limit:              Maximum number of results.
            chunk_type:         Optional chunk_type filter.
        """
        where_clauses = [
            "positionCaseInsensitive(content, {sub:String}) > 0",
            "source = {file:String}",
            "positionCaseInsensitive(section, {sec:String}) > 0"
        ]
        if chunk_type is not None:
            where_clauses.append("chunk_type = {ct:String}")

        sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            WHERE {" AND ".join(where_clauses)}
            ORDER BY line_start, chunk_index
            LIMIT {{lim:UInt32}}
        """
        params: dict[str, Any] = {
            "sub": substring,
            "file": source_file,
            "sec": section_substring,
            "lim": limit
        }
        if chunk_type is not None:
            params["ct"] = chunk_type

        result = self._client.query(sql, parameters=params)
        docs: list[Document] = []
        for row in result.result_rows:
            source, section, chunk_type_val, table_headers, content, line_start, line_end, chunk_index = row
            meta: dict = {
                "source":      source,
                "section":     section,
                "chunk_type":  chunk_type_val,
                "line_start":  int(line_start),
                "line_end":    int(line_end),
                "chunk_index": int(chunk_index),
            }
            if table_headers:
                meta["table_headers"] = table_headers
            docs.append(Document(page_content=content, metadata=meta))
        return docs

    def multi_term_exact_search(
        self,
        terms: list[str],
        limit: int = 100,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
        section: Optional[str] = None,
    ) -> list[tuple[Document, int]]:
        """Multi-term exact search with per-chunk match-count scoring.

        Each term is checked independently via positionCaseInsensitive.
        The SQL expression sums per-term boolean hits to produce a match_count
        for every chunk.  Results are sorted by match_count DESC so chunks
        containing the most terms appear first.

        Used to implement the search strategy:
          1. All terms at once (match_count == len(terms))
          2. Most terms (match_count >= threshold)
          3. Any single term (match_count >= 1)
        — all in a single round-trip to ClickHouse.

        Args:
            terms:      List of substrings to search (case-insensitive).
            limit:      Maximum number of results to return.
            chunk_type: Optional chunk_type filter.
            source:     Optional source file filter.
            section:    Optional section substring filter.

        Returns:
            List of (Document, match_count) sorted by match_count DESC.
            match_count is the number of terms found in the chunk's content.
        """
        if not terms:
            return []

        # Build: sum of per-term boolean hits as match_count
        match_expr = " + ".join(
            f"(positionCaseInsensitive(content, {{t{i}:String}}) > 0)"
            for i in range(len(terms))
        )

        where_clauses = [f"({match_expr}) > 0"]
        params: dict[str, Any] = {f"t{i}": term for i, term in enumerate(terms)}
        params["lim"] = limit
        
        if chunk_type is not None:
            where_clauses.append("chunk_type = {ct:String}")
            params["ct"] = chunk_type
        
        if source is not None:
            where_clauses.append("source = {src:String}")
            params["src"] = source
        
        if section is not None:
            where_clauses.append("positionCaseInsensitive(section, {sec:String}) > 0")
            params["sec"] = section

        sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index,
                   {match_expr} AS match_count
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            WHERE {" AND ".join(where_clauses)}
            ORDER BY match_count DESC
            LIMIT {{lim:UInt32}}
        """

        result = self._client.query(sql, parameters=params)
        docs: list[tuple[Document, int]] = []
        for row in result.result_rows:
            source, section, ct_val, table_headers, content, line_start, line_end, chunk_index, match_count = row
            meta: dict = {
                "source":      source,
                "section":     section,
                "chunk_type":  ct_val,
                "line_start":  int(line_start),
                "line_end":    int(line_end),
                "chunk_index": int(chunk_index),
            }
            if table_headers:
                meta["table_headers"] = table_headers
            docs.append((Document(page_content=content, metadata=meta), int(match_count)))
        return docs

    def exact_search_sections(
        self,
        substring: str,
        limit: int = 100,
        chunk_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[tuple[str, str, int]]:
        """Find all unique (source, section) pairs containing the substring.

        Returns a list of sections where the term was found, with match counts.
        Useful for discovering which sections contain relevant information
        before doing detailed searches.

        Args:
            substring:  Text to search for in content.
            limit:      Maximum number of chunks to scan (default 100).
            chunk_type: Optional chunk_type filter.
            source:     Optional source file filter.

        Returns:
            List of (source, section, match_count) tuples sorted by match_count DESC.
            match_count = number of chunks in that section containing the term.
        """
        where_clauses = ["positionCaseInsensitive(content, {sub:String}) > 0"]
        params: dict[str, Any] = {"sub": substring, "lim": limit}

        if chunk_type is not None:
            where_clauses.append("chunk_type = {ct:String}")
            params["ct"] = chunk_type

        if source is not None:
            where_clauses.append("source = {src:String}")
            params["src"] = source

        # Subquery: get top limit chunks, then extract unique (source, section) with counts
        sql = f"""
            SELECT source, section, count() AS match_count
            FROM (
                SELECT source, section
                FROM {self._cfg.database}.{self._cfg.table} FINAL
                WHERE {" AND ".join(where_clauses)}
                LIMIT {{lim:UInt32}}
            )
            GROUP BY source, section
            ORDER BY match_count DESC, source, section
        """

        result = self._client.query(sql, parameters=params)
        sections: list[tuple[str, str, int]] = []
        for row in result.result_rows:
            src, sec, cnt = row
            sections.append((src, sec if sec else "<root>", int(cnt)))
        return sections

    def find_sections_by_name(
        self,
        name_substring: str,
        source: Optional[str] = None,
    ) -> list[tuple[str, str, int]]:
        """Find sections where the section name contains the substring.

        Searches in section names/breadcrumbs, not in content.
        Useful for finding sections that match the user's query by title.

        Args:
            name_substring: Substring to search in section names (case-insensitive).
            source:         Optional source file filter.

        Returns:
            List of (source, section, chunk_count) tuples.
            chunk_count = total number of chunks in that section.
        """
        where_clauses = ["positionCaseInsensitive(section, {name:String}) > 0"]
        params: dict[str, Any] = {"name": name_substring}

        if source is not None:
            where_clauses.append("source = {src:String}")
            params["src"] = source

        sql = f"""
            SELECT source, section, count() AS chunk_count
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            WHERE {" AND ".join(where_clauses)}
            GROUP BY source, section
            ORDER BY chunk_count DESC, source, section
        """

        result = self._client.query(sql, parameters=params)
        sections: list[tuple[str, str, int]] = []
        for row in result.result_rows:
            src, sec, cnt = row
            sections.append((src, sec if sec else "<root>", int(cnt)))
        return sections


    def get_sample(
        self,
        limit: int = 15,
        chunk_type: Optional[str] = None,
    ) -> list[Document]:
        """Return a random sample of documents, optionally filtered by chunk_type.

        Args:
            limit:      Number of rows to return.
            chunk_type: Optional chunk_type filter.
        """
        where = f"WHERE chunk_type = '{{ct:String}}'" if chunk_type is not None else ""
        sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index
            FROM {self._cfg.database}.{self._cfg.table} FINAL
            {where}
            ORDER BY rand()
            LIMIT {{lim:UInt32}}
        """
        params: dict[str, Any] = {"lim": limit}
        if chunk_type is not None:
            params["ct"] = chunk_type

        result = self._client.query(sql, parameters=params)
        docs: list[Document] = []
        for row in result.result_rows:
            source, section, chunk_type_val, table_headers, content, line_start, line_end, chunk_index = row
            meta: dict = {
                "source":      source,
                "section":     section,
                "chunk_type":  chunk_type_val,
                "line_start":  int(line_start),
                "line_end":    int(line_end),
                "chunk_index": int(chunk_index),
            }
            if table_headers:
                meta["table_headers"] = table_headers
            docs.append(Document(page_content=content, metadata=meta))
        return docs

    def get_neighbor_chunks(
        self,
        source: str,
        line_start: int,
        before: int = 5,
        after: int = 5,
    ) -> list[Document]:
        """Return up to `before` chunks preceding and `after` chunks following
        the chunk at `line_start` within the same `source` file, ordered by
        line_start ASC.

        Args:
            source:     Source filename (exact match).
            line_start: Reference line position of the anchor chunk.
            before:     Number of preceding chunks to fetch.
            after:      Number of following chunks to fetch.
        """
        db, tbl = self._cfg.database, self._cfg.table

        def _fetch(sql: str, params: dict) -> list[Document]:
            result = self._client.query(sql, parameters=params)
            docs: list[Document] = []
            for row in result.result_rows:
                src, sec, ct, th, content, ls, le, ci = row
                meta: dict = {
                    "source":      src,
                    "section":     sec,
                    "chunk_type":  ct,
                    "line_start":  int(ls),
                    "line_end":    int(le),
                    "chunk_index": int(ci),
                }
                if th:
                    meta["table_headers"] = th
                docs.append(Document(page_content=content, metadata=meta))
            return docs

        prev_sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index
            FROM {db}.{tbl} FINAL
            WHERE source = {{src:String}}
              AND line_start < {{ls:UInt32}}
            ORDER BY line_start DESC
            LIMIT {{n:UInt32}}
        """
        next_sql = f"""
            SELECT source, section, chunk_type, table_headers, content,
                   line_start, line_end, chunk_index
            FROM {db}.{tbl} FINAL
            WHERE source = {{src:String}}
              AND line_start > {{ls:UInt32}}
            ORDER BY line_start ASC
            LIMIT {{n:UInt32}}
        """

        params_prev = {"src": source, "ls": line_start, "n": before}
        params_next = {"src": source, "ls": line_start, "n": after}

        # prev fetched DESC → reverse to get chronological order
        prev_docs = list(reversed(_fetch(prev_sql, params_prev)))
        next_docs = _fetch(next_sql, params_next)
        return prev_docs + next_docs

    # ── LangChain abstract classmethod ────────────────────────────────────────

    def clone(self) -> "ClickHouseVectorStore":
        """Return a new store with a fresh client connection.

        ClickHouse HTTP client is not thread-safe for concurrent queries within
        the same session.  Call clone() per thread to get an independent client.
        """
        return ClickHouseVectorStore(
            client=_make_client(self._cfg),
            embedding=self._embedding,
            cfg=self._cfg,
        )

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: Optional[list[dict]] = None,
        cfg: Optional[ClickHouseStoreSettings] = None,
        **kwargs: Any,
    ) -> "ClickHouseVectorStore":
        """Create store, ensure schema, and insert texts."""
        cfg = cfg or ClickHouseStoreSettings()
        client = _make_client(cfg)
        store = cls(client=client, embedding=embedding, cfg=cfg)
        store.create_table()
        store.add_texts(texts, metadatas)
        return store


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_client(cfg: ClickHouseStoreSettings) -> Client:
    """Create a clickhouse-connect Client from settings.

    pool_mgr is set explicitly to prevent urllib3 "Connection pool is full"
    warnings when the agent makes many parallel tool calls to ClickHouse.
    Since clickhouse-connect >= 0.8 pool_maxsize was replaced by pool_mgr
    (urllib3.PoolManager).
    """
    import urllib3
    return clickhouse_connect.get_client(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
        pool_mgr=urllib3.PoolManager(maxsize=cfg.pool_maxsize),
    )


def build_store(
    cfg: ClickHouseStoreSettings,
    embedding: Embeddings,
    documents: Optional[list[Document]] = None,
    force_reindex: bool = False,
) -> ClickHouseVectorStore:
    """Create or connect to a ClickHouseVectorStore.

    Args:
        cfg:           Connection + table settings.
        embedding:     Embeddings instance for encoding queries and documents.
        documents:     Documents to index (required when force_reindex=True or table is empty).
        force_reindex: If True, drop and recreate the table before indexing.

    Returns:
        Ready-to-use ClickHouseVectorStore.
    """
    client = _make_client(cfg)
    store = ClickHouseVectorStore(client=client, embedding=embedding, cfg=cfg)

    # Ensure database and table exist
    store.create_table()

    if force_reindex:
        store.drop_table()
        store.create_table()
        logger.info(f"Table {cfg.database}.{cfg.table} recreated for reindex")

    if documents:
        logger.info(f"Inserting {len(documents)} documents into ClickHouse")
        batch_size = 100
        total = 0
        for i in range(0, len(documents), batch_size):
            batch = documents[i: i + batch_size]
            store.add_documents(batch)
            total += len(batch)
            logger.info(f"  Inserted {total}/{len(documents)} chunks")
        logger.info(f"Indexing complete: {total} chunks stored")

    return store

