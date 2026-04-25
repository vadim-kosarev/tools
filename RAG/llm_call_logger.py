"""LLM call logger — writes prompts and responses to a single local file.

When enabled (LLM_LOG_ENABLED=true in .env), every LLM call is recorded
as a REQUEST/RESPONSE pair with a sequential number.

File created in the log directory:
    _rag_llm.log   — alternating REQUEST / RESPONSE blocks

Each entry format:
    === #001 2026-04-24 12:00:00 [step_name] REQUEST ===
    <prompt text>
    --- end #001 REQUEST ---

    === #001 2026-04-24 12:00:05 [step_name] RESPONSE ===
    <raw LLM response>
    --- end #001 RESPONSE ---

Usage:
    with llm_logger.record("analyze_query") as rec:
        rec.set_request(rendered_prompt)   # written to file immediately
        raw = chain.invoke(...)
        rec.set_response(raw)              # written to file immediately
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

_DEFAULT_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FILE = "_rag_llm.log"


class _CallRecord:
    """Holds step/number and writes request/response immediately to the logger."""

    def __init__(self, step: str, number: int, logger: "LlmCallLogger") -> None:
        self.step    = step
        self.number  = number
        self._logger = logger

    def set_request(self, text: str) -> None:
        """Write REQUEST block to log immediately."""
        self._logger._write(self.number, self.step, "REQUEST", text)

    def set_response(self, text: str) -> None:
        """Write RESPONSE block to log immediately."""
        self._logger._write(self.number, self.step, "RESPONSE", text)


class LlmCallLogger:
    """Thread-safe sequential logger for LLM requests and responses.

    Each call to set_request / set_response flushes to disk immediately
    so the log file is always up-to-date even during long LLM inference.

    Args:
        enabled: If False all operations are no-ops (zero overhead).
        log_dir: Directory for the log file (created automatically).
    """

    def __init__(self, enabled: bool = False, log_dir: Path = _DEFAULT_LOG_DIR) -> None:
        self._enabled = enabled
        self._log_dir = log_dir
        self._counter = 0
        self._lock    = threading.Lock()

        if enabled:
            log_dir.mkdir(parents=True, exist_ok=True)

    def _next_number(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _write(self, number: int, step: str, kind: str, text: str) -> None:
        """Append one block to the log file and flush immediately."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"\n=== #{number:03d} {ts} [{step}] {kind} ===\n"
            f"{text}\n"
            f"--- end #{number:03d} {kind} ---\n"
        )
        path = self._log_dir / _LOG_FILE
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
                f.flush()

    def log_event(self, step: str, text: str) -> None:
        """Write a single informational block to the log immediately.

        Use this for chunk search queries, retrieved results, and other
        pipeline events that are not LLM calls.
        """
        if not self._enabled:
            return
        number = self._next_number()
        self._write(number, step, "EVENT", text)

    @contextmanager
    def record(self, step: str) -> Generator[_CallRecord, None, None]:
        """Context manager wrapping one LLM call.

        Returns a _CallRecord whose set_request / set_response methods
        write to the file immediately (no buffering).
        """
        if not self._enabled:
            yield _CallRecord(step, 0, self)
            return

        number = self._next_number()
        yield _CallRecord(step, number, self)
