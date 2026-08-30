# -*- coding: utf-8 -*-
"""Video Search sidecar: fast text search over Frigate events.

Frigate's own /api/events/search is very slow for text queries (40-65s) because
jina-clip-v2 is exported as a single combined text+vision ONNX graph and Frigate
runs a full vision-tower forward pass over a dummy blank image on every text query.

This service keeps a copy of the thumbnail embeddings (already computed by Frigate,
stored in frigate.db's sqlite-vec vec_thumbnails table) in Postgres+pgvector, and
computes the text-query embedding directly via jina-clip-v2's native text-only path
(transformers, trust_remote_code) - no dummy image involved.
"""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("video_search_api")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Video Search API for Frigate")
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--env", type=str, default=None, help="Path to .env file")
    parser.add_argument("--port", type=int, default=8768, help="Server port (default: 8768)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host")
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
else:
    _args = argparse.Namespace(debug=False, env=None, port=8768, host="0.0.0.0")

# ---------------------------------------------------------------------------
import functools
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator, Optional

import httpx
import numpy as np
import psycopg2
import psycopg2.extras
import sqlite_vec
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response

_SCRIPT_DIR = Path(__file__).parent
_env_file = _args.env or str(_SCRIPT_DIR / ".env")
if not os.path.exists(_env_file):
    _env_file = ".env"
load_dotenv(_env_file, override=False)

LOG_LEVEL = "DEBUG" if _args.debug else os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
)
logger.setLevel(LOG_LEVEL)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "frigate")
POSTGRES_USER = os.getenv("POSTGRES_USER", "rgzz")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "rgzz")
GRANT_SELECT_ROLE = os.getenv("GRANT_SELECT_ROLE", "frigate")

FRIGATE_DB_PATH = os.getenv("FRIGATE_DB_PATH", "/frigate-config/frigate.db")
FRIGATE_INTERNAL_URL = os.getenv("FRIGATE_INTERNAL_URL", "http://frigate:5000")
SYNC_INTERVAL_SEC = int(os.getenv("SYNC_INTERVAL_SEC", "60"))
SYNC_BATCH_SIZE = int(os.getenv("SYNC_BATCH_SIZE", "1000"))

EMBED_MODEL_NAME = "jinaai/jina-clip-v2"
EMBED_DIM = 768

# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------


@contextmanager
def _pg_conn() -> Generator[Any, None, None]:
    conn = psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD,
    )
    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema() -> None:
    with _pg_conn() as conn:
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS video_search")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS video_search.events (
                id               TEXT PRIMARY KEY,
                camera           TEXT NOT NULL,
                label            TEXT NOT NULL,
                start_time       DOUBLE PRECISION NOT NULL,
                end_time         DOUBLE PRECISION,
                has_clip         BOOLEAN NOT NULL DEFAULT false,
                has_snapshot     BOOLEAN NOT NULL DEFAULT false,
                thumb_embedding  vector({EMBED_DIM}) NOT NULL,
                synced_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_search_events_start_time
                ON video_search.events (start_time)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS video_search.sync_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        logger.info("video_search schema ready")

    if GRANT_SELECT_ROLE:
        # Postgres is shared with the frigate/ANPR pipeline (public schema, role "frigate" -
        # postgres-init/01-init.sql in the tools repo). This schema is created under our own
        # connection user, so that role has no access to it by default - grant read access so
        # ad-hoc queries against video_search.* work the same as against public.*. Best-effort:
        # a failed grant (e.g. role doesn't exist in some other deployment) shouldn't block
        # startup - just log and move on, in its own connection so it can't abort schema setup.
        try:
            with _pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"GRANT USAGE ON SCHEMA video_search TO {GRANT_SELECT_ROLE}")
                cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA video_search TO {GRANT_SELECT_ROLE}")
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA video_search GRANT SELECT ON TABLES TO {GRANT_SELECT_ROLE}"
                )
                conn.commit()
        except psycopg2.Error as e:
            logger.warning("could not grant SELECT on video_search to %r: %s", GRANT_SELECT_ROLE, e)


_vector_index_ready = False


def _ensure_vector_index() -> None:
    """Build the HNSW index once, after bulk backfill - incremental HNSW inserts during a
    large backfill are much slower than a one-shot build over already-loaded rows."""
    global _vector_index_ready
    if _vector_index_ready:
        return
    with _pg_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_search_events_embedding
                ON video_search.events USING hnsw (thumb_embedding vector_cosine_ops)
        """)
        conn.commit()
    _vector_index_ready = True
    logger.info("vector HNSW index ready")


def _get_watermark() -> float:
    with _pg_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM video_search.sync_state WHERE key = 'last_start_time'")
        row = cur.fetchone()
        return float(row[0]) if row else 0.0


def _vec_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec.tolist()) + "]"


def _upsert_batch(rows: list[tuple], watermark: float) -> None:
    """Upsert a batch and advance the watermark in a single connection/transaction."""
    with _pg_conn() as conn:
        cur = conn.cursor()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO video_search.events
                    (id, camera, label, start_time, end_time, has_clip, has_snapshot, thumb_embedding)
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    camera = EXCLUDED.camera,
                    label = EXCLUDED.label,
                    start_time = EXCLUDED.start_time,
                    end_time = EXCLUDED.end_time,
                    has_clip = EXCLUDED.has_clip,
                    has_snapshot = EXCLUDED.has_snapshot,
                    thumb_embedding = EXCLUDED.thumb_embedding,
                    synced_at = now()
                """,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s::vector)",
            )
        cur.execute("""
            INSERT INTO video_search.sync_state (key, value) VALUES ('last_start_time', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (str(watermark),))
        conn.commit()


def _get_stats() -> dict:
    with _pg_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*), max(synced_at) FROM video_search.events")
        count, last_sync = cur.fetchone()
        cur.execute("SELECT value FROM video_search.sync_state WHERE key = 'last_start_time'")
        row = cur.fetchone()
        watermark = float(row[0]) if row else 0.0
        return {
            "synced_events": count,
            "last_sync_at": last_sync.isoformat() if last_sync else None,
            "watermark_start_time": watermark,
        }


# ---------------------------------------------------------------------------
# Frigate sqlite (read-only) helpers
# ---------------------------------------------------------------------------


def _open_frigate_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{FRIGATE_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _fetch_batch(conn: sqlite3.Connection, after: float, limit: int) -> tuple[list[tuple], int, float]:
    """Returns (rows_with_embeddings, events_scanned, max_start_time_scanned).

    Note: tried splitting this into a cheap ordered `event` scan + a `WHERE id IN (...)`
    point-lookup into vec_thumbnails, expecting it to be faster than the combined join below -
    measured *slower* (~22 rows/s vs ~150 rows/s here), so keeping the join. The single-query
    plan apparently lets SQLite drive the scan from vec_thumbnails directly instead of doing N
    separate point lookups, which are surprisingly expensive against this vec0 table.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.id, e.camera, e.label, e.start_time, e.end_time,
               e.has_clip, e.has_snapshot, v.thumbnail_embedding
        FROM event e
        JOIN vec_thumbnails v ON v.id = e.id
        WHERE e.start_time > ?
        ORDER BY e.start_time ASC
        LIMIT ?
        """,
        (after, limit),
    )
    rows = cur.fetchall()
    if not rows:
        return [], 0, after
    max_start = rows[-1][3]
    return rows, len(rows), max_start


def _count_pending(conn: sqlite3.Connection, after: float) -> int:
    """Cheap approximate count of events newer than the watermark.

    Deliberately NOT joined against vec_thumbnails (a vec0 virtual table) - that join is very
    slow (>60s, vs. ~0.1s for the plain event-table scan) and would stall progress reporting
    itself. A handful of events without an embedding yet aren't counted precisely, fine for a
    progress indicator.
    """
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM event WHERE start_time > ?", (after,))
    return cur.fetchone()[0]


def _sync_once() -> int:
    """Pull all rows newer than the watermark. Returns number of rows synced."""
    watermark = _get_watermark()
    conn = _open_frigate_db()
    total = 0
    try:
        pending = _count_pending(conn, watermark)
        if pending:
            logger.info("sync: %d event(s) pending", pending)
        while True:
            t0 = time.monotonic()
            batch, scanned, max_start = _fetch_batch(conn, watermark, SYNC_BATCH_SIZE)
            if scanned == 0:
                break
            pg_rows = []
            for eid, camera, label, start_time, end_time, has_clip, has_snapshot, blob in batch:
                if not blob or len(blob) != EMBED_DIM * 4:
                    continue
                vec = np.frombuffer(blob, dtype="<f4")
                pg_rows.append((
                    eid, camera, label, start_time, end_time,
                    bool(has_clip), bool(has_snapshot), _vec_literal(vec),
                ))
            watermark = max_start
            _upsert_batch(pg_rows, watermark)
            total += len(pg_rows)
            elapsed = time.monotonic() - t0
            rate = len(pg_rows) / elapsed if elapsed > 0 else 0.0
            pct = 100.0 * total / pending if pending else 100.0
            logger.info(
                "sync batch: +%d rows (scanned %d) in %.1fs (%.0f/s) | %d/%d this cycle (%.0f%%)",
                len(pg_rows), scanned, elapsed, rate, total, pending, pct,
            )
            if scanned < SYNC_BATCH_SIZE:
                break
    finally:
        conn.close()
    if total or not _vector_index_ready:
        _ensure_vector_index()
    return total


def _sync_loop() -> None:
    while True:
        try:
            n = _sync_once()
            if n:
                logger.info("synced %d event(s)", n)
        except sqlite3.OperationalError as e:
            logger.warning("frigate.db read failed (will retry): %s", e)
        except Exception:
            logger.exception("sync cycle failed")
        time.sleep(SYNC_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# Text embedding (jina-clip-v2, text-only path - no dummy vision pass)
# ---------------------------------------------------------------------------

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                import torch
                from transformers import AutoModel

                logger.info("Loading %s text encoder...", EMBED_MODEL_NAME)
                start = time.monotonic()
                model = AutoModel.from_pretrained(EMBED_MODEL_NAME, trust_remote_code=True)
                model.eval()
                torch.set_num_threads(max(1, os.cpu_count() or 1))
                _model = model
                logger.info("Model loaded in %.1fs", time.monotonic() - start)
    return _model


def _embed_query(text: str) -> list[float]:
    import torch

    model = _get_model()
    with torch.no_grad():
        embeddings = model.encode_text([text], truncate_dim=EMBED_DIM)
    vec = embeddings[0]
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return [float(x) for x in vec]


@functools.lru_cache(maxsize=128)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    """Cache the query embedding so sort switches / "load more" pagination for the
    same query text don't re-pay the ~5s text-tower forward pass."""
    return tuple(_embed_query(text))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI()
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


@app.on_event("startup")
def _on_startup() -> None:
    _ensure_schema()
    threading.Thread(target=_get_model, daemon=True).start()
    threading.Thread(target=_sync_loop, daemon=True).start()


@app.get("/api/stats")
def stats() -> JSONResponse:
    return JSONResponse(_get_stats())


@app.get("/api/proxy/thumbnail/{event_id}")
async def proxy_thumbnail(event_id: str) -> Response:
    """Proxy Frigate's event thumbnail via the internal docker network.

    Frigate is only reachable externally through nginx basic-auth (vkosarev.name:5001) - an
    <img> tag can't supply those credentials, so the browser always loads thumbnails through us
    instead, and we reach Frigate directly (same docker network, no auth needed there).
    """
    client = _get_http_client()
    try:
        resp = await client.get(f"{FRIGATE_INTERNAL_URL}/api/events/{event_id}/thumbnail.jpg")
    except httpx.HTTPError:
        return Response(status_code=502)
    if resp.status_code != 200:
        return Response(status_code=resp.status_code)
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))


CANDIDATE_POOL_SIZE = int(os.getenv("CANDIDATE_POOL_SIZE", "500"))

_SORT_EXPR = {
    "relevance": "distance ASC",
    "newest": "start_time DESC",
    "oldest": "start_time ASC",
}


@app.get("/api/search")
def search(
    query: str = Query(..., min_length=1),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("relevance"),
) -> JSONResponse:
    if sort not in _SORT_EXPR:
        return JSONResponse({"detail": f"invalid sort: {sort!r}, expected one of {list(_SORT_EXPR)}"}, status_code=400)

    t0 = time.monotonic()
    embedding = _embed_query_cached(query)
    embed_ms = (time.monotonic() - t0) * 1000

    vec_str = _vec_literal(np.array(embedding, dtype="<f4"))
    order_by = _SORT_EXPR[sort]
    with _pg_conn() as conn:
        cur = conn.cursor()
        # pgvector's HNSW defaults to ef_search=40 - silently caps the inner ORDER BY/LIMIT
        # below CANDIDATE_POOL_SIZE (e.g. only 40 of the intended 500 candidates), which made
        # has_more permanently false ("Показать ещё" never appeared) since a page could never
        # reach `limit` rows. Must be >= CANDIDATE_POOL_SIZE for the pool to actually fill.
        cur.execute("SET hnsw.ef_search = %s", (max(CANDIDATE_POOL_SIZE, 40),))
        cur.execute(
            f"""
            WITH candidates AS (
                SELECT id, camera, label, start_time, end_time, has_clip, has_snapshot,
                       thumb_embedding <=> %s::vector AS distance
                FROM video_search.events
                ORDER BY distance ASC
                LIMIT %s
            )
            SELECT id, camera, label, start_time, end_time, has_clip, has_snapshot, distance
            FROM candidates
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
            """,
            (vec_str, CANDIDATE_POOL_SIZE, limit, offset),
        )
        rows = cur.fetchall()

    results = [
        {
            "id": r[0],
            "camera": r[1],
            "label": r[2],
            "start_time": r[3],
            "end_time": r[4],
            "has_clip": r[5],
            "has_snapshot": r[6],
            "distance": round(float(r[7]), 4),
        }
        for r in rows
    ]
    total_ms = (time.monotonic() - t0) * 1000
    logger.debug("search %r sort=%s offset=%d: embed=%.0fms total=%.0fms results=%d",
                 query, sort, offset, embed_ms, total_ms, len(results))
    return JSONResponse({
        "results": results,
        "count": len(results),
        "has_more": offset + len(results) < CANDIDATE_POOL_SIZE and len(results) == limit,
        "embed_ms": round(embed_ms),
        "total_ms": round(total_ms),
    })


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _HTML


# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video Search</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #1a1a2e; color: #e0e0e0; min-height: 100vh; }
.container { max-width: 1300px; margin: 0 auto; padding: 20px; }
h1 { text-align: center; margin-bottom: 20px; color: #4ea8de; font-size: 24px; }
.search-bar { display: flex; gap: 10px; margin-bottom: 10px; }
.search-bar input[type=text] {
    flex: 1; padding: 12px 16px; border-radius: 8px; border: 2px solid #4ea8de;
    background: #16213e; color: #e0e0e0; font-size: 16px;
}
.search-bar button {
    padding: 12px 24px; border-radius: 8px; border: none; background: #4ea8de;
    color: #16213e; font-weight: bold; cursor: pointer; font-size: 16px;
}
.search-bar button:hover { background: #6fc0f0; }
.search-bar select {
    padding: 12px 14px; border-radius: 8px; border: 2px solid #4ea8de;
    background: #16213e; color: #e0e0e0; font-size: 15px;
}
.status { text-align: center; color: #888; margin-bottom: 20px; font-size: 13px; min-height: 18px; }
.results {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
}
.load-more {
    display: block; margin: 24px auto 0; padding: 10px 28px; border-radius: 8px;
    border: 2px solid #4ea8de; background: transparent; color: #4ea8de;
    font-weight: bold; cursor: pointer; font-size: 15px;
}
.load-more:hover { background: #4ea8de; color: #16213e; }
.load-more[hidden] { display: none; }
.card {
    background: #16213e; border-radius: 10px; overflow: hidden;
    text-decoration: none; color: inherit; transition: transform 0.15s;
}
.card:hover { transform: scale(1.03); }
.card .thumb { width: 100%; aspect-ratio: 16/9; object-fit: cover; background: #0f1626; display: block; }
.card .info { padding: 8px 10px; }
.card .label { font-weight: bold; color: #4ea8de; text-transform: capitalize; }
.card .camera { color: #aaa; font-size: 12px; }
.card .time { color: #888; font-size: 11px; margin-top: 2px; }
.card .dist { color: #666; font-size: 11px; }

@media (max-width: 640px) {
    .container { padding: 14px 12px 40px; }
    h1 { font-size: 20px; margin-bottom: 14px; }
    .search-bar { flex-direction: column; gap: 8px; }
    .search-bar input[type=text],
    .search-bar select,
    .search-bar button {
        width: 100%; font-size: 16px; /* 16px не даёт iOS Safari зумить при фокусе */
    }
    .results { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
    .load-more { width: 100%; padding: 14px; }
}
</style>
</head>
<body>
<div class="container">
<h1>Video Search</h1>
<div class="search-bar">
    <input type="text" id="q" placeholder="человек с собакой, машина у ворот..." autofocus>
    <select id="sort">
        <option value="relevance">По релевантности</option>
        <option value="newest">Сначала новые</option>
        <option value="oldest">Сначала старые</option>
    </select>
    <button onclick="doSearch()">Найти</button>
</div>
<div class="status" id="status"></div>
<div class="results" id="results"></div>
<button class="load-more" id="loadMore" hidden onclick="loadMore()">Показать ещё</button>
</div>
<script>
const q = document.getElementById('q');
const sortSel = document.getElementById('sort');
const status = document.getElementById('status');
const results = document.getElementById('results');
const loadMoreBtn = document.getElementById('loadMore');

const PAGE_SIZE = 48;
let offset = 0;
let lastEmbedMs = 0;

q.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
sortSel.addEventListener('change', () => { if (q.value.trim()) doSearch(); });

// Клип — прямая ссылка на сам Frigate (не через наш backend): это top-level переход по
// ссылке (target=_blank), basic-auth на удалённом Frigate браузер спросит нормально и
// закэширует, а видео (бывает 100+ МБ) не гоняется вдвойне через video-search.
// Thumbnail так не может — <img> не показывает диалог basic-auth для встроенных
// ресурсов, поэтому она всегда идёт через наш /api/proxy/thumbnail (см. cardHtml).
const FRIGATE_HOSTS = {
    'vkosarev.name': 'https://vkosarev.name:5001',
};

function frigateBase() {
    return FRIGATE_HOSTS[location.hostname] || `${location.protocol}//${location.hostname}:5000`;
}

function fmtTime(ts) {
    return new Date(ts * 1000).toLocaleString('ru-RU');
}

function cardHtml(r) {
    return `
        <a class="card" href="${frigateBase()}/api/events/${r.id}/clip.mp4" target="_blank">
            <img class="thumb" src="/api/proxy/thumbnail/${r.id}" loading="lazy">
            <div class="info">
                <div class="label">${r.label}</div>
                <div class="camera">${r.camera}</div>
                <div class="time">${fmtTime(r.start_time)}</div>
                <div class="dist">distance: ${r.distance}</div>
            </div>
        </a>
    `;
}

async function runSearch(append) {
    const query = q.value.trim();
    if (!query) return;
    const url = `/api/search?query=${encodeURIComponent(query)}&limit=${PAGE_SIZE}&offset=${offset}&sort=${sortSel.value}`;
    const resp = await fetch(url);
    const data = await resp.json();
    if (!resp.ok) {
        status.textContent = 'Ошибка: ' + (data.detail || resp.status);
        loadMoreBtn.hidden = true;
        return;
    }
    if (append) {
        results.insertAdjacentHTML('beforeend', data.results.map(cardHtml).join(''));
    } else {
        results.innerHTML = data.results.map(cardHtml).join('');
        lastEmbedMs = data.embed_ms;
    }
    offset += data.results.length;
    status.textContent = `${offset} результатов показано · эмбеддинг запроса: ${lastEmbedMs} мс · за запрос: ${data.total_ms} мс`;
    loadMoreBtn.hidden = !data.has_more;
}

async function doSearch() {
    offset = 0;
    status.textContent = 'Ищу...';
    results.innerHTML = '';
    loadMoreBtn.hidden = true;
    try {
        await runSearch(false);
    } catch (e) {
        status.textContent = 'Ошибка запроса: ' + e;
    }
}

async function loadMore() {
    try {
        await runSearch(true);
    } catch (e) {
        status.textContent = 'Ошибка запроса: ' + e;
    }
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host=_args.host, port=_args.port)
