"""
LangGraph RAG-агент — рефакторинг v3.

АРХИТЕКТУРА (простая линейная цепочка):
    START → planner → tool_selector → tool_executor → analyzer → refiner → final → END

Узлы:
  planner       - строит текстовый план поиска (LLM, Russian)
  tool_selector - выбирает инструменты и формирует инструкции (LLM → JSON)
  tool_executor - выполняет инструменты, без LLM
  analyzer      - добавляет результаты в контекст, без LLM
  refiner       - решает следующий шаг (без LLM, всегда → final)
  final         - строит ответ по накопленному контексту (LLM)

AgentState (TypedDict для совместимости с LangGraph):
  user_query          : str            — запрос пользователя
  plan                : list[str]      — список шагов плана (от planner)
  tool_instructions   : list[dict]     — [{tool, input}] (от tool_selector)
  context             : list[dict]     — накопленные результаты тулов (от tool_executor)
  history             : list[dict]     — структурированная история (HistoryEntry dicts)
  next_node           : str            — от refiner (всегда "final")
  final_answer        : str            — итоговый ответ JSON (от final)

История (HistoryEntry) — Pydantic-объекты, сохраняемые как dict через .model_dump():
  type="user_prompt"      — вопрос пользователя
  type="llm_reply"        — ответ LLM из любого узла
  type="tool_execution"   — вызов инструмента с аргументами и markdown-результатом
  type="tool_summary"     — сводка по итогам выполнения инструментов (от analyzer)
  type="refiner_summary"  — решение refiner-а

ОБРАБОТКА ОШИБОК:
  - Все узлы при ошибке пишут полный стектрейс в .log и делают re-raise
  - _fatal_error(context, exc, **input_data) — красивый вывод в stderr
  - main() ловит всё, вызывает _fatal_error и завершает с кодом 1
  - KeyboardInterrupt / EOFError → exit code 0

КОНВЕРТАЦИЯ СООБЩЕНИЙ:
  - _build_messages_from_history(system_msg, history) собирает list[dict] для LLM
  - dict/list content нормализуется в Markdown через dict_to_markdown

ПОВТОРНЫЕ ПОПЫТКИ (retry):
  - _invoke_llm_with_retry(llm, schema, messages, retry_fn, llm_logger, node)
  - При ошибке парсинга: логирует raw-ответ, добавляет retry-промпт, повторяет 1 раз

Использование:
    python rag_lg_agent.py "найди все СУБД"
    python rag_lg_agent.py --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Literal, TypedDict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama

import rag_chat
from rag_chat import build_vectorstore, settings
from kb_tools import create_kb_tools, get_tool_registry
from llm_call_logger import LangChainFileLogger, LlmCallLogger
from prompt_loader import get_loader
from schema_generator import (
    get_plan_schema,
    get_action_schema,
    get_final_schema,
)
from logging_config import setup_logging
from pydantic_utils import pydantic_to_markdown, dict_to_markdown

logger = setup_logging("rag_lg_agent_v3")

_prompt_loader = get_loader()


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 1   # В новой линейной архитектуре — 1 проход
AUTO_EXPAND_CONTEXT = True


# ---------------------------------------------------------------------------
# Утилиты: ошибки и нормализация
# ---------------------------------------------------------------------------

def _fatal_error(context: str, exc: Exception, **input_data: Any) -> None:
    """Красивый вывод фатальной ошибки в stderr. Не прерывает — нужен raise/exit после."""
    SEP  = "=" * 80
    SEP2 = "-" * 80
    print(f"\n{SEP}", file=sys.stderr)
    print(f"💥 ОШИБКА в {context}", file=sys.stderr)
    print(f"   {type(exc).__name__}: {exc}", file=sys.stderr)
    if input_data:
        print(SEP2, file=sys.stderr)
        print("Входные данные:", file=sys.stderr)
        for k, v in input_data.items():
            v_str = str(v)
            if len(v_str) > 500:
                v_str = v_str[:497] + "..."
            print(f"  {k}: {v_str}", file=sys.stderr)
    print(SEP2, file=sys.stderr)
    print("Стектрейс:", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    print(SEP, file=sys.stderr)


def _extract_llm_raw_output(exc: Exception) -> str:
    """Извлекает raw-текст LLM из OutputParserException.llm_output или str(exc)."""
    raw = getattr(exc, "llm_output", None)
    if raw:
        return str(raw)
    if hasattr(exc, "errors"):
        try:
            return str(exc.errors())
        except Exception:
            pass
    return str(exc)


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Конвертирует dict/list content в Markdown перед отправкой в LLM."""
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, (dict, list)):
            title = msg.get("name") or msg.get("role") or None
            content = dict_to_markdown(content, title=title)
            msg = {**msg, "content": content}
        result.append(msg)
    return result


def _to_serializable(obj: Any) -> Any:
    """
    Конвертирует объект в JSON-сериализуемую структуру.

    Pydantic v2: model_dump(mode='json') возвращает dict где все значения
    уже JSON-совместимы (datetime → str, Enum → value, вложенные модели → dict).
    Для остальных типов — рекурсивный обход.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='json')
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(i) for i in obj]
    return obj


def _save_agent_state(state: AgentState, node_name: str, llm_logger: LlmCallLogger) -> None:
    """
    Сохраняет текущее состояние агента в .json файл в logs/.
    
    Имя файла: {counter:03d}_agent_state_{node_name}.json
    где counter берётся из llm_logger для синхронизации с request/response файлами.
    """
    if not llm_logger._enabled:
        return
    
    try:
        # Получаем текущий счётчик (последний использованный номер)
        with llm_logger._lock:
            counter = llm_logger._counter
        
        filename = f"{counter:03d}_agent_state_{node_name}.json"
        filepath = llm_logger._log_dir / filename
        
        # Конвертируем state в JSON-сериализуемый формат
        state_json = _to_serializable(dict(state))
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state_json, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"Сохранено состояние в {filename}")
    except Exception as e:
        logger.warning(f"Не удалось сохранить состояние агента: {e}")


# ---------------------------------------------------------------------------
# История: Pydantic-модели для HistoryEntry
# ---------------------------------------------------------------------------

class HistoryUserPrompt(BaseModel):
    type: Literal["user_prompt"] = "user_prompt"
    content: str


class HistoryLLMReply(BaseModel):
    type: Literal["llm_reply"] = "llm_reply"
    node: str
    content: str  # pydantic_to_markdown(result)


class HistoryToolExecution(BaseModel):
    type: Literal["tool_execution"] = "tool_execution"
    call_id: str
    tool: str
    args: dict[str, Any]
    result_md: str  # dict_to_markdown(tool_result)


class HistoryToolSummary(BaseModel):
    type: Literal["tool_summary"] = "tool_summary"
    tool_count: int
    content: str


class HistoryRefinerSummary(BaseModel):
    type: Literal["refiner_summary"] = "refiner_summary"
    decision: str
    content: str


# ---------------------------------------------------------------------------
# AgentState (TypedDict — нативная совместимость с LangGraph)
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    """Состояние агента. Поля заполняются последовательно по цепочке узлов."""
    user_query: str                    # вход: запрос пользователя
    plan: list[str]                    # planner → список шагов
    tool_instructions: list[dict]      # tool_selector → [{tool, input}]
    context: list[dict]                # tool_executor → [{tool, input, result, result_md}]
    history: list[dict]                # HistoryEntry.model_dump() — хронология
    next_node: str                     # refiner → "final" (всегда)
    final_answer: str                  # final → JSON строка


# ---------------------------------------------------------------------------
# Pydantic-модели LLM-ответов
# ---------------------------------------------------------------------------

class AgentPlan(BaseModel):
    """Ответ planner-а."""
    status: Literal["plan"] = Field(default="plan")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    plan: list[str] = Field(description="Список шагов плана (3-5 пунктов)")


class ToolAction(BaseModel):
    """Один вызов инструмента."""
    tool: str = Field(description="Имя инструмента")
    input: dict[str, Any] = Field(description="Параметры инструмента")


class AgentAction(BaseModel):
    """Ответ tool_selector-а."""
    status: Literal["action"] = Field(default="action")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    actions: list[ToolAction] = Field(description="Список вызовов инструментов")


class FinalAnswerData(BaseModel):
    entity: str
    attribute: str
    value: str


class RecommendedSection(BaseModel):
    source: str = Field(description="Источник (файл)")
    section: str = Field(description="Название раздела")
    relevance: Literal["high", "medium"] = Field(description="Релевантность")
    reason: str = Field(description="Причина рекомендации")


class FinalAnswer(BaseModel):
    summary: str = Field(description="Краткий ответ")
    details: str = Field(description="Подробное объяснение")
    data: list[FinalAnswerData] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    recommendations: list[RecommendedSection] = Field(default_factory=list)
    self_assessment: str = Field(description="Самооценка агента")


class AgentFinal(BaseModel):
    """Ответ final-узла."""
    status: Literal["final"] = Field(default="final")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    final_answer: FinalAnswer = Field(description="Итоговый ответ")


# ---------------------------------------------------------------------------
# LLM logger (singleton)
# ---------------------------------------------------------------------------
# LLM Call Logger с синхронным сохранением состояния
# ---------------------------------------------------------------------------

_llm_logger: LlmCallLogger | None = None
_current_agent_state: dict[str, Any] = {}  # Глобальное хранилище текущего состояния


def _get_current_state() -> dict[str, Any]:
    """Возвращает текущее состояние агента для сохранения в логах."""
    return _to_serializable(dict(_current_agent_state))


def _get_llm_logger() -> LlmCallLogger:
    global _llm_logger
    if _llm_logger is None:
        _llm_logger = LlmCallLogger(
            enabled=settings.llm_log_enabled,
            log_dir=Path(__file__).parent / "logs",
            stream_to_console=True,
            state_callback=_get_current_state,  # Callback для получения текущего state
        )
    return _llm_logger


# ---------------------------------------------------------------------------
# Список доступных инструментов (кэш)
# ---------------------------------------------------------------------------

_TOOLS_JSON_CACHE: str | None = None


def _build_tools_json() -> str:
    global _TOOLS_JSON_CACHE
    if _TOOLS_JSON_CACHE is not None:
        return _TOOLS_JSON_CACHE

    tool_registry = get_tool_registry()
    tool_schemas: dict[str, Any] = {}
    try:
        vs = rag_chat.build_vectorstore()
        kd = Path(rag_chat.settings.knowledge_dir)
        for t in create_kb_tools(vs, kd):
            if hasattr(t, "name") and hasattr(t, "args_schema"):
                tool_schemas[t.name] = t.args_schema
    except Exception as e:
        logger.warning(f"Не удалось получить схемы параметров инструментов: {e}")

    tools_list = []
    for name, desc in tool_registry.items():
        info: dict[str, Any] = {"name": name, "description": desc, "parameters": {}}
        schema = tool_schemas.get(name)
        if schema and hasattr(schema, "model_json_schema"):
            js = schema.model_json_schema()
            info["parameters"] = {
                "type": "object",
                "properties": js.get("properties", {}),
                "required": js.get("required", []),
            }
        tools_list.append(info)

    _TOOLS_JSON_CACHE = json.dumps(tools_list, ensure_ascii=False, indent=2)
    return _TOOLS_JSON_CACHE


def _get_available_tools() -> str:
    raw = _build_tools_json()
    try:
        tools = json.loads(raw)
        return "[\n  " + ",\n  ".join(
            json.dumps(t, ensure_ascii=False, separators=(",", ":")) for t in tools
        ) + "\n]"
    except Exception:
        return raw


_SYSTEM_PROMPT: str = ""  # инициализируется лениво при первом вызове


def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if not _SYSTEM_PROMPT:
        _SYSTEM_PROMPT = _prompt_loader.render(
            "system.md", {"available_tools": _get_available_tools()}
        )
    return _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _fix_tool_args(tool_name: str, tool_input: dict) -> dict:
    """Исправляет типичные ошибки LLM при именовании параметров."""
    fixed = tool_input.copy()
    if tool_name == "find_sections_by_term":
        if "term" in fixed and "substring" not in fixed:
            fixed["substring"] = fixed.pop("term")
        if "query" in fixed and "substring" not in fixed:
            fixed["substring"] = fixed.pop("query")
    if tool_name in ("exact_search_in_file", "exact_search_in_file_section", "get_section_content"):
        if "source" in fixed and "source_file" not in fixed:
            fixed["source_file"] = fixed.pop("source")
        if "file" in fixed and "source_file" not in fixed:
            fixed["source_file"] = fixed.pop("file")
    if tool_name == "get_neighbor_chunks" and "chunk_indices" in fixed:
        raise ValueError(
            "get_neighbor_chunks requires 'line_start' parameter, not 'chunk_indices'. "
            "LLM probably confused it with get_chunks_by_index tool."
        )
    return fixed


def _build_messages_from_history(system_msg: str, history: list[dict]) -> list[dict]:
    """
    Строит список LLM-сообщений из структурированной истории.

    Маппинг типов:
      user_prompt     → role: user
      llm_reply       → role: assistant
      tool_execution  → role: tool
      tool_summary    → role: user
      refiner_summary → role: user
    """
    messages: list[dict] = [{"role": "system", "content": system_msg}]
    for h in history:
        t = h.get("type")
        if t == "user_prompt":
            messages.append({"role": "user", "content": h["content"]})
        elif t == "llm_reply":
            messages.append({"role": "assistant", "content": h["content"]})
        elif t == "tool_execution":
            messages.append({
                "role": "tool",
                "name": h["tool"],
                "tool_call_id": h.get("call_id", f"call_{h['tool']}"),
                "content": h["result_md"],
            })
        elif t in ("tool_summary", "refiner_summary"):
            messages.append({"role": "user", "content": h["content"]})
    return messages


def _invoke_llm_with_retry(
    structured_llm: Any,
    messages: list[dict],
    node_name: str,
    llm_logger: LlmCallLogger,
    retry_render_fn: Any,
    invoke_config: dict,
) -> Any:
    """
    Вызывает LLM с одной попыткой retry при ошибке парсинга JSON.

    При ошибке:
    1. Извлекает raw-ответ из исключения
    2. Логирует в файл с меткой "PARSE ERROR <node_name>"
    3. Добавляет retry-промпт к messages
    4. Повторяет вызов
    5. При повторной ошибке — re-raise
    """
    max_retries = 2
    for attempt in range(max_retries):
        try:
            return structured_llm.invoke(_normalize_messages(messages), config=invoke_config)
        except Exception as exc:
            if attempt < max_retries - 1:
                raw = _extract_llm_raw_output(exc)
                logger.warning(f"Ошибка парсинга JSON в {node_name} (attempt {attempt + 1}): {exc}")
                llm_logger.log_stage(
                    f"PARSE ERROR {node_name} (attempt {attempt + 1})",
                    f"Ошибка: {exc}\n\nRaw LLM output:\n{raw}",
                )
                messages.append({"role": "user", "content": retry_render_fn(raw)})
            else:
                logger.error(
                    f"Не удалось распарсить JSON в {node_name} после {max_retries} попыток: {exc}",
                    exc_info=True,
                )
                raise


# ---------------------------------------------------------------------------
# Узлы графа
# ---------------------------------------------------------------------------

def planner_node(state: AgentState) -> AgentState:
    """Строит текстовый план поиска на основе запроса пользователя."""
    global _current_agent_state
    _current_agent_state = dict(state)  # Обновляем глобальный state
    
    llm_logger = _get_llm_logger()
    user_query = state["user_query"]
    llm_logger.log_stage("PLANNER START", f"Вопрос: {user_query}")

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentPlan)

        render_ctx = {
            "user_query": user_query,
            "system_prompt": _get_system_prompt(),
            "available_tools": _get_available_tools(),
            "schema_AgentPlan": get_plan_schema(),
            "MAX_ITERATIONS": MAX_ITERATIONS,
        }
        system_msg = _prompt_loader.render_plan_system(render_ctx)
        user_msg   = _prompt_loader.render_plan_user(render_ctx)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {"callbacks": [LangChainFileLogger(llm_logger, step_prefix="PLANNER")]}

        # Начало истории: вопрос пользователя
        history = list(state.get("history", []))
        history.append(HistoryUserPrompt(content=user_query).model_dump())

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        result: AgentPlan = _invoke_llm_with_retry(
            structured_llm, messages, "planner", llm_logger,
            retry_render_fn=lambda raw: _prompt_loader.render_plan_retry(
                render_ctx, extra={"error_message": raw, "schema_AgentPlan": get_plan_schema()}
            ),
            invoke_config=invoke_config,
        )

        history.append(HistoryLLMReply(
            node="planner", content=pydantic_to_markdown(result)
        ).model_dump())

        llm_logger.log_stage(
            "PLANNER COMPLETE",
            "Plan:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(result.plan))
        )
        logger.info(f"Planner: {len(result.plan)} шагов")

        state["plan"] = result.plan
        state["history"] = history
        _save_agent_state(state, "planner", llm_logger)
        return state

    except Exception as exc:
        llm_logger.log_stage("PLANNER ERROR", f"Ошибка: {exc}\n\n{traceback.format_exc()}")
        logger.error(f"Ошибка planner_node: {exc}", exc_info=True)
        raise


def tool_selector_node(state: AgentState) -> AgentState:
    """На основе плана выбирает инструменты и формирует JSON-инструкции."""
    global _current_agent_state
    _current_agent_state = dict(state)  # Обновляем глобальный state
    
    llm_logger = _get_llm_logger()
    llm_logger.log_stage("TOOL_SELECTOR START", f"Plan: {state.get('plan', [])}")

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentAction)

        render_ctx = {
            "user_query": state["user_query"],
            "plan": state.get("plan", []),
            "all_tool_results": state.get("context", []),
            "system_prompt": _get_system_prompt(),
            "available_tools": _get_available_tools(),
            "MAX_ITERATIONS": MAX_ITERATIONS,
        }
        system_msg = _prompt_loader.render_action_system(render_ctx)
        user_msg   = _prompt_loader.render_action_user(render_ctx)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {"callbacks": [LangChainFileLogger(llm_logger, step_prefix="TOOL_SELECTOR")]}

        history = list(state.get("history", []))
        messages = _build_messages_from_history(system_msg, history)
        messages.append({"role": "user", "content": user_msg})

        result: AgentAction = _invoke_llm_with_retry(
            structured_llm, messages, "tool_selector", llm_logger,
            retry_render_fn=lambda raw: _prompt_loader.render_action_retry(
                render_ctx, extra={"error_message": raw, "schema_AgentAction": get_action_schema()}
            ),
            invoke_config=invoke_config,
        )

        tool_instructions = [{"tool": a.tool, "input": a.input} for a in result.actions]

        history.append(HistoryLLMReply(
            node="tool_selector", content=pydantic_to_markdown(result)
        ).model_dump())

        llm_logger.log_stage(
            "TOOL_SELECTOR COMPLETE",
            f"Thought: {result.thought}\n" +
            "\n".join(f"  - {a.tool}({json.dumps(a.input, ensure_ascii=False)})" for a in result.actions)
        )
        logger.info(f"ToolSelector: выбрано {len(tool_instructions)} инструментов")

        state["tool_instructions"] = tool_instructions
        state["history"] = history
        _save_agent_state(state, "tool_selector", llm_logger)
        return state

    except Exception as exc:
        llm_logger.log_stage("TOOL_SELECTOR ERROR", f"Ошибка: {exc}\n\n{traceback.format_exc()}")
        logger.error(f"Ошибка tool_selector_node: {exc}", exc_info=True)
        raise


def tool_executor_node(state: AgentState) -> AgentState:
    """Выполняет инструменты из tool_instructions. Без LLM."""
    global _current_agent_state
    _current_agent_state = dict(state)  # Обновляем глобальный state
    
    llm_logger = _get_llm_logger()
    instructions = state.get("tool_instructions", [])
    llm_logger.log_stage("TOOL_EXECUTOR START", f"Инструментов: {len(instructions)}")

    try:
        vs = build_vectorstore(force_reindex=False)
        kd = Path(settings.knowledge_dir)
        tools_list = create_kb_tools(
            vectorstore=vs, knowledge_dir=kd,
            semantic_top_k=settings.retriever_top_k,
            llm_logger=llm_logger,
        )
        tools_map = {t.name: t for t in tools_list}

        context = list(state.get("context", []))
        history = list(state.get("history", []))
        call_idx = 0

        for tc in instructions:
            tool_name  = tc["tool"]
            tool_input = tc["input"]
            call_id    = f"call_{call_idx}"
            call_idx  += 1

            try:
                tool_input = _fix_tool_args(tool_name, tool_input)
            except ValueError as e:
                logger.error(f"Некорректные параметры для {tool_name}: {e}")
                context.append({"tool": tool_name, "input": tool_input, "result": f"ERROR: {e}", "result_md": f"ERROR: {e}"})
                history.append(HistoryToolExecution(call_id=call_id, tool=tool_name, args=tool_input, result_md=f"ERROR: {e}").model_dump())
                continue

            if tool_name not in tools_map:
                msg = f"ERROR: Tool {tool_name} not found"
                context.append({"tool": tool_name, "input": tool_input, "result": msg, "result_md": msg})
                history.append(HistoryToolExecution(call_id=call_id, tool=tool_name, args=tool_input, result_md=msg).model_dump())
                continue

            try:
                raw_result = tools_map[tool_name].invoke(tool_input)
                serial_result = _to_serializable(raw_result)
                result_dict = {"tool": tool_name, "input": tool_input, "result": serial_result}
                result_md = dict_to_markdown(result_dict, title=f"Tool: {tool_name}")
                context.append({**result_dict, "result_md": result_md})
                history.append(HistoryToolExecution(call_id=call_id, tool=tool_name, args=tool_input, result_md=result_md).model_dump())
                logger.info(f"Выполнен {tool_name}: {len(str(raw_result))} символов")
            except Exception as e:
                logger.warning(f"Ошибка выполнения {tool_name}: {e}")
                msg = f"ERROR: {e}"
                context.append({"tool": tool_name, "input": tool_input, "result": msg, "result_md": msg})
                history.append(HistoryToolExecution(call_id=call_id, tool=tool_name, args=tool_input, result_md=msg).model_dump())

        # Автоматическое расширение контекста (get_neighbor_chunks)
        if AUTO_EXPAND_CONTEXT and "get_neighbor_chunks" in tools_map:
            import re
            expansions = 0
            for entry in list(context):
                if expansions >= 5:
                    break
                if "ERROR" in str(entry.get("result", "")) or entry["tool"] == "get_neighbor_chunks":
                    continue
                for source, line_str in re.findall(r'\[([^\]]+\.md)\][^\(]*\(line (\d+)\)', str(entry.get("result_md", "")))[:2]:
                    if expansions >= 5:
                        break
                    try:
                        expand_input = {"source": source, "line_start": int(line_str), "before": 3, "after": 3}
                        expand_result = tools_map["get_neighbor_chunks"].invoke(expand_input)
                        exp_dict = {"tool": "get_neighbor_chunks", "input": expand_input, "result": _to_serializable(expand_result)}
                        exp_md = dict_to_markdown(exp_dict, title="Tool: get_neighbor_chunks")
                        context.append({**exp_dict, "result_md": exp_md, "auto_expanded": True})
                        history.append(HistoryToolExecution(
                            call_id=f"expand_{expansions}", tool="get_neighbor_chunks",
                            args=expand_input, result_md=exp_md
                        ).model_dump())
                        expansions += 1
                        logger.info(f"Расширение контекста: {source}:{line_str}")
                    except Exception as e:
                        logger.warning(f"Ошибка расширения {source}:{line_str}: {e}")

        llm_logger.log_stage(
            "TOOL_EXECUTOR COMPLETE",
            f"Всего результатов: {len(context)}\n" +
            "\n".join(f"  - {e['tool']}: {len(str(e['result']))} символов" for e in context)
        )

        state["context"] = context
        state["history"] = history
        _current_agent_state.update(state)  # Обновляем глобальный state перед сохранением

        _save_agent_state(state, "tool_executor", llm_logger)
        return state

    except Exception as exc:
        llm_logger.log_stage("TOOL_EXECUTOR ERROR", f"Ошибка: {exc}\n\n{traceback.format_exc()}")
        logger.error(f"Ошибка tool_executor_node: {exc}", exc_info=True)
        raise


def analyzer_node(state: AgentState) -> AgentState:
    """Создаёт сводку по результатам инструментов. Без LLM."""
    global _current_agent_state
    _current_agent_state = dict(state)  # Обновляем глобальный state
    
    llm_logger = _get_llm_logger()
    context = state.get("context", [])
    llm_logger.log_stage("ANALYZER START", f"Элементов контекста: {len(context)}")

    history = list(state.get("history", []))

    ok     = [e for e in context if not str(e.get("result", "")).startswith("ERROR")]
    errors = [e for e in context if str(e.get("result", "")).startswith("ERROR")]
    total_chars = sum(len(str(e.get("result", ""))) for e in ok)

    lines = [
        f"Выполнено инструментов: {len(context)} (успешно: {len(ok)}, ошибок: {len(errors)})",
        f"Суммарный объём данных: {total_chars} символов",
    ]
    if ok:
        lines.append("Источники:")
        for e in ok:
            lines.append(f"  - {e['tool']}({json.dumps(e['input'], ensure_ascii=False)[:80]}): {len(str(e['result']))} символов")
    if errors:
        lines.append("Ошибки:")
        for e in errors:
            lines.append(f"  - {e['tool']}: {e['result']}")

    summary = "\n".join(lines)
    history.append(HistoryToolSummary(tool_count=len(context), content=summary).model_dump())

    llm_logger.log_stage("ANALYZER COMPLETE", summary)
    logger.info(f"Analyzer: {len(ok)} успешных, {len(errors)} ошибок, {total_chars} символов")

    state["history"] = history
    
    _save_agent_state(state, "analyzer", llm_logger)
    return state


def refiner_node(state: AgentState) -> AgentState:
    """Всегда идёт в final. Без LLM — простота и надёжность."""
    global _current_agent_state
    _current_agent_state = dict(state)  # Обновляем глобальный state

    llm_logger = _get_llm_logger()

    history = list(state.get("history", []))
    history.append(HistoryRefinerSummary(
        decision="final",
        content=f"Данные собраны ({len(state.get('context', []))} результатов). Переходим к формированию ответа."
    ).model_dump())

    llm_logger.log_stage("REFINER", "Решение: → final (always)")

    state["next_node"] = "final"
    state["history"] = history
    
    _save_agent_state(state, "refiner", llm_logger)
    return state


def final_node(state: AgentState) -> AgentState:
    """Строит финальный ответ на основе накопленного контекста. Вызывает LLM."""
    global _current_agent_state
    _current_agent_state = dict(state)  # Обновляем глобальный state

    llm_logger = _get_llm_logger()
    context = state.get("context", [])
    llm_logger.log_stage("FINAL START", f"Контекст: {len(context)} результатов")

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentFinal)

        all_results_json = json.dumps(
            [{"tool": e["tool"], "input": e["input"], "result": _to_serializable(e["result"])} for e in context],
            ensure_ascii=False, indent=2
        )

        # Для совместимости с final/user.md: берём observation из последней tool_summary
        tool_summaries = [h for h in state.get("history", []) if h.get("type") == "tool_summary"]
        observation = tool_summaries[-1]["content"] if tool_summaries else "Результаты инструментов собраны."

        render_ctx = {
            "user_query": state["user_query"],
            "plan": state.get("plan", []),
            "observation": observation,
            "all_results_json": all_results_json,
            "system_prompt": _get_system_prompt(),
            "available_tools": _get_available_tools(),
            "schema_AgentFinal": get_final_schema(),
            "total_tools": len(context),
            "MAX_ITERATIONS": MAX_ITERATIONS,
        }
        system_msg = _prompt_loader.render_final_system(render_ctx)
        user_msg   = _prompt_loader.render_final_user(render_ctx)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {"callbacks": [LangChainFileLogger(llm_logger, step_prefix="FINAL")]}

        history = list(state.get("history", []))
        messages = _build_messages_from_history(system_msg, history)
        messages.append({"role": "user", "content": user_msg})

        result: AgentFinal = _invoke_llm_with_retry(
            structured_llm, messages, "final", llm_logger,
            retry_render_fn=lambda raw: _prompt_loader.render_final_retry(
                render_ctx, extra={"error_message": raw, "schema_AgentFinal": get_final_schema()}
            ),
            invoke_config=invoke_config,
        )

        history.append(HistoryLLMReply(
            node="final", content=pydantic_to_markdown(result)
        ).model_dump())

        llm_logger.log_stage(
            "FINAL COMPLETE",
            f"Summary: {result.final_answer.summary}\n"
            f"Confidence: {result.final_answer.confidence:.2f}\n"
            f"Sources: {len(result.final_answer.sources)}"
        )
        logger.info(
            f"Final: confidence={result.final_answer.confidence:.2f}, "
            f"sources={len(result.final_answer.sources)}"
        )

        state["final_answer"] = result.model_dump_json(indent=2)
        state["history"] = history
        _current_agent_state.update(state)  # Обновляем глобальный state перед сохранением

        _save_agent_state(state, "final", llm_logger)
        return state

    except Exception as exc:
        llm_logger.log_stage("FINAL ERROR", f"Ошибка: {exc}\n\n{traceback.format_exc()}")
        logger.error(f"Ошибка final_node: {exc}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Построение графа
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    """
    Линейный граф без условных ветвлений:
    planner → tool_selector → tool_executor → analyzer → refiner → final
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("planner",       planner_node)
    workflow.add_node("tool_selector", tool_selector_node)
    workflow.add_node("tool_executor", tool_executor_node)
    workflow.add_node("analyzer",      analyzer_node)
    workflow.add_node("refiner",       refiner_node)
    workflow.add_node("final",         final_node)

    workflow.set_entry_point("planner")
    workflow.add_edge("planner",       "tool_selector")
    workflow.add_edge("tool_selector", "tool_executor")
    workflow.add_edge("tool_executor", "analyzer")
    workflow.add_edge("analyzer",      "refiner")
    workflow.add_edge("refiner",       "final")
    workflow.add_edge("final",         END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Выполнение запроса
# ---------------------------------------------------------------------------

def run_query(question: str, verbose: bool = False, max_steps: int = None) -> dict:
    llm_logger = _get_llm_logger()
    llm_logger.log_stage("QUERY START", f"Вопрос: {question}\nMode: {'verbose' if verbose else 'normal'}")

    graph = build_graph()
    initial_state: AgentState = {
        "user_query": question,
        "plan": [],
        "tool_instructions": [],
        "context": [],
        "history": [],
        "next_node": "final",
        "final_answer": "",
    }

    try:
        if max_steps is not None:
            logger.info(f"🔍 РЕЖИМ ОТЛАДКИ: максимум {max_steps} шагов")
            final_state = initial_state
            steps_done = 0
            for step_output in graph.stream(initial_state):
                steps_done += 1
                node_name = list(step_output.keys())[0]
                final_state = step_output[node_name]
                logger.info(f"  Шаг {steps_done}/{max_steps}: '{node_name}'")
                if steps_done >= max_steps:
                    logger.info(f"🛑 ОСТАНОВКА: лимит {max_steps} шагов")
                    llm_logger.log_stage("DEBUG STOP", f"Выполнено шагов: {steps_done}, последний: {node_name}")
                    break
            llm_logger.log_stage("QUERY INCOMPLETE (DEBUG)",
                f"Шагов: {steps_done}, context: {len(final_state.get('context', []))}"
            )
        else:
            final_state = graph.invoke(initial_state)
            llm_logger.log_stage("QUERY COMPLETE",
                f"context: {len(final_state.get('context', []))} results\n"
                f"history: {len(final_state.get('history', []))} entries\n"
                f"answer: {len(final_state.get('final_answer', ''))} chars"
            )

        return final_state

    except Exception as exc:
        llm_logger.log_stage("QUERY ERROR", f"Ошибка: {exc}\n\nВопрос: {question}\n\n{traceback.format_exc()}")
        logger.error(f"Ошибка выполнения графа: {exc}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Вывод результатов
# ---------------------------------------------------------------------------

def print_result(question: str, state: dict, verbose: bool = False, max_steps: int = None) -> None:
    SEP = "=" * 80

    context = state.get("context", [])
    history = state.get("history", [])
    plan    = state.get("plan", [])

    print(f"\n{SEP}")
    print(f"Вопрос: {question}")
    if max_steps:
        print(f"🔍 РЕЖИМ ОТЛАДКИ: лимит {max_steps} шагов")
    print(f"Context: {len(context)} результатов инструментов")
    print(f"History: {len(history)} записей")
    print(SEP)

    if verbose:
        print("\nПлан:")
        for i, step in enumerate(plan, 1):
            print(f"  {i}. {step}")

        print(f"\nВыполненные инструменты:")
        for e in context:
            print(f"  - {e['tool']}({json.dumps(e['input'], ensure_ascii=False)[:60]})")

        print(f"\nИстория:")
        for h in history:
            t = h.get("type", "?")
            if t == "llm_reply":
                print(f"  [{t}] node={h.get('node')} ({len(h.get('content', ''))} chars)")
            elif t == "tool_execution":
                print(f"  [{t}] {h.get('tool')} ({len(h.get('result_md', ''))} chars)")
            else:
                print(f"  [{t}] ({len(str(h.get('content', '')))} chars)")

    print(f"\n{SEP}")
    if max_steps:
        print("🔍 ОТЛАДКА — СОСТОЯНИЕ НА МОМЕНТ ОСТАНОВКИ:")
    else:
        print("ОТВЕТ:")
    print(SEP)

    if not state.get("final_answer"):
        if max_steps:
            print("\n⚠️  Выполнение остановлено до формирования финального ответа")
            if plan:
                print(f"\n📊 Текущее состояние:")
                print(f"  - Plan: {len(plan)} шагов")
                print(f"  - Context: {len(context)} результатов")
        else:
            print("\n⚠️  Финальный ответ не был сформирован")
        print(SEP)
        return

    try:
        answer_data = json.loads(state["final_answer"])
        fa = answer_data.get("final_answer", {})

        print(f"\n📝 Summary:\n  {fa.get('summary', 'N/A')}")
        print(f"\n📋 Details:\n  {fa.get('details', 'N/A')}")

        if fa.get("data"):
            print(f"\n📊 Data:")
            for item in fa["data"]:
                print(f"  - {item.get('entity')}: {item.get('attribute')} = {item.get('value')}")

        if fa.get("sources"):
            print(f"\n📚 Sources:")
            for src in fa["sources"]:
                print(f"  - {src}")

        if fa.get("recommendations"):
            high = [r for r in fa["recommendations"] if r.get("relevance") == "high"]
            med  = [r for r in fa["recommendations"] if r.get("relevance") == "medium"]
            if high:
                print(f"\n  🔥 Точно помогут:")
                for r in high:
                    print(f"    📄 [{r.get('source')}] — {r.get('section')}")
                    print(f"       ℹ️  {r.get('reason')}")
            if med:
                print(f"\n  📌 Полезные по теме:")
                for r in med:
                    print(f"    📄 [{r.get('source')}] — {r.get('section')}")
                    print(f"       ℹ️  {r.get('reason')}")

        if fa.get("self_assessment"):
            print(f"\n🔍 Self-assessment:\n  {fa['self_assessment']}")

        print(f"\n🎯 Confidence: {fa.get('confidence', 0):.2%}")

    except Exception as exc:
        print(state["final_answer"])
        logger.error(f"Ошибка парсинга final_answer: {exc}")

    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный режим
# ---------------------------------------------------------------------------

def run_interactive(verbose: bool = False, max_steps: int = None) -> None:
    SEP = "=" * 80
    print(f"\n{SEP}")
    print("RAG-агент v3 (planner → tool_selector → tool_executor → analyzer → refiner → final)")
    if max_steps:
        print(f"  🔍 РЕЖИМ ОТЛАДКИ: максимум {max_steps} шагов")
    print("  /verbose — переключить подробный вывод")
    print("  exit / quit / выход — выйти")
    print(f"{SEP}\n")

    while True:
        try:
            question = input("Вопрос: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "выход"):
            print("До свидания!")
            break
        if question.lower() == "/verbose":
            verbose = not verbose
            print(f"Verbose: {'включён' if verbose else 'выключен'}.")
            continue

        try:
            state = run_query(question, verbose=verbose, max_steps=max_steps)
            print_result(question, state, verbose=verbose, max_steps=max_steps)
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break
        except Exception as exc:
            _fatal_error("run_interactive", exc, question=question)
            print("\n[!] Продолжаем работу...\n")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAG-агент v3: planner → tool_selector → tool_executor → analyzer → refiner → final"
    )
    parser.add_argument("question", nargs="*", help="Вопрос (если не указан — интерактивный режим)")
    parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    parser.add_argument("--steps", type=int, metavar="N", help="Режим отладки: выполнить только N шагов")
    return parser.parse_args()


def clear_logs_directory() -> None:
    logs_dir = Path(__file__).parent / "logs"
    if not logs_dir.exists():
        return
    deleted = 0
    for f in logs_dir.glob("[0-9][0-9][0-9]_*.log"):
        try:
            f.unlink()
            deleted += 1
        except Exception as e:
            logger.warning(f"Не удалось удалить {f.name}: {e}")
    old = logs_dir / "_rag_llm.log"
    if old.exists():
        try:
            old.unlink()
            deleted += 1
        except Exception as e:
            logger.warning(f"Не удалось удалить _rag_llm.log: {e}")
    if deleted:
        logger.info(f"Очищена директория logs: удалено {deleted} файлов")
    global _llm_logger
    if _llm_logger is not None:
        with _llm_logger._lock:
            _llm_logger._counter = 0
        logger.info("Счетчик нумерации логов сброшен")


def main() -> None:
    args = parse_args()
    clear_logs_directory()

    logger.info(
        f"Запуск RAG-агента v3 (planner → tool_selector → tool_executor → analyzer → refiner → final)\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  ClickHouse:  {settings.clickhouse_host}:{settings.clickhouse_port}"
        + (f"\n  🔍 ОТЛАДКА: {args.steps} шагов" if args.steps else "")
    )

    question = " ".join(args.question) if args.question else None

    try:
        if question:
            state = run_query(question, verbose=args.verbose, max_steps=args.steps)
            print_result(question, state, verbose=args.verbose, max_steps=args.steps)
        else:
            run_interactive(verbose=args.verbose, max_steps=args.steps)
    except (EOFError, KeyboardInterrupt):
        print("\nПрерывание пользователем.")
        sys.exit(0)
    except Exception as exc:
        _fatal_error(
            "main", exc,
            question=question or "(интерактивный режим)",
            model=settings.ollama_model,
            knowledge_dir=settings.knowledge_dir,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

