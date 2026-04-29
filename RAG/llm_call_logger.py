"""LLM call logger - writes prompts and responses to a single local file.

When enabled (LLM_LOG_ENABLED=true in .env), every LLM call, tool call and
pipeline stage transition is recorded to a single file with visually distinct
separators that make it easy to follow how context evolves.

Visual conventions:
    ______  (_): PIPELINE STAGE markers - prominent

File created in the log directory:
    _rag_llm.log   - chronological stream of all events

Each LLM block:
    #001  2026-04-25 12:00:00  [EXPAND_QUERY]  REQUEST
    <prompt content>
    #001  2026-04-25 12:00:01  [EXPAND_QUERY]  REQUEST end

Each TOOL block:
    #002  2026-04-25 12:00:01  [TOOL:semantic_search]  REQUEST
    {"query": "BDKO description"}
    #002  2026-04-25 12:00:02  [TOOL:semantic_search]  REQUEST end

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
_SEP_STAGE = "_" * _W        # pipeline stage markers


class _CallRecord:
    """Holds step/number and writes request/response immediately to the logger."""

    def __init__(self, step: str, number: int, logger: "LlmCallLogger") -> None:
        self.step    = step
        self.number  = number
        self._logger = logger
        self._streaming_started = False
        self._stream_buffer: list[str] = []
        self._streaming_file_path: Path | None = None  # Путь к файлу для streaming

    def set_request(self, text: str) -> None:
        """Write REQUEST block to log immediately with unique number."""
        # Получаем новый уникальный номер для request
        request_num = self._logger._next_number()
        self._logger._write(request_num, self.step, "REQUEST", text)
    
    def append_token(self, token: str, to_console: bool = True) -> None:
        """Append a streaming token to the response buffer.

        Writes token to file incrementally and optionally to console.
        Must be followed by finalize_response() to close the RESPONSE block.

        Args:
            token: Text token to append
            to_console: If True, print token to stdout immediately
        """
        if not self._streaming_started:
            # Get new unique number for streaming response header
            response_num = self._logger._next_number()
            # Write RESPONSE block header on first token
            self._streaming_file_path = self._logger._write_streaming_header(response_num, self.step)
            self._streaming_started = True
            self.number = response_num  # Update number for footer

        self._stream_buffer.append(token)
        self._logger._append_streaming_token(token, self._streaming_file_path)

        if to_console:
            print(token, end='', flush=True)

    def finalize_response(self) -> None:
        """Close the streaming RESPONSE block with footer."""
        if self._streaming_started:
            self._logger._write_streaming_footer(self.number, self._streaming_file_path)
            if self._stream_buffer:  # Print newline after streaming output
                print()  # newline after streamed response

    def set_response(self, text: str) -> None:
        """Write RESPONSE block to log immediately (non-streaming mode)."""
        if self._streaming_started:
            # Already streamed, just finalize
            self.finalize_response()
        else:
            # Get new unique number for response
            response_num = self._logger._next_number()
            self._logger._write(response_num, self.step, "RESPONSE", text)


class LlmCallLogger:
    """Thread-safe sequential logger for LLM requests, responses, and pipeline stages.

    Visual hierarchy in the log file:
        LLM blocks   - header and footer with timestamp
        TOOL blocks  - header and footer with timestamp
        STAGE marks  - bounded by ___ lines (pipeline stage transitions)

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
        separate_files: If True, write each request/response to separate numbered files.
        state_callback: Optional callback function() → dict to save agent state alongside logs.
    """

    def __init__(
        self,
        enabled: bool = False,
        log_dir: Path = _DEFAULT_LOG_DIR,
        stream_to_console: bool = True,
        separate_files: bool = True,
        state_callback: Any = None
    ) -> None:
        self._enabled = enabled
        self._log_dir = log_dir
        self._stream_to_console = stream_to_console
        self._separate_files = separate_files
        self._counter = 0
        self._lock    = threading.Lock()
        self._state_callback = state_callback

        if enabled:
            log_dir.mkdir(parents=True, exist_ok=True)

    def _next_number(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _write(self, number: int, step: str, kind: str, text: str) -> None:
        """Append one block to the log file and flush immediately.

        Visual style depends on block type:
          - LLM_CALL / custom -> header and footer with timestamp
          - TOOL:*            -> header and footer with timestamp
          - EVENT             -> ___ border (reuses log_stage format)

        If separate_files=True, writes to individual numbered files:
          001_llm_request.log, 002_llm_response.log, 003_tool_request.log, etc.
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_tool  = step.startswith("TOOL:") or step.startswith("DB:")
        is_event = kind == "EVENT"

        if is_event:
            # EVENT uses the same style as log_stage
            header = f"##  {ts}  {step}"
            indented = "\n".join(f"##    {line}" for line in text.splitlines())
            block = f"\n{_SEP_STAGE}\n{header}\n{indented}\n{_SEP_STAGE}\n"
        elif is_tool:
            header = f"#{number:03d}  {ts}  [{step}]  {kind}"
            end    = f"#{number:03d}  {ts}  [{step}]  {kind} end"
            block  = f"\n{header}\n{text}\n{end}\n"
        else:
            header = f"#{number:03d}  {ts}  [{step}]  {kind}"
            end    = f"#{number:03d}  {ts}  [{step}]  {kind} end"
            block  = (
                f"\n{header}\n"
                f"{text}\n"
                f"{end}\n"
            )

        with self._lock:
            if self._separate_files and not is_event:
                # Пишем в отдельный файл с номером
                kind_lower = kind.lower()
                tool_name = step.replace("TOOL:", "").replace("DB:", "") if is_tool else step.lower()

                # Заменяем недопустимые символы в именах файлов (для Windows)
                # : → _ (для DB:xxx и других)
                tool_name = tool_name.replace(":", "_")
                step_clean = step.lower().replace(":", "_")

                # Формируем имя файла: 001_llm_request.log, 002_tool_exact_search_response.log
                if is_tool:
                    filename = f"{number:03d}_tool_{tool_name}_{kind_lower}.log"
                else:
                    filename = f"{number:03d}_llm_{step_clean}_{kind_lower}.log"

                path = self._log_dir / filename
                with path.open("w", encoding="utf-8") as f:
                    f.write(block)
                    f.flush()

                # Сохраняем state рядом с .log файлом
                if self._state_callback:
                    try:
                        state = self._state_callback()
                        if state:
                            state_filename = filename.replace(".log", ".json")
                            state_path = self._log_dir / state_filename
                            import json
                            with state_path.open("w", encoding="utf-8") as sf:
                                json.dump(state, sf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass  # Не падаем если не удалось сохранить state
            else:
                # Пишем в общий файл _rag_llm.log
                path = self._log_dir / _LOG_FILE
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
                    f.flush()

    def _write_streaming_header(self, number: int, step: str) -> Path | None:
        """Write RESPONSE block header for streaming mode (without closing footer).

        Returns:
            Path to the file being written (for subsequent token appends), or None if using single file.
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"#{number:03d}  {ts}  [{step}]  RESPONSE"
        block = f"\n{header}\n"

        with self._lock:
            if self._separate_files:
                # Пишем в отдельный файл
                # Заменяем недопустимые символы в именах файлов (для Windows)
                step_clean = step.lower().replace(":", "_")
                filename = f"{number:03d}_llm_{step_clean}_response.log"
                path = self._log_dir / filename
                with path.open("w", encoding="utf-8") as f:
                    f.write(block)
                    f.flush()
                return path
            else:
                # Пишем в общий файл
                path = self._log_dir / _LOG_FILE
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
                    f.flush()
                return None

    def _append_streaming_token(self, token: str, file_path: Path | None = None) -> None:
        """Append a single token to the currently open streaming response.

        Args:
            token: Token to append
            file_path: Path to specific file (for separate_files mode) or None for single file
        """
        with self._lock:
            if file_path:
                # Append to specific file
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(token)
                    f.flush()
            else:
                # Append to single log file
                path = self._log_dir / _LOG_FILE
                with path.open("a", encoding="utf-8") as f:
                    f.write(token)
                    f.flush()

    def _write_streaming_footer(self, number: int, file_path: Path | None = None) -> None:
        """Write closing footer for streaming RESPONSE block.

        Args:
            number: Record number
            file_path: Path to specific file (for separate_files mode) or None for single file
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        end = f"#{number:03d}  {ts}  RESPONSE end"
        block = f"\n{end}\n"

        with self._lock:
            if file_path:
                # Append to specific file
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(block)
                    f.flush()

                # Сохраняем state рядом с .log файлом (после завершения response)
                if self._state_callback:
                    try:
                        state = self._state_callback()
                        if state:
                            state_filename = file_path.name.replace(".log", ".json")
                            state_path = self._log_dir / state_filename
                            with state_path.open("w", encoding="utf-8") as sf:
                                json.dump(state, sf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass  # Не падаем если не удалось сохранить state
            else:
                # Append to single log file
                path = self._log_dir / _LOG_FILE
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
    """Formats a nested list of LangChain messages into a readable log string with clear sections."""
    parts: list[str] = []

    # Model info
    if model_name:
        parts.append(f"Model: {model_name}\n{'-' * 40}\n")

    # Collect all messages first to determine structure
    all_messages = []
    for msg_list in messages:
        for msg in msg_list:
            role = type(msg).__name__.replace("Message", "").upper()

            if isinstance(msg.content, str):
                content = msg.content
            elif isinstance(msg.content, (dict, list)):
                # Dict или list - сериализуем в JSON с отступами для читаемости
                # Обрамляем тегами ```json для синтаксической подсветки
                json_content = json.dumps(msg.content, ensure_ascii=False, indent=2, default=str)
                content = f"```json\n{json_content}\n```"
            else:
                # Другие типы - преобразуем в JSON
                json_content = json.dumps(msg.content, ensure_ascii=False, default=str)
                content = f"```json\n{json_content}\n```"

            all_messages.append((role, msg, content))

    # Group messages: SYSTEM separate, rest under [MESSAGES]
    system_messages = []
    conversation_messages = []

    for role, msg, content in all_messages:
        if role == "SYSTEM":
            system_messages.append((role, msg, content))
        else:
            conversation_messages.append((role, msg, content))

    # Format SYSTEM messages
    for role, msg, content in system_messages:
        parts.append(f"[SYSTEM]\n{content}")

    # Format conversation messages under [MESSAGES] section if there are any
    if conversation_messages:
        if system_messages:  # Add Messages section only if there was a system message before
            parts.append("\n[MESSAGES]")

        for msg_num, (role, msg, content) in enumerate(conversation_messages, start=1):
            if role == "HUMAN":
                parts.append(f"\n[#{msg_num} USER]\n{content}")
            elif role == "AI":
                # AI messages may have tool_calls
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    # JSON для tool_calls с обрамлением ```json тегами
                    tc_json = json.dumps(tool_calls, ensure_ascii=False, default=str, indent=2)
                    parts.append(f"\n[#{msg_num} ASSISTANT]\n{content}\n\n[TOOL_CALLS]\n```json\n{tc_json}\n```")
                else:
                    parts.append(f"\n[#{msg_num} ASSISTANT]\n{content}")
            elif role == "TOOL":
                tool_name = getattr(msg, "name", "unknown")
                parts.append(f"\n[#{msg_num} TOOL_RESULT: {tool_name}]\n{content}")
            else:
                # Fallback for any other message types
                parts.append(f"\n[#{msg_num} {role}]\n{content}")

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
                    # JSON для tool_calls с обрамлением ```json тегами
                    tc_json = json.dumps(tool_calls, ensure_ascii=False, default=str, indent=2)
                    parts.append(f"{content}\n\n[TOOL_CALLS]\n```json\n{tc_json}\n```")
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
                self._log._append_streaming_token(f"\n\nERROR: {error}", rec._streaming_file_path)
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
        
        # Попытка отформатировать input_str как JSON для читаемости
        formatted_input = input_str
        try:
            # Попробуем распарсить как JSON и красиво отформатировать с обрамлением ```json
            parsed = json.loads(input_str)
            json_content = json.dumps(parsed, ensure_ascii=False, indent=2)
            formatted_input = f"```json\n{json_content}\n```"
        except (json.JSONDecodeError, TypeError):
            # Если не JSON - оставляем как есть
            pass
        
        rec = self._log.start_record(f"TOOL:{tool_name}")
        rec.set_request(formatted_input)
        self._tool_records[str(run_id)] = (rec, formatted_input)

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

            # Форматируем output - если это dict/list, обрамляем ```json тегами
            output_str = str(output)
            if isinstance(output, (dict, list)):
                try:
                    json_content = json.dumps(output, ensure_ascii=False, indent=2)
                    output_str = f"```json\n{json_content}\n```"
                except (TypeError, ValueError):
                    # Если не сериализуется - оставляем как строку
                    pass

            # Prepend full arguments to response for easier log reading
            response_text = f"[args]\n{input_str}\n{'-' * 40}\n{output_str}"
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
