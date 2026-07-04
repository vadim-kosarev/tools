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
from fastapi.responses import FileResponse, JSONResponse, Response
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
# /api/ff — face_finder schema (read-only)
# ---------------------------------------------------------------------------

_FF_SORT_MAP = {
    "date_desc":    "vf.start_time DESC",
    "date_asc":     "vf.start_time ASC",
    "name_asc":     "vf.filename ASC",
    "name_desc":    "vf.filename DESC",
    "persons_desc": "person_count DESC, vf.start_time DESC",
    "persons_asc":  "person_count ASC,  vf.start_time DESC",
}


@app.get("/api/ff/video-files")
async def ff_video_files(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None),
    sort: str = Query("date_desc"),
) -> JSONResponse:
    offset = (page - 1) * limit
    order_by = _FF_SORT_MAP.get(sort, _FF_SORT_MAP["date_desc"])

    where = "WHERE vf.filename ILIKE %s" if q else ""
    params_filter: list = [f"%{q}%"] if q else []

    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(f"SELECT COUNT(*) as total FROM face_finder.video_files vf {where}", params_filter)
    total = cur.fetchone()["total"]

    cur.execute(f"""
        SELECT
            vf.id,
            vf.filename,
            vf.path,
            vf.status,
            vf.fps,
            vf.total_frames,
            vf.frames_sampled,
            to_char(vf.start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS start_time,
            to_char(vf.processed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS processed_at,
            COUNT(DISTINCT ft.local_person_id) AS person_count,
            COUNT(ft.id) AS track_count,
            (
                SELECT json_agg(p.*)
                FROM (
                    SELECT
                        ft2.local_person_id,
                        lp.label,
                        lp.immich_person_name,
                        -- segments from face_track_segments; fallback to best_face_jpeg row
                        json_agg(json_build_object(
                            'segment_id', fts.id,
                            'face_track_id', ft2.id,
                            'quality', COALESCE(fts.quality, ft2.best_quality),
                            'frame_index', COALESCE(fts.frame_index, ft2.best_frame_index)
                        ) ORDER BY COALESCE(fts.frame_index, ft2.best_frame_index) ASC NULLS LAST) AS segments
                    FROM face_finder.face_tracks ft2
                    LEFT JOIN face_finder.local_persons lp ON lp.id = ft2.local_person_id
                    LEFT JOIN face_finder.face_track_segments fts ON fts.face_track_id = ft2.id
                    WHERE ft2.video_id = vf.id
                    GROUP BY ft2.local_person_id, lp.label, lp.immich_person_name
                    ORDER BY COALESCE(lp.immich_person_name, lp.label) ASC
                ) p
            ) AS persons
        FROM face_finder.video_files vf
        LEFT JOIN face_finder.face_tracks ft ON ft.video_id = vf.id
        {where}
        GROUP BY vf.id
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
    """, params_filter + [limit, offset])

    items = []
    for row in cur.fetchall():
        d = dict(row)
        persons = d.get("persons") or []
        for p in persons:
            for s in (p.get("segments") or []):
                if s.get("segment_id"):
                    s["thumb_url"] = f"/api/ff/face-track-segments/{s['segment_id']}/thumbnail"
                else:
                    s["thumb_url"] = f"/api/ff/face-tracks/{s['face_track_id']}/thumbnail"
        d["persons"] = persons
        items.append(d)

    conn.close()
    return JSONResponse({"items": items, "total": total, "page": page, "limit": limit})


_FF_PERSONS_SORT_MAP = {
    "tracks_desc": "lp.track_count DESC",
    "tracks_asc":  "lp.track_count ASC",
    "days_desc":   "distinct_days DESC, lp.track_count DESC",
    "days_asc":    "distinct_days ASC,  lp.track_count DESC",
    "name_asc":    "COALESCE(lp.immich_person_name, lp.label) ASC",
    "name_desc":   "COALESCE(lp.immich_person_name, lp.label) DESC",
}

_PERSON_DAYS_CTE = """
    WITH person_days AS (
        SELECT ft.local_person_id,
               COUNT(DISTINCT DATE(vf.start_time)) AS distinct_days
        FROM face_finder.face_tracks ft
        JOIN face_finder.video_files vf ON vf.id = ft.video_id
        GROUP BY ft.local_person_id
    )
"""


@app.get("/api/ff/persons")
async def ff_persons(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None),
    sort: str = Query("tracks_desc"),
    min_days: int = Query(1, ge=1),
) -> JSONResponse:
    offset = (page - 1) * limit
    order_by = _FF_PERSONS_SORT_MAP.get(sort, _FF_PERSONS_SORT_MAP["tracks_desc"])

    clauses: list[str] = []
    params_filter: list = []
    if q:
        clauses.append("(lp.label ILIKE %s OR lp.immich_person_name ILIKE %s)")
        params_filter += [f"%{q}%", f"%{q}%"]
    if min_days > 1:
        clauses.append("COALESCE(pd.distinct_days, 0) >= %s")
        params_filter.append(min_days)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(f"""
        {_PERSON_DAYS_CTE}
        SELECT COUNT(*) as total
        FROM face_finder.local_persons lp
        LEFT JOIN person_days pd ON pd.local_person_id = lp.id
        {where}
    """, params_filter)
    total = cur.fetchone()["total"]

    cur.execute(f"""
        {_PERSON_DAYS_CTE}
        SELECT
            lp.id,
            lp.label,
            lp.immich_person_name,
            lp.track_count,
            COALESCE(pd.distinct_days, 0) AS distinct_days,
            (
                SELECT ft_best.id
                FROM face_finder.face_tracks ft_best
                WHERE ft_best.local_person_id = lp.id
                ORDER BY ft_best.best_quality DESC
                LIMIT 1
            ) AS best_track_id,
            (
                SELECT json_agg(file_data.*)
                FROM (
                    SELECT
                        vf.id AS video_id,
                        vf.filename,
                        to_char(vf.start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS start_time,
                        json_agg(json_build_object(
                            'track_id', ft2.id,
                            'best_quality', ft2.best_quality
                        ) ORDER BY ft2.best_quality DESC) AS tracks
                    FROM face_finder.face_tracks ft2
                    JOIN face_finder.video_files vf ON vf.id = ft2.video_id
                    WHERE ft2.local_person_id = lp.id
                    GROUP BY vf.id, vf.filename, vf.start_time
                    ORDER BY vf.start_time DESC
                ) file_data
            ) AS files
        FROM face_finder.local_persons lp
        LEFT JOIN person_days pd ON pd.local_person_id = lp.id
        {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
    """, params_filter + [limit, offset])

    items = []
    for row in cur.fetchall():
        d = dict(row)
        d["best_thumb_url"] = (
            f"/api/ff/face-tracks/{d['best_track_id']}/thumbnail"
            if d.get("best_track_id") else None
        )
        for f in (d.get("files") or []):
            for t in (f.get("tracks") or []):
                t["thumb_url"] = f"/api/ff/face-tracks/{t['track_id']}/thumbnail"
        d["files"] = d.get("files") or []
        items.append(d)

    conn.close()
    return JSONResponse({"items": items, "total": total, "page": page, "limit": limit})


@app.get("/api/ff/persons/{person_id}")
async def ff_person_detail(person_id: int) -> JSONResponse:
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT lp.id, lp.label, lp.immich_person_name, lp.track_count,
               COUNT(DISTINCT DATE(vf.start_time)) AS distinct_days
        FROM face_finder.local_persons lp
        LEFT JOIN face_finder.face_tracks ft ON ft.local_person_id = lp.id
        LEFT JOIN face_finder.video_files vf ON vf.id = ft.video_id
        WHERE lp.id = %s
        GROUP BY lp.id
    """, (person_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Person not found")
    person = dict(row)

    # Top segment faces by quality (max 16), fallback to best_face_jpeg if no segments
    cur.execute("""
        SELECT fts.id AS segment_id, fts.face_track_id, fts.quality, fts.frame_index,
               vf.filename, vf.fps, vf.total_frames,
               to_char(vf.start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS start_time
        FROM face_finder.face_track_segments fts
        JOIN face_finder.face_tracks ft ON ft.id = fts.face_track_id
        JOIN face_finder.video_files vf ON vf.id = ft.video_id
        WHERE ft.local_person_id = %s
        ORDER BY fts.quality DESC
        LIMIT 16
    """, (person_id,))
    best_segs = cur.fetchall()
    if best_segs:
        person["best_faces"] = [
            {**dict(r), "thumb_url": f"/api/ff/face-track-segments/{r['segment_id']}/thumbnail"}
            for r in best_segs
        ]
    else:
        cur.execute("""
            SELECT ft.id AS face_track_id, NULL AS segment_id, ft.best_quality AS quality, vf.filename
            FROM face_finder.face_tracks ft
            JOIN face_finder.video_files vf ON vf.id = ft.video_id
            WHERE ft.local_person_id = %s
            ORDER BY ft.best_quality DESC LIMIT 16
        """, (person_id,))
        person["best_faces"] = [
            {**dict(r), "thumb_url": f"/api/ff/face-tracks/{r['face_track_id']}/thumbnail"}
            for r in cur.fetchall()
        ]

    # All files with segments per file, sorted chronologically
    cur.execute("""
        SELECT
            vf.id AS video_id,
            vf.filename,
            vf.fps,
            vf.total_frames,
            to_char(vf.start_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS start_time,
            json_agg(json_build_object(
                'segment_id', fts.id,
                'face_track_id', ft.id,
                'quality', COALESCE(fts.quality, ft.best_quality),
                'frame_index', COALESCE(fts.frame_index, ft.best_frame_index)
            ) ORDER BY COALESCE(fts.frame_index, ft.best_frame_index) ASC NULLS LAST) AS segments
        FROM face_finder.face_tracks ft
        JOIN face_finder.video_files vf ON vf.id = ft.video_id
        LEFT JOIN face_finder.face_track_segments fts ON fts.face_track_id = ft.id
        WHERE ft.local_person_id = %s
        GROUP BY vf.id, vf.filename, vf.fps, vf.total_frames, vf.start_time
        ORDER BY vf.start_time DESC
    """, (person_id,))
    files = []
    for r in cur.fetchall():
        f = dict(r)
        for s in (f.get("segments") or []):
            if s.get("segment_id"):
                s["thumb_url"] = f"/api/ff/face-track-segments/{s['segment_id']}/thumbnail"
            else:
                s["thumb_url"] = f"/api/ff/face-tracks/{s['face_track_id']}/thumbnail"
        files.append(f)
    person["files"] = files

    conn.close()
    return JSONResponse(person)


@app.get("/api/ff/face-tracks/{track_id}/thumbnail")
async def ff_face_track_thumbnail(track_id: int) -> Response:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT best_face_jpeg FROM face_finder.face_tracks WHERE id = %s",
        (track_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row or not row["best_face_jpeg"]:
        raise HTTPException(404, "No thumbnail for this track")
    return Response(content=bytes(row["best_face_jpeg"]), media_type="image/jpeg")



@app.get("/api/ff/face-track-segments/{segment_id}/thumbnail")
async def ff_face_track_segment_thumbnail(segment_id: int) -> Response:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT jpeg FROM face_finder.face_track_segments WHERE id = %s", (segment_id,))
    row = cur.fetchone()
    conn.close()
    if not row or not row["jpeg"]:
        raise HTTPException(404, "No thumbnail for this segment")
    return Response(content=bytes(row["jpeg"]), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Serve Vue 3 SPA  (catch-all — must be last)
# ---------------------------------------------------------------------------
_static_dir = _SCRIPT_DIR / "static"


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catchall(full_path: str) -> Response:
    requested = (_static_dir / full_path).resolve()
    if requested.is_relative_to(_static_dir.resolve()) and requested.is_file():
        return FileResponse(str(requested))
    index = _static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(404, "Not found")

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host=_args.host, port=_args.port,
                log_level="debug" if _args.debug else "info")
