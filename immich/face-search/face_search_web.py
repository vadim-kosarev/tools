# -*- coding: utf-8 -*-
"""Web UI for face search: paste a face, find matches in Immich.

Uses Immich ML service for face embedding extraction (no local ML models needed).
"""

import argparse
import logging
import os
import sys

logger = logging.getLogger("face_search_web")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Web UI for face search in Immich DB")
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--env", type=str, default=None, help="Path to .env file")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host")
    parser.add_argument("--top-k", type=int, default=20, help="Top K results")
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()

# ---------------------------------------------------------------------------
import base64
import json

import httpx
import psycopg2
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_env_file = getattr(_args, "env", None) or os.path.join(_SCRIPT_DIR, ".env")
load_dotenv(_env_file, override=False)

LOG_LEVEL = "DEBUG" if getattr(_args, "debug", False) else os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
)
logger.setLevel(LOG_LEVEL)

IMMICH_DB_HOST = os.getenv("IMMICH_DB_HOST", "database")
IMMICH_DB_PORT = int(os.getenv("IMMICH_DB_PORT", "5432"))
IMMICH_DB_NAME = os.getenv("IMMICH_DB_NAME", "immich")
IMMICH_DB_USER = os.getenv("IMMICH_DB_USER", "postgres")
IMMICH_DB_PASSWORD = os.getenv("IMMICH_DB_PASSWORD", "postgres")
IMMICH_URL = os.getenv("IMMICH_URL", "http://immich-server:2283")
IMMICH_ML_URL = os.getenv("IMMICH_ML_URL", "http://immich-machine-learning:3003")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")
FR_MODEL_NAME = os.getenv("FR_MODEL_NAME", "buffalo_l")

# ---------------------------------------------------------------------------
app = FastAPI()
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def _get_embedding(image_bytes: bytes) -> list[float] | None:
    """Send image to Immich ML and return the best face embedding."""
    client = await _get_http_client()
    entries = json.dumps({
        "facial-recognition": {
            "detection": {"modelName": FR_MODEL_NAME, "options": {}},
            "recognition": {"modelName": FR_MODEL_NAME, "options": {}},
        }
    })
    try:
        resp = await client.post(
            f"{IMMICH_ML_URL}/predict",
            data={"entries": entries},
            files={"image": ("face.jpg", image_bytes, "image/jpeg")},
        )
    except httpx.HTTPError as e:
        logger.error("Immich ML request failed: %s", e)
        return None

    if resp.status_code != 200:
        logger.error("Immich ML error: %s %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json()
    faces = data.get("facial-recognition", [])
    if not faces:
        return None

    best = max(faces, key=lambda f: f.get("score", 0))
    embedding = best.get("embedding")
    if embedding is None:
        return None

    if isinstance(embedding, list):
        return [float(v) for v in embedding]
    if isinstance(embedding, str):
        try:
            parsed = json.loads(embedding)
            if isinstance(parsed, list):
                return [float(v) for v in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            return [float(x) for x in embedding.split(",")]
        except ValueError:
            pass

    logger.error("Could not parse embedding format: %s", type(embedding))
    return None


def _search_immich(embedding: list[float], top_k: int = 20) -> list[dict]:
    """Search Immich pgvector for nearest faces."""
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    conn = psycopg2.connect(
        host=IMMICH_DB_HOST, port=IMMICH_DB_PORT,
        dbname=IMMICH_DB_NAME, user=IMMICH_DB_USER,
        password=IMMICH_DB_PASSWORD,
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.name,
               min(fs.embedding <=> %s::vector) as distance,
               count(af.id) as face_count
        FROM face_search fs
        JOIN asset_face af ON af.id = fs."faceId"
        JOIN person p ON p.id = af."personId"
        WHERE p.name IS NOT NULL AND p.name != ''
        GROUP BY p.id, p.name
        ORDER BY distance ASC
        LIMIT %s
    """, (vec_str, top_k))
    results = []
    for row in cur.fetchall():
        results.append({
            "person_id": str(row[0]),
            "name": row[1],
            "distance": round(float(row[2]), 4),
            "face_count": row[3],
            "immich_url": f"{IMMICH_URL}/people/{row[0]}",
            "thumb_url": f"/api/thumb/{row[0]}",
        })
    conn.close()
    return results


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    image_base64: str


@app.post("/api/search")
async def search(req: SearchRequest) -> JSONResponse:
    try:
        _header, _, data = req.image_base64.partition(",")
        image_bytes = base64.b64decode(data if data else req.image_base64)
    except Exception:
        return JSONResponse({"error": "Invalid image data"}, status_code=400)

    embedding = await _get_embedding(image_bytes)
    if embedding is None:
        return JSONResponse({"error": "No face detected in image"}, status_code=422)

    top_k = getattr(_args, "top_k", 20)
    results = _search_immich(embedding, top_k)
    return JSONResponse({"results": results, "count": len(results)})


@app.get("/api/thumb/{person_id}")
async def thumb_proxy(person_id: str) -> Response:
    """Proxy person thumbnail from Immich API (adds auth header)."""
    client = await _get_http_client()
    headers: dict[str, str] = {}
    if IMMICH_API_KEY:
        headers["x-api-key"] = IMMICH_API_KEY
    try:
        resp = await client.get(
            f"{IMMICH_URL}/api/people/{person_id}/thumbnail",
            headers=headers,
        )
    except httpx.HTTPError:
        return Response(status_code=502)
    if resp.status_code != 200:
        return Response(status_code=resp.status_code)
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _HTML


# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Face Search</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #1a1a2e; color: #e0e0e0; min-height: 100vh; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
h1 { text-align: center; margin-bottom: 20px; color: #e94560; }

.paste-zone {
    border: 3px dashed #e94560; border-radius: 12px; padding: 40px;
    text-align: center; cursor: pointer; margin-bottom: 30px;
    transition: all 0.3s; min-height: 200px;
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 10px;
}
.paste-zone:hover, .paste-zone.drag { border-color: #0f3460; background: #16213e; }
.paste-zone img { max-height: 300px; max-width: 100%; border-radius: 8px; }
.paste-zone .hint { color: #888; font-size: 18px; }

.status { text-align: center; margin: 10px 0; font-size: 14px; color: #888; }
.status.error { color: #e94560; }
.status.ok { color: #4ecca3; }

.results { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
.card {
    background: #16213e; border-radius: 10px; overflow: hidden;
    transition: transform 0.2s; cursor: pointer;
}
.card:hover { transform: translateY(-4px); }
.card a { text-decoration: none; color: inherit; }
.card .thumb { width: 100%; height: 200px; object-fit: cover; background: #0f3460; }
.card .info { padding: 12px; }
.card .name { font-size: 16px; font-weight: 600; color: #e94560; }
.card .dist { font-size: 13px; color: #888; margin-top: 4px; }
.card .faces { font-size: 12px; color: #555; }
.bar { height: 4px; background: #0f3460; border-radius: 2px; margin-top: 6px; }
.bar .fill { height: 100%; border-radius: 2px; transition: width 0.3s; }
.bar .fill.good { background: #4ecca3; }
.bar .fill.ok { background: #e9c46a; }
.bar .fill.weak { background: #e76f51; }
</style>
</head>
<body>
<div class="container">
<h1>Face Search</h1>
<div class="paste-zone" id="pasteZone" tabindex="0">
    <div class="hint">Ctrl+V to paste face image, or drag & drop</div>
</div>
<div class="status" id="status"></div>
<div class="results" id="results"></div>
</div>
<script>
const zone = document.getElementById('pasteZone');
const status = document.getElementById('status');
const results = document.getElementById('results');

zone.addEventListener('paste', e => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            handleImage(item.getAsFile());
            break;
        }
    }
});

zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag'); });
zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('drag');
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith('image/')) handleImage(file);
});

document.addEventListener('paste', e => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            handleImage(item.getAsFile());
            break;
        }
    }
});

function handleImage(file) {
    const reader = new FileReader();
    reader.onload = async (e) => {
        zone.innerHTML = `<img src="${e.target.result}">`;
        status.textContent = 'Searching...';
        status.className = 'status';
        results.innerHTML = '';

        try {
            const resp = await fetch('/api/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({image_base64: e.target.result})
            });
            const data = await resp.json();
            if (data.error) {
                status.textContent = data.error;
                status.className = 'status error';
                return;
            }
            status.textContent = `Found ${data.count} matches`;
            status.className = 'status ok';
            renderResults(data.results);
        } catch (err) {
            status.textContent = 'Error: ' + err.message;
            status.className = 'status error';
        }
    };
    reader.readAsDataURL(file);
}

function renderResults(items) {
    results.innerHTML = items.map(r => {
        const pct = Math.max(0, Math.min(100, (1 - r.distance) * 100));
        const cls = r.distance < 0.4 ? 'good' : r.distance < 0.6 ? 'ok' : 'weak';
        return `
        <div class="card">
            <a href="${r.immich_url}" target="_blank">
                <img class="thumb" src="${r.thumb_url}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 200 200%22><rect fill=%22%230f3460%22 width=%22200%22 height=%22200%22/><text x=%22100%22 y=%22110%22 text-anchor=%22middle%22 fill=%22%23888%22 font-size=%2240%22>?</text></svg>'" alt="${r.name}">
                <div class="info">
                    <div class="name">${r.name}</div>
                    <div class="dist">Distance: ${r.distance.toFixed(3)} (${pct.toFixed(0)}% match)</div>
                    <div class="faces">${r.face_count} faces in Immich</div>
                    <div class="bar"><div class="fill ${cls}" style="width:${pct}%"></div></div>
                </div>
            </a>
        </div>`;
    }).join('');
}
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host=_args.host, port=_args.port,
                log_level="debug" if _args.debug else "info")
