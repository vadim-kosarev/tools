"""LLM call logger - writes prompts and responses to a single local file.

When enabled (LLM_LOG_ENABLED=true in .env), every LLM call, tool call and
pipeline stage transition is recorded to a single file with visually distinct
separators that make it easy to follow how context evolves.

Visual conventions:
    ......  (.): LLM REQUEST / RESPONSE blocks   - most prominent
    ------  (-): TOOL REQUEST / RESPONSE blocks  - medium
    ______  (_): PIPELINE STAGE markers          - prominent, different char
    ~~~~~~  (~): end-of-block footer             - common closing line

File created in the log directory:
    _rag_llm.log   - chronological stream of all events

Each LLM block:
    ................................................................................
      #001  2026-04-25 12:00:00  [EXPAND_QUERY]  REQUEST
    ................................................................................
    <prompt content>
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
      end #001 REQUEST

Each TOOL block:
    --------------------------------------------------------------------------------
      #002  2026-04-25 12:00:01  [TOOL:semantic_search]  REQUEST
    --------------------------------------------------------------------------------
    {"query": "BDKO description"}
    ..............  end #002 REQUEST  ..............

Each STAGE block:
    ________________________________________________________________________________
    ##  2026-04-25 12:00:05  QUERY EXPANSION COMPLETE
    ##    Rephrased (5): "database KE"; "object registry KCOI"...
    ##    Key terms: BDKO, KCOI, Active Directory
    ________________________________________________________________________________

Usage:
    with llm_logger.record("analyze_query") as rec:
        rec.set_request(rendered_prompt)
        raw = chain.invoke(...)
        rec.set_response(raw)

    llm_logger.log_stage("AGENT PASS 1 START", "question: '...'\\ntools hint: ...")

LangChain integration:
    from llm_call_logger import LangChainFileLogger
    handler = LangChainFileLogger(llm_logger, step_prefix="EXPAND_QUERY")
    agent.invoke(input, config={"callbacks": [handler]})
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

_DEFAULT_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FILE = "_rag_llm.log"

_W = 80                      # line width for separators
_SEP_LLM   = "." * _W        # LLM calls  (prominent double-line feel)
_SEP_TOOL  = "-" * _W        # tool calls (single-line)
_SEP_STAGE = "_" * _W        # pipeline stage markers
_SEP_END   = "~" * _W        # block end / footer


class _CallRecord:
    """Holds step/number and writes request/response immediately to the logger."""

    def __init__(self, step: str, number: int, logger: "LlmCallLogger") -> None:
        self.step    = step
        self.number  = number
        self._logger = logger
        self._streaming_started = False
        self._stream_buffer: list[str] = []

    def set_request(self, text: str) -> None:
        """Write REQUEST block to log immediately."""
        self._logger._write(self.number, self.step, "REQUEST", text)

    def append_token(self, token: str, to_console: bool = True) -> None:
        """Append a streaming token to the response buffer.

        Writes token to file incrementally and optionally to console.
        Must be followed by finalize_response() to close the RESPONSE block.

        Args:
            token: Text token to append
            to_console: If True, print token to stdout immediately
        """
        if not self._streaming_started:
            # Write RESPONSE block header on first token
            self._logger._write_streaming_header(self.number, self.step)
            self._streaming_started = True

        self._stream_buffer.append(token)
        self._logger._append_streaming_token(token)

        if to_console:
            print(token, end='', flush=True)

    def finalize_response(self) -> None:
        """Close the streaming RESPONSE block with footer."""
        if self._streaming_started:
            self._logger._write_streaming_footer(self.number)
            if self._stream_buffer:  # Print newline after streaming output
                print()  # newline after streamed response

    def set_response(self, text: str) -> None:
        """Write RESPONSE block to log immediately (non-streaming mode)."""
        if self._streaming_started:
            # Already streamed, just finalize
            self.finalize_response()
        else:
            self._logger._write(self.number, self.step, "RESPONSE", text)


class LlmCallLogger:
    """Thread-safe sequential logger for LLM requests, responses, and pipeline stages.

    Visual hierarchy in the log file:
        LLM blocks  - bounded by ... lines (most prominent)
        TOOL blocks - bounded by --- lines
        STAGE marks - bounded by ___ lines (pipeline stage transitions)

    Each call to set_request / set_response flushes to disk immediately
    so the log file is always up-to-date even during long LLM inference.

    Streaming mode:
        When LLM uses streaming generation (ChatOllama with streaming=True),
        tokens are written to the log file and printed to console incrementally
        as they arrive, providing live feedback during generation.

    Args:
        enabled: If False all operations are no-ops (zero overhead).
        log_dir: Directory for the log file (created automatically).
        stream_to_console: If True, print streaming tokens to stdout in real-time.
    """

    def __init__(self, enabled: bool = False, log_dir: Path = _DEFAULT_LOG_DIR, stream_to_console: bool = True) -> None:
        self._enabled = enabled
        self._log_dir = log_dir
        self._stream_to_console = stream_to_console
        self._counter = 0
        self._lock    = threading.Lock()

        if enabled:
            log_dir.mkdir(parents=True, exist_ok=True)

    def _next_number(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _write(self, number: int, step: str, kind: str, text: str) -> None:
        """Append one block to the log file and flush immediately.

        Visual style depends on block type:
          - LLM_CALL / custom -> ... borders, ~~~ footer
          - TOOL:*            -> --- borders, ... end marker
          - EVENT             -> ___ border (reuses log_stage format)
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_tool  = step.startswith("TOOL:")
        is_event = kind == "EVENT"

        if is_event:
            # EVENT uses the same style as log_stage
            header = f"##  {ts}  {step}"
            indented = "\n".join(f"##    {line}" for line in text.splitlines())
            block = f"\n{_SEP_STAGE}\n{header}\n{indented}\n{_SEP_STAGE}\n"
        elif is_tool:
            header = f"  #{number:03d}  {ts}  [{step}]  {kind}"
            end    = f"..............  end #{number:03d} {kind}  .............."
            block  = f"\n{_SEP_TOOL}\n{header}\n{_SEP_TOOL}\n{text}\n{end}\n"
        else:
            header = f"  #{number:03d}  {ts}  [{step}]  {kind}"
            end    = f"  end #{number:03d} {kind}"
            block  = (
                f"\n{_SEP_LLM}\n{header}\n{_SEP_LLM}\n"
                f"{text}\n"
                f"{_SEP_END}\n{end}\n"
            )

        path = self._log_dir / _LOG_FILE
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
                f.flush()

    def _write_streaming_header(self, number: int, step: str) -> None:
        """Write RESPONSE block header for streaming mode (without closing footer)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"  #{number:03d}  {ts}  [{step}]  RESPONSE"
        block = f"\n{_SEP_LLM}\n{header}\n{_SEP_LLM}\n"

        path = self._log_dir / _LOG_FILE
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
                f.flush()

    def _append_streaming_token(self, token: str) -> None:
        """Append a single token to the currently open streaming response."""
        path = self._log_dir / _LOG_FILE
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(token)
                f.flush()

    def _write_streaming_footer(self, number: int) -> None:
        """Write closing footer for streaming RESPONSE block."""
        end = f"  end #{number:03d} RESPONSE"
        block = f"\n{_SEP_END}\n{end}\n"

        path = self._log_dir / _LOG_FILE
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
                f.flush()

    def log_stage(self, stage: str, details: str = "") -> None:
        """Write a pipeline stage annotation with ___ border.

        Use before/after each major operation so the log clearly shows the
        pipeline progression and what context was built at each step.

        Example stages:
            "QUERY EXPANSION START"    - before expand_query()
            "QUERY EXPANSION COMPLETE" - with rephrased queries + key terms
            "AGENT PASS 1 START"       - with expanded query summary
            "AGENT PASS 1 COMPLETE"    - with tool call count and answer length
            "EVALUATION 1 COMPLETE"    - relevance/completeness/missing aspects
            "REFINEMENT COMPLETE"      - quality delta and best pass

        Args:
            stage:   Short stage name (one line, shown on header line).
            details: Multi-line detail text; each line gets "##  " prefix.
        """
        if not self._enabled:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"##  {ts}  {stage}"
        if details:
            indented = "\n".join(f"##    {line}" for line in details.splitlines())
            body = f"{header}\n{indented}"
        else:
            body = header

        path = self._log_dir / _LOG_FILE
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(f"\n{_SEP_STAGE}\n{body}\n{_SEP_STAGE}\n")
                f.flush()

    def log_event(self, step: str, text: str) -> None:
        """Write a single informational block to the log immediately.

        Use this for chunk search queries, retrieved results, and other
        pipeline events that are not LLM calls.
        Rendered with ___ borders (same as log_stage).

        Args:
            step: Short label for this event (shown as stage name).
            text: Informational content of the event.
        """
        if not self._enabled:
            return
        number = self._next_number()
        self._write(number, step, "EVENT", text)

    def start_record(self, step: str) -> _CallRecord:
        """Create a _CallRecord for use outside a context manager.

        Useful in callback handlers where request and response arrive in
        separate method calls (on_chat_model_start / on_llm_end).

        Returns a _CallRecord whose set_request / set_response methods
        write to the file immediately. When disabled, returns a no-op record.
        """
        if not self._enabled:
            return _CallRecord(step, 0, self)
        number = self._next_number()
        return _CallRecord(step, number, self)

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


# ---------------------------------------------------------------------------
# LangChain callback handler
# ---------------------------------------------------------------------------

def _fmt_message_list(messages: list[list[BaseMessage]], model_name: str = "") -> str:
    """Formats a nested list of LangChain messages into a readable log string."""
    parts: list[str] = []
    if model_name:
        parts.append(f"Model: {model_name}\n{'-' * 40}")
    for msg_list in messages:
        for msg in msg_list:
            role = type(msg).__name__.replace("Message", "").upper()
            if isinstance(msg.content, str):
                content = msg.content
            else:
                content = json.dumps(msg.content, ensure_ascii=False, default=str)
            # Append tool_calls if present (AIMessage with pending tool calls)
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                tc_json = json.dumps(tool_calls, ensure_ascii=False, default=str, indent=2)
                content = f"{content}\n[tool_calls]\n{tc_json}"
            parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _fmt_llm_result(response: LLMResult) -> str:
    """Extracts generated text (and tool calls) from LLMResult for logging."""
    parts: list[str] = []
    for gen_list in response.generations:
        for gen in gen_list:
            if hasattr(gen, "message"):
                msg = gen.message
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    tc_json = json.dumps(tool_calls, ensure_ascii=False, default=str, indent=2)
                    parts.append(f"{content}\n\n[tool_calls]\n{tc_json}")
                else:
                    parts.append(content)
            elif hasattr(gen, "text"):
                parts.append(gen.text)
            else:
                parts.append(str(gen))
    return "\n---\n".join(parts)


class LangChainFileLogger(BaseCallbackHandler):
    """LangChain callback handler that writes LLM calls and tool calls to LlmCallLogger.

    Intercepts:
      - on_chat_model_start  -> logs full message list as REQUEST
      - on_llm_end           -> logs generated text (with tool_calls) as RESPONSE
      - on_llm_error         -> logs error as RESPONSE
      - on_tool_start        -> logs tool name + input as REQUEST
      - on_tool_end          -> logs tool output as RESPONSE
      - on_tool_error        -> logs error as RESPONSE

    Each LLM / tool invocation gets a unique sequential number so REQUEST
    and RESPONSE blocks can be matched by number in the log file.

    Args:
        file_logger:  Initialized LlmCallLogger instance (may be disabled).
        step_prefix:  Label used for LLM blocks instead of default "LLM_CALL".
                      Set to e.g. "EXPAND_QUERY" or "EVALUATE_PASS_1" to make
                      the log immediately readable without tracing call stacks.
    """

    def __init__(
        self,
        file_logger: LlmCallLogger,
        step_prefix: str = "LLM_CALL",
    ) -> None:
        super().__init__()
        self._log = file_logger
        self._step_prefix = step_prefix
        # run_id (str) -> _CallRecord
        self._llm_records: dict[str, _CallRecord] = {}
        # run_id (str) -> (record, input_str) so we can prepend args to RESPONSE
        self._tool_records: dict[str, tuple[_CallRecord, str]] = {}

    # -- LLM events ------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self._log._enabled:
            return
        model_name = (
            serialized.get("kwargs", {}).get("model")
            or serialized.get("name", "LLM")
        )
        text = _fmt_message_list(messages, model_name)
        rec = self._log.start_record(self._step_prefix)
        rec.set_request(text)
        self._llm_records[str(run_id)] = rec

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        rec = self._llm_records.pop(str(run_id), None)
        if rec is None:
            return

        # If streaming was used, finalize; otherwise write full response
        if rec._streaming_started:
            rec.finalize_response()
        else:
            rec.set_response(_fmt_llm_result(response))

    def on_llm_new_token(
        self,
        token: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Called for each new token during streaming generation.

        Writes token to file immediately and prints to console for live output
        (if stream_to_console is enabled in the logger).
        """
        rec = self._llm_records.get(str(run_id))
        if rec:
            rec.append_token(token, to_console=self._log._stream_to_console)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        rec = self._llm_records.pop(str(run_id), None)
        if rec:
            if rec._streaming_started:
                # Streaming was in progress, finalize and append error
                self._log._append_streaming_token(f"\n\nERROR: {error}")
                rec.finalize_response()
            else:
                rec.set_response(f"ERROR: {error}")

    # -- Tool events -----------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self._log._enabled:
            return
        tool_name = serialized.get("name", "tool")
        rec = self._log.start_record(f"TOOL:{tool_name}")
        rec.set_request(input_str)
        self._tool_records[str(run_id)] = (rec, input_str)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        entry = self._tool_records.pop(str(run_id), None)
        if entry:
            rec, input_str = entry
            # Prepend full arguments to response for easier log reading
            response_text = f"[args]\n{input_str}\n{'-' * 40}\n{str(output)}"
            rec.set_response(response_text)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        entry = self._tool_records.pop(str(run_id), None)
        if entry:
            rec, input_str = entry
            rec.set_response(f"[args]\n{input_str}\n{'-' * 40}\nERROR: {error}")
