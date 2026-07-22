# -*- coding: utf-8 -*-
"""Run raw SQL against the Immich Postgres database.

Meant as a thin, generic ad-hoc runner (debugging, one-off migrations/checks) -
it sends the given SQL text to Postgres as-is via a single execute() call and
prints whatever the server returns for it, so semicolon-separated statements in
a file are executed together and only the last statement's result set is shown
(the same simple-query behavior `psql` uses without per-statement reporting).
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

    parser = argparse.ArgumentParser(
        prog="run_sql",
        description="Run raw SQL against the Immich Postgres database.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", parents=[common], help="Execute SQL and print the result")
    sql_source = run_parser.add_mutually_exclusive_group(required=True)
    sql_source.add_argument("--sql-file", type=str, help="Path to a .sql file to execute")
    sql_source.add_argument("--sql", type=str, help="SQL text to execute")

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
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pydantic import BaseModel

if sys.platform == "win32":
    # psycopg's async mode needs a selector-based loop; the Windows default (Proactor) is unsupported.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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

_CONNECT_TIMEOUT_SECONDS = 10


class DbConfig(BaseModel):
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "DbConfig":
        return cls(
            host=os.getenv("IMMICH_DB_HOST", "localhost"),
            port=int(os.getenv("IMMICH_DB_PORT", "5432")),
            dbname=os.getenv("IMMICH_DB_NAME", "immich"),
            user=os.getenv("IMMICH_DB_USER", "postgres"),
            password=os.getenv("IMMICH_DB_PASSWORD", ""),
        )


def _read_sql(sql_file: str | None, sql: str | None) -> str:
    if sql_file:
        return Path(sql_file).read_text(encoding="utf-8")
    return sql


def _format_rows(columns: list[str], rows: list[tuple]) -> str:
    widths = [max(len(col), *(len(str(row[i])) for row in rows)) if rows else len(col) for i, col in enumerate(columns)]
    header = " | ".join(col.ljust(w) for col, w in zip(columns, widths))
    separator = "-+-".join("-" * w for w in widths)
    lines = [header, separator]
    lines += [" | ".join(str(row[i]).ljust(w) for i, w in enumerate(widths)) for row in rows]
    return "\n".join(lines)


async def run_sql(sql: str, config: DbConfig) -> None:
    logger.info("Connecting to %s:%s/%s as %s", config.host, config.port, config.dbname, config.user)
    async with await psycopg.AsyncConnection.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=config.user,
        password=config.password,
        autocommit=True,
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            if cur.description is None:
                logger.info("OK, %d row(s) affected", cur.rowcount)
                return
            columns = [desc.name for desc in cur.description]
            rows = await cur.fetchall()
            logger.info("%d row(s) returned:\n%s", len(rows), _format_rows(columns, rows))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if _args.command == "run":
        sql_text = _read_sql(_args.sql_file, _args.sql)
        asyncio.run(run_sql(sql_text, DbConfig.from_env()))
