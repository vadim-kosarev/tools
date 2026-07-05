# -*- coding: utf-8 -*-
"""Sync Immich person search results into per-person albums.

For every configured person, the target album is made to exactly match the
set of assets Immich currently returns for that person (add missing assets,
remove assets that no longer show up in the person search). Meant to be run
periodically from an external cron, see face-indexer.md.
"""

import argparse
import sys

# ---------------------------------------------------------------------------
# CLI parsing happens before any heavy import (network/config/logging).
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", type=str, default=None, help="Path to .env file (default: .env next to script)")
    common.add_argument("--config", type=str, default=None, help="Path to .env.json file (default: .env.json next to script)")
    common.add_argument("--debug", action="store_true", help="Enable DEBUG logging")

    parser = argparse.ArgumentParser(
        prog="face-indexer",
        description="Sync Immich person search results into per-person albums.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", parents=[common], help="Sync albums for all configured persons")
    sync_parser.add_argument("--dry-run", action="store_true", help="Only log planned changes, do not write to Immich")

    subparsers.add_parser("list", parents=[common], help="Resolve configured persons and print them, without syncing")

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
import json
import logging
import os
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

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

IMMICH_URL = os.getenv("IMMICH_URL", "http://immich-server:2283").rstrip("/")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")
_CONFIG_FILE = Path(getattr(_args, "config", None) or (_SCRIPT_DIR / ".env.json"))

_SEARCH_PAGE_SIZE = 1000

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class PersonRef(BaseModel):
    """One entry from .env.json: either a person id (parsed from an Immich URL) or a plain name."""

    kind: Literal["id", "name"]
    value: str
    raw: str


class ImmichPerson(BaseModel):
    id: str
    name: str


class AlbumSummary(BaseModel):
    id: str
    album_name: str


class AlbumSyncResult(BaseModel):
    label: str
    album_id: str
    created: bool
    added: int
    removed: int


def _parse_person_entry(raw: str) -> PersonRef:
    """Parse a .env.json entry: an Immich person URL (.../people/{id}...) or a plain name."""
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[-2] == "people":
            return PersonRef(kind="id", value=parts[-1], raw=raw)
        raise ValueError(f"Cannot extract person id from URL: {raw}")
    return PersonRef(kind="name", value=raw, raw=raw)


def load_person_refs(config_file: Path) -> list[PersonRef]:
    with open(config_file, "r", encoding="utf-8") as f:
        entries = json.load(f)
    return [_parse_person_entry(str(entry)) for entry in entries]


def resolve_person_groups(refs: list[PersonRef], id_to_name: dict[str, str]) -> dict[str, set[str]]:
    """Group configured refs by album label (person name), merging duplicate person ids under one name."""
    name_to_ids: dict[str, set[str]] = {}
    for person_id, name in id_to_name.items():
        if name:
            name_to_ids.setdefault(name, set()).add(person_id)

    groups: dict[str, set[str]] = {}
    for ref in refs:
        if ref.kind == "id":
            name = id_to_name.get(ref.value)
            if name is None:
                logger.warning("Person id not found in Immich: %s", ref.raw)
                continue
            groups.setdefault(name, set()).add(ref.value)
        else:
            matched = name_to_ids.get(ref.value)
            if not matched:
                logger.warning("No person named '%s' found in Immich", ref.value)
                continue
            groups.setdefault(ref.value, set()).update(matched)
    return groups


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

    async def list_people(self) -> list[ImmichPerson]:
        resp = await self._client.get("/api/people", params={"withHidden": "true"})
        resp.raise_for_status()
        data = resp.json()
        return [ImmichPerson(id=p["id"], name=p.get("name") or "") for p in data.get("people", [])]

    async def _search_metadata_asset_ids(self, filter_key: str, filter_ids: list[str]) -> set[str]:
        """Paginate /api/search/metadata for a personIds/albumIds filter and collect all asset ids."""
        asset_ids: set[str] = set()
        page = 1
        while True:
            payload = {
                filter_key: filter_ids,
                "page": page,
                "size": _SEARCH_PAGE_SIZE,
                "isVisible": True,
            }
            resp = await self._client.post("/api/search/metadata", json=payload)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("assets", {}).get("items", [])
            asset_ids.update(item["id"] for item in items)
            if not data.get("assets", {}).get("nextPage"):
                break
            page += 1
        return asset_ids

    async def search_person_asset_ids(self, person_ids: list[str]) -> set[str]:
        return await self._search_metadata_asset_ids("personIds", person_ids)

    async def find_album_by_name(self, name: str) -> Optional[AlbumSummary]:
        resp = await self._client.get("/api/albums")
        resp.raise_for_status()
        for album in resp.json():
            if album.get("albumName") == name:
                return AlbumSummary(id=album["id"], album_name=album["albumName"])
        return None

    async def create_album(self, name: str, asset_ids: list[str]) -> str:
        resp = await self._client.post("/api/albums", json={"albumName": name, "assetIds": list(asset_ids)})
        resp.raise_for_status()
        return resp.json()["id"]

    async def get_album_asset_ids(self, album_id: str) -> set[str]:
        # GET /api/albums/{id} only returns assetCount, not the asset list, on this Immich version.
        return await self._search_metadata_asset_ids("albumIds", [album_id])

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        if not asset_ids:
            return
        resp = await self._client.put(f"/api/albums/{album_id}/assets", json={"ids": asset_ids})
        resp.raise_for_status()

    async def remove_assets_from_album(self, album_id: str, asset_ids: list[str]) -> None:
        if not asset_ids:
            return
        resp = await self._client.request("DELETE", f"/api/albums/{album_id}/assets", json={"ids": asset_ids})
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


async def sync_person_album(client: ImmichClient, label: str, person_ids: set[str], dry_run: bool) -> AlbumSyncResult:
    target_ids = await client.search_person_asset_ids(list(person_ids))
    album = await client.find_album_by_name(label)

    if album is None:
        logger.info("Album '%s' does not exist, will create with %d assets", label, len(target_ids))
        if dry_run:
            return AlbumSyncResult(label=label, album_id="(dry-run)", created=True, added=len(target_ids), removed=0)
        album_id = await client.create_album(label, list(target_ids))
        return AlbumSyncResult(label=label, album_id=album_id, created=True, added=len(target_ids), removed=0)

    current_ids = await client.get_album_asset_ids(album.id)
    to_add = target_ids - current_ids
    to_remove = current_ids - target_ids

    logger.info(
        "Album '%s' (%s): %d to add, %d to remove",
        label, album.id, len(to_add), len(to_remove),
    )
    if not dry_run:
        await client.add_assets_to_album(album.id, list(to_add))
        await client.remove_assets_from_album(album.id, list(to_remove))

    return AlbumSyncResult(label=label, album_id=album.id, created=False, added=len(to_add), removed=len(to_remove))


async def _load_groups(client: ImmichClient) -> dict[str, set[str]]:
    refs = load_person_refs(_CONFIG_FILE)
    people = await client.list_people()
    id_to_name = {p.id: p.name for p in people}
    return resolve_person_groups(refs, id_to_name)


async def run_sync(dry_run: bool) -> None:
    client = ImmichClient(IMMICH_URL, IMMICH_API_KEY)
    try:
        groups = await _load_groups(client)

        if not groups:
            logger.warning("No configured persons could be resolved, nothing to do")
            return

        results: list[AlbumSyncResult] = []
        for label, person_ids in groups.items():
            try:
                result = await sync_person_album(client, label, person_ids, dry_run)
                results.append(result)
            except httpx.HTTPError as e:
                logger.error("Failed to sync album for '%s': %s", label, e)

        total_added = sum(r.added for r in results)
        total_removed = sum(r.removed for r in results)
        logger.info(
            "Done: %d persons processed, %d assets added, %d assets removed%s",
            len(results), total_added, total_removed, " (dry-run)" if dry_run else "",
        )
    finally:
        await client.close()


async def run_list() -> None:
    client = ImmichClient(IMMICH_URL, IMMICH_API_KEY)
    try:
        groups = await _load_groups(client)
        for label, person_ids in groups.items():
            logger.info("%s -> %s", label, ", ".join(sorted(person_ids)))
    finally:
        await client.close()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if _args.command == "sync":
        asyncio.run(run_sync(dry_run=_args.dry_run))
    elif _args.command == "list":
        asyncio.run(run_list())
