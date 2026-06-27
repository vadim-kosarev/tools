# -*- coding: utf-8 -*-
"""Face Finder API: browse and manage faces/persons in Immich database.

Reads face/person data from Immich public schema.
Writes merge history to face_finder schema (auto-created on startup).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("face_finder_api")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Face Finder API for Immich")
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--env", type=str, default=None, help="Path to .env file")
    parser.add_argument("--port", type=int, default=8767, help="Server port (default: 8767)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host")
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
else:
    _args = argparse.Namespace(debug=False, env=None, port=8767, host="0.0.0.0")

# ---------------------------------------------------------------------------
import io
from typing import Optional

import httpx
import psycopg2
import psycopg2.extras
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

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

IMMICH_DB_HOST = os.getenv("IMMICH_DB_HOST", "database")
IMMICH_DB_PORT = int(os.getenv("IMMICH_DB_PORT", "5432"))
IMMICH_DB_NAME = os.getenv("IMMICH_DB_NAME", "immich")
IMMICH_DB_USER = os.getenv("IMMICH_DB_USER", "postgres")
IMMICH_DB_PASSWORD = os.getenv("IMMICH_DB_PASSWORD", "postgres")
IMMICH_URL = os.getenv("IMMICH_URL", "http://immich-server:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=IMMICH_DB_HOST, port=IMMICH_DB_PORT,
        dbname=IMMICH_DB_NAME, user=IMMICH_DB_USER,
        password=IMMICH_DB_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _init_schema() -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE SCHEMA IF NOT EXISTS face_finder;
        CREATE TABLE IF NOT EXISTS face_finder.merge_log (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_person_id   UUID NOT NULL,
            target_person_id   UUID NOT NULL,
            source_person_name TEXT,
            target_person_name TEXT,
            face_count_moved   INT DEFAULT 0,
            merged_at          TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    conn.commit()
    conn.close()
    logger.info("face_finder schema initialized")


# ---------------------------------------------------------------------------
# HTTP client (shared)
# ---------------------------------------------------------------------------
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


def _immich_headers() -> dict[str, str]:
    if IMMICH_API_KEY:
        return {"x-api-key": IMMICH_API_KEY}
    return {}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Face Finder", docs_url="/api/docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def _startup() -> None:
    try:
        _init_schema()
    except Exception as exc:
        logger.error("Schema init failed: %s", exc)


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------
@app.get("/api/stats")
async def get_stats() -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM person WHERE name IS NOT NULL AND name != '') AS named_persons,
            (SELECT COUNT(*) FROM person) AS total_persons,
            (SELECT COUNT(*) FROM asset_face) AS total_faces,
            (SELECT COUNT(DISTINCT "assetId") FROM asset_face) AS assets_with_faces,
            (SELECT COUNT(*) FROM asset_face WHERE "personId" IS NULL) AS unassigned_faces
    """)
    row = cur.fetchone()
    conn.close()
    return JSONResponse(dict(row))


# ---------------------------------------------------------------------------
# /api/persons
# ---------------------------------------------------------------------------
@app.get("/api/persons")
async def list_persons(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None),
    named_only: bool = Query(False),
) -> JSONResponse:
    offset = (page - 1) * limit
    conn = _get_conn()
    cur = conn.cursor()

    clauses = []
    params: list = []

    if named_only:
        clauses.append("p.name IS NOT NULL AND p.name != ''")
    if q:
        clauses.append("p.name ILIKE %s")
        params.append(f"%{q}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    cur.execute(f"SELECT COUNT(DISTINCT p.id) as total FROM person p {where}", params)
    total = cur.fetchone()["total"]

    cur.execute(f"""
        SELECT p.id::text, p.name, COUNT(af.id) as face_count
        FROM person p
        LEFT JOIN asset_face af ON af."personId" = p.id
        {where}
        GROUP BY p.id, p.name
        ORDER BY COUNT(af.id) DESC, p.name NULLS LAST
        LIMIT %s OFFSET %s
    """, params + [limit, offset])

    items = []
    for row in cur.fetchall():
        d = dict(row)
        d["thumb_url"] = f"/api/persons/{d['id']}/thumbnail"
        items.append(d)

    conn.close()
    return JSONResponse({"items": items, "total": total, "page": page, "limit": limit})


@app.get("/api/persons/unassigned-count")
async def unassigned_count() -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM asset_face WHERE \"personId\" IS NULL")
    total = cur.fetchone()["total"]
    conn.close()
    return JSONResponse({"total": total})


@app.get("/api/persons/{person_id}")
async def get_person(person_id: str) -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT p.id::text, p.name, COUNT(af.id) as face_count
        FROM person p
        LEFT JOIN asset_face af ON af."personId" = p.id
        WHERE p.id = %s::uuid
        GROUP BY p.id, p.name
    """, (person_id,))
    person = cur.fetchone()
    if not person:
        conn.close()
        raise HTTPException(404, "Person not found")

    cur.execute("""
        SELECT af.id::text, af."assetId"::text, af.score,
               af."boundingBoxX1", af."boundingBoxY1",
               af."boundingBoxX2", af."boundingBoxY2",
               af."imageWidth", af."imageHeight"
        FROM asset_face af
        WHERE af."personId" = %s::uuid
        ORDER BY af.score DESC NULLS LAST
        LIMIT 300
    """, (person_id,))

    faces = []
    for row in cur.fetchall():
        d = dict(row)
        d["crop_url"] = f"/api/faces/{d['id']}/crop"
        d["asset_url"] = f"/api/assets/{d['assetId']}/thumbnail"
        faces.append(d)

    conn.close()
    result = dict(person)
    result["thumb_url"] = f"/api/persons/{person_id}/thumbnail"
    result["faces"] = faces
    return JSONResponse(result)


@app.get("/api/persons/{person_id}/thumbnail")
async def person_thumbnail(person_id: str) -> Response:
    client = await _get_http()
    try:
        resp = await client.get(
            f"{IMMICH_URL}/api/people/{person_id}/thumbnail",
            headers=_immich_headers(),
        )
    except httpx.HTTPError:
        return Response(status_code=502)
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        status_code=resp.status_code,
    )


# ---------------------------------------------------------------------------
# /api/persons/merge
# ---------------------------------------------------------------------------
class MergeRequest(BaseModel):
    source_id: str
    target_id: str


@app.post("/api/persons/merge")
async def merge_persons(req: MergeRequest) -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT p.id::text, p.name, COUNT(af.id) as face_count
        FROM person p
        LEFT JOIN asset_face af ON af."personId" = p.id
        WHERE p.id IN (%s::uuid, %s::uuid)
        GROUP BY p.id, p.name
    """, (req.source_id, req.target_id))
    persons = {row["id"]: dict(row) for row in cur.fetchall()}

    source = persons.get(req.source_id)
    target = persons.get(req.target_id)
    if not source or not target:
        conn.close()
        raise HTTPException(404, "One or both persons not found")

    client = await _get_http()
    try:
        resp = await client.post(
            f"{IMMICH_URL}/api/people/{req.target_id}/merge",
            json={"ids": [req.source_id]},
            headers=_immich_headers(),
        )
    except httpx.HTTPError as exc:
        conn.close()
        raise HTTPException(502, f"Immich API error: {exc}")

    if resp.status_code not in (200, 201, 204):
        conn.close()
        raise HTTPException(resp.status_code, f"Immich merge failed: {resp.text[:300]}")

    cur.execute("""
        INSERT INTO face_finder.merge_log
            (source_person_id, target_person_id, source_person_name, target_person_name, face_count_moved)
        VALUES (%s::uuid, %s::uuid, %s, %s, %s)
    """, (req.source_id, req.target_id,
          source["name"], target["name"], source["face_count"]))
    conn.commit()
    conn.close()

    return JSONResponse({
        "success": True,
        "merged_face_count": source["face_count"],
        "target_name": target["name"],
    })


# ---------------------------------------------------------------------------
# /api/assets
# ---------------------------------------------------------------------------
@app.get("/api/assets/with-faces")
async def list_assets_with_faces(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    offset = (page - 1) * limit
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(DISTINCT af."assetId") as total
        FROM asset_face af
    """)
    total = cur.fetchone()["total"]

    cur.execute("""
        SELECT a.id::text, a."originalFileName",
               COUNT(af.id) as face_count,
               COUNT(af.id) FILTER (WHERE af."personId" IS NOT NULL) as named_count
        FROM asset_face af
        JOIN assets a ON a.id = af."assetId"
        GROUP BY a.id, a."originalFileName"
        ORDER BY COUNT(af.id) DESC
        LIMIT %s OFFSET %s
    """, (limit, offset))

    items = []
    for row in cur.fetchall():
        d = dict(row)
        d["thumb_url"] = f"/api/assets/{d['id']}/thumbnail"
        items.append(d)

    conn.close()
    return JSONResponse({"items": items, "total": total, "page": page, "limit": limit})


@app.get("/api/assets/{asset_id}/thumbnail")
async def asset_thumbnail(asset_id: str) -> Response:
    client = await _get_http()
    try:
        resp = await client.get(
            f"{IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=thumbnail",
            headers=_immich_headers(),
        )
    except httpx.HTTPError:
        return Response(status_code=502)
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        status_code=resp.status_code,
    )


@app.get("/api/assets/{asset_id}/faces")
async def asset_faces(asset_id: str) -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT af.id::text, af."personId"::text as person_id,
               p.name as person_name, af.score,
               af."boundingBoxX1", af."boundingBoxY1",
               af."boundingBoxX2", af."boundingBoxY2",
               af."imageWidth", af."imageHeight"
        FROM asset_face af
        LEFT JOIN person p ON p.id = af."personId"
        WHERE af."assetId" = %s::uuid
        ORDER BY af.score DESC NULLS LAST
    """, (asset_id,))

    faces = []
    for row in cur.fetchall():
        d = dict(row)
        d["crop_url"] = f"/api/faces/{d['id']}/crop"
        if d.get("person_id"):
            d["person_thumb_url"] = f"/api/persons/{d['person_id']}/thumbnail"
        faces.append(d)

    conn.close()
    return JSONResponse({"faces": faces})


# ---------------------------------------------------------------------------
# /api/faces
# ---------------------------------------------------------------------------
@app.get("/api/faces/unassigned")
async def list_unassigned_faces(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    offset = (page - 1) * limit
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) as total FROM asset_face WHERE "personId" IS NULL
    """)
    total = cur.fetchone()["total"]

    cur.execute("""
        SELECT af.id::text, af."assetId"::text, af.score
        FROM asset_face af
        WHERE af."personId" IS NULL
        ORDER BY af.score DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, (limit, offset))

    items = []
    for row in cur.fetchall():
        d = dict(row)
        d["crop_url"] = f"/api/faces/{d['id']}/crop"
        d["asset_url"] = f"/api/assets/{d['assetId']}/thumbnail"
        items.append(d)

    conn.close()
    return JSONResponse({"items": items, "total": total, "page": page, "limit": limit})


@app.get("/api/faces/{face_id}/crop")
async def face_crop(face_id: str) -> Response:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT af."assetId"::text,
               af."boundingBoxX1", af."boundingBoxY1",
               af."boundingBoxX2", af."boundingBoxY2",
               af."imageWidth", af."imageHeight"
        FROM asset_face af
        WHERE af.id = %s::uuid
    """, (face_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Face not found")

    client = await _get_http()
    try:
        resp = await client.get(
            f"{IMMICH_URL}/api/assets/{row['assetId']}/thumbnail?size=preview",
            headers=_immich_headers(),
        )
    except httpx.HTTPError:
        return Response(status_code=502)

    if resp.status_code != 200:
        return Response(status_code=resp.status_code)

    try:
        img = Image.open(io.BytesIO(resp.content))
        img_w, img_h = img.size

        orig_w = row["imageWidth"] or 1
        orig_h = row["imageHeight"] or 1

        # Bounding box stored in original image pixel coordinates
        norm_x1 = row["boundingBoxX1"] / orig_w
        norm_y1 = row["boundingBoxY1"] / orig_h
        norm_x2 = row["boundingBoxX2"] / orig_w
        norm_y2 = row["boundingBoxY2"] / orig_h

        x1 = int(norm_x1 * img_w)
        y1 = int(norm_y1 * img_h)
        x2 = int(norm_x2 * img_w)
        y2 = int(norm_y2 * img_h)

        # Add 25% padding around face
        pad_x = int((x2 - x1) * 0.25)
        pad_y = int((y2 - y1) * 0.25)
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(img_w, x2 + pad_x)
        y2 = min(img_h, y2 + pad_y)

        face_img = img.crop((x1, y1, x2, y2))
        face_img = face_img.resize((200, 200), Image.LANCZOS)

        buf = io.BytesIO()
        face_img.save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg")
    except Exception as exc:
        logger.error("Face crop failed for %s: %s", face_id, exc)
        return Response(status_code=500)


# ---------------------------------------------------------------------------
# /api/merge-log
# ---------------------------------------------------------------------------
@app.get("/api/merge-log")
async def merge_log_list(limit: int = Query(100, ge=1, le=500)) -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT source_person_id::text, target_person_id::text,
               source_person_name, target_person_name,
               face_count_moved,
               to_char(merged_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as merged_at
        FROM face_finder.merge_log
        ORDER BY merged_at DESC
        LIMIT %s
    """, (limit,))
    items = [dict(row) for row in cur.fetchall()]
    conn.close()
    return JSONResponse({"items": items})


# ---------------------------------------------------------------------------
# Serve Vue 3 SPA
# ---------------------------------------------------------------------------
_static_dir = _SCRIPT_DIR / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host=_args.host, port=_args.port,
                log_level="debug" if _args.debug else "info")
