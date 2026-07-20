# -*- coding: utf-8 -*-
"""Tag people who show up repeatedly on camera sources in an Immich external library.

For every person whose face appears in the configured source folders (e.g. frigate,
dashcam) with a gap of more than N hours between two consecutive appearances, this
script:
  - assigns a configured tag to every scoped asset of that person (visible in Immich
    search/filter by tag);
  - optionally marks the person as favorite (visible on the People page).

Only uses the Immich REST API (no direct DB writes) so that Immich's own bookkeeping
(tag_closure, search indices, etc.) stays consistent.
"""

import argparse
import sys

# ---------------------------------------------------------------------------
# CLI parsing happens before any heavy import (network/config/logging).
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", type=str, default=None, help="Path to .env file (default: .env next to script)")
    common.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    common.add_argument("--library-id", type=str, default=None, help="Immich external library id to scan")
    common.add_argument(
        "--source-folder",
        dest="source_folders",
        action="append",
        default=None,
        help="Path segment identifying a camera source (e.g. frigate). Repeatable.",
    )
    common.add_argument("--gap-hours", type=float, default=None, help="Minimum gap between appearances to qualify as 'frequent'")
    common.add_argument("--tag-name", type=str, default=None, help="Tag value to assign to qualifying assets")

    parser = argparse.ArgumentParser(
        prog="tag_frequent_faces",
        description="Tag people who repeatedly appear across camera sources in an Immich external library.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", parents=[common], help="Compute and print qualifying persons, without writing anything")

    tag_parser = subparsers.add_parser("tag", parents=[common], help="Create/assign the tag and mark persons as favorite")
    tag_parser.add_argument("--dry-run", action="store_true", help="Only log planned changes, do not write to Immich")
    tag_parser.add_argument(
        "--mark-favorite",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Also mark qualifying persons as favorite (default: from config, True)",
    )

    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
    if not getattr(_args, "command", None):
        _parser.print_help()
        sys.exit(0)

# ---------------------------------------------------------------------------
# Heavy imports: only reached once a command was given.
# ---------------------------------------------------------------------------
import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPT_NAME = Path(__file__).stem

_env_file = getattr(_args, "env", None) or str(_SCRIPT_DIR / ".env")
load_dotenv(_env_file, override=False)

LOG_LEVEL = "DEBUG" if getattr(_args, "debug", False) else os.getenv("LOG_LEVEL", "INFO").upper()

_LOGS_DIR = _SCRIPT_DIR / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

logger = logging.getLogger(_SCRIPT_NAME)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.propagate = False

_formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

_file_handler = logging.FileHandler(_LOGS_DIR / f"{_SCRIPT_NAME}.log", encoding="utf-8")
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)

IMMICH_URL = os.getenv("IMMICH_URL", "http://brightsky:2283").rstrip("/")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")


def _config(flag: str, default: str) -> str:
    """<script_name>.config.<flag> from .env, falling back to a hardcoded default."""
    return os.getenv(f"{_SCRIPT_NAME}.config.{flag}", default)


_cli_library_id = getattr(_args, "library_id", None)
LIBRARY_ID = _cli_library_id or _config("library-id", "")

_cli_source_folders = getattr(_args, "source_folders", None)
SOURCE_FOLDERS = _cli_source_folders or [
    f.strip() for f in _config("source-folders", "frigate,dashcam").split(",") if f.strip()
]

_cli_gap_hours = getattr(_args, "gap_hours", None)
GAP_HOURS = _cli_gap_hours if _cli_gap_hours is not None else float(_config("gap-hours", "1"))

_cli_tag_name = getattr(_args, "tag_name", None)
TAG_NAME = _cli_tag_name or _config("tag-name", "Frequent")

_cli_mark_favorite = getattr(_args, "mark_favorite", None)
_default_mark_favorite = _config("mark-favorite", "true").lower() in ("1", "true", "yes")
MARK_FAVORITE = _cli_mark_favorite if _cli_mark_favorite is not None else _default_mark_favorite

_SEARCH_PAGE_SIZE = 1000
_BULK_CHUNK_SIZE = 1000

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class PersonRef(BaseModel):
    id: str
    name: str = ""


class AssetInfo(BaseModel):
    id: str
    file_created_at: datetime
    original_path: str
    is_offline: bool = False
    people: list[PersonRef] = []


class PersonCandidate(BaseModel):
    person_id: str
    name: str
    asset_ids: list[str]
    first_seen: datetime
    last_seen: datetime
    max_gap_hours: float


# ---------------------------------------------------------------------------
# Immich REST client
# ---------------------------------------------------------------------------


class ImmichClient:
    """Thin async REST client for the subset of the Immich API used here."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=30.0,
            trust_env=False,  # Immich is always a local host; never route through a system HTTP(S)_PROXY
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def search_library_assets(self, library_id: str) -> list[AssetInfo]:
        """Paginate /api/search/metadata for a whole library, with face/person info attached.

        /search/metadata silently excludes offline assets (file missing on disk at last
        library scan) unless isOffline is explicitly requested - it is not a tri-state
        filter, so online and offline assets have to be fetched as two separate passes
        and merged."""
        assets: list[AssetInfo] = []
        for is_offline in (False, True):
            page = 1
            while True:
                payload = {
                    "libraryId": library_id,
                    "withPeople": True,
                    "withExif": False,
                    "withArchived": True,
                    "withDeleted": False,
                    "isOffline": is_offline,
                    "page": page,
                    "size": _SEARCH_PAGE_SIZE,
                    "order": "asc",
                }
                resp = await self._client.post("/api/search/metadata", json=payload)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("assets", {}).get("items", [])
                for item in items:
                    assets.append(
                        AssetInfo(
                            id=item["id"],
                            file_created_at=item["fileCreatedAt"],
                            original_path=item.get("originalPath", ""),
                            is_offline=item.get("isOffline", False),
                            people=[PersonRef(id=p["id"], name=p.get("name") or "") for p in item.get("people", [])],
                        )
                    )
                if not data.get("assets", {}).get("nextPage"):
                    break
                page += 1
        return assets

    async def upsert_tag(self, name: str) -> str:
        resp = await self._client.put("/api/tags", json={"tags": [name]})
        resp.raise_for_status()
        for tag in resp.json():
            if tag["value"] == name:
                return tag["id"]
        raise RuntimeError(f"Immich did not return the upserted tag '{name}'")

    async def tag_assets(self, tag_id: str, asset_ids: list[str]) -> None:
        for i in range(0, len(asset_ids), _BULK_CHUNK_SIZE):
            chunk = asset_ids[i : i + _BULK_CHUNK_SIZE]
            resp = await self._client.put(f"/api/tags/{tag_id}/assets", json={"ids": chunk})
            resp.raise_for_status()

    async def mark_people_favorite(self, person_ids: list[str]) -> None:
        for i in range(0, len(person_ids), _BULK_CHUNK_SIZE):
            chunk = person_ids[i : i + _BULK_CHUNK_SIZE]
            people = [{"id": pid, "isFavorite": True} for pid in chunk]
            resp = await self._client.put("/api/people", json={"people": people})
            resp.raise_for_status()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _in_scope(asset: AssetInfo, source_folders: list[str]) -> bool:
    path_lower = asset.original_path.lower()
    return any(f"/{folder.lower()}/" in path_lower for folder in source_folders)


def find_frequent_persons(assets: list[AssetInfo], gap_hours: float) -> list[PersonCandidate]:
    """Group scoped assets by person and keep those with a gap > gap_hours between two
    consecutive appearances (i.e. repeat visits, not one long continuous appearance)."""
    by_person: dict[str, list[tuple[datetime, str, str]]] = {}
    for asset in assets:
        for person in asset.people:
            by_person.setdefault(person.id, []).append((asset.file_created_at, asset.id, person.name))

    threshold = timedelta(hours=gap_hours)
    candidates: list[PersonCandidate] = []
    for person_id, entries in by_person.items():
        entries.sort(key=lambda e: e[0])
        max_gap = max(
            (curr[0] - prev[0] for prev, curr in zip(entries, entries[1:])),
            default=timedelta(0),
        )
        if max_gap <= threshold:
            continue
        name = next((n for _, _, n in entries if n), "")
        candidates.append(
            PersonCandidate(
                person_id=person_id,
                name=name,
                asset_ids=[asset_id for _, asset_id, _ in entries],
                first_seen=entries[0][0],
                last_seen=entries[-1][0],
                max_gap_hours=max_gap.total_seconds() / 3600,
            )
        )
    candidates.sort(key=lambda c: len(c.asset_ids), reverse=True)
    return candidates


async def compute_candidates(client: ImmichClient) -> list[PersonCandidate]:
    if not LIBRARY_ID:
        raise RuntimeError("library-id is not configured (set --library-id or tag_frequent_faces.config.library-id)")

    logger.info("Fetching assets for library %s ...", LIBRARY_ID)
    assets = await client.search_library_assets(LIBRARY_ID)
    logger.info("Fetched %d assets total", len(assets))

    scoped = [a for a in assets if _in_scope(a, SOURCE_FOLDERS)]
    logger.info("%d assets match source folders %s", len(scoped), SOURCE_FOLDERS)

    candidates = find_frequent_persons(scoped, GAP_HOURS)
    logger.info("%d persons have a gap > %.1fh between appearances", len(candidates), GAP_HOURS)
    return candidates


async def run_list() -> None:
    client = ImmichClient(IMMICH_URL, IMMICH_API_KEY)
    try:
        candidates = await compute_candidates(client)
        for c in candidates:
            logger.info(
                "%-20s | %3d assets | first %s | last %s | max gap %.1fh | %s",
                c.name or "(unnamed)", len(c.asset_ids), c.first_seen, c.last_seen, c.max_gap_hours, c.person_id,
            )
    finally:
        await client.close()


async def run_tag(dry_run: bool, mark_favorite: bool) -> None:
    client = ImmichClient(IMMICH_URL, IMMICH_API_KEY)
    try:
        candidates = await compute_candidates(client)
        if not candidates:
            logger.info("No qualifying persons, nothing to do")
            return

        asset_ids = sorted({asset_id for c in candidates for asset_id in c.asset_ids})
        person_ids = [c.person_id for c in candidates]

        logger.info(
            "Will tag %d assets from %d persons with '%s'%s",
            len(asset_ids), len(person_ids), TAG_NAME, " and mark them favorite" if mark_favorite else "",
        )
        if dry_run:
            logger.info("Dry-run: no changes written")
            return

        tag_id = await client.upsert_tag(TAG_NAME)
        logger.info("Tag '%s' resolved to id %s", TAG_NAME, tag_id)

        await client.tag_assets(tag_id, asset_ids)
        logger.info("Tagged %d assets", len(asset_ids))

        if mark_favorite:
            await client.mark_people_favorite(person_ids)
            logger.info("Marked %d persons as favorite", len(person_ids))
    finally:
        await client.close()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if _args.command == "list":
        asyncio.run(run_list())
    elif _args.command == "tag":
        asyncio.run(run_tag(dry_run=_args.dry_run, mark_favorite=MARK_FAVORITE))