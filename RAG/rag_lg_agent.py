"""
LangGraph-агент для итеративного анализа с уточнениями.

АРХИТЕКТУРА (несколько проходов с уточнениями, до 5 итераций):
    START -> plan_node -> action_node -> observation_node -> refine_node
                              ↑                                   ↓
                              +--------- [нужно уточнение] -------+
                                                 ↓ [достаточно данных]
                                           final_node -> END

Следует system_prompt.md с добавлением этапа refine:
  - plan: анализ вопроса, построение стратегии
  - action: вызов tools (первичный или уточняющий)
  - observation: анализ результатов
  - refine: решение - достаточно ли данных или нужны уточнения
  - final: формирование ответа

КЛЮЧЕВЫЕ ОСОБЕННОСТИ:
  - До 5 итераций уточнения (plan + 5 action/observation циклов)
  - Автоматическое расширение контекста через get_neighbor_chunks
    * Анализирует результаты поиска и находит line_start метки
    * Автоматически вызывает get_neighbor_chunks для расширения контекста
    * Максимум 5 расширений на итерацию (по 3 чанка до и после)
  - После каждой observation - решение о продолжении
  - Уточняющие запросы используют targeted tools: find_relevant_sections,
    get_chunks_by_index, exact_search_in_file_section
  - Полное логирование всех messages и операций расширения контекста
  - Строгое следование JSON schema из system_prompt.md

Использование:
    python rag_lg_agent.py "найди все СУБД"
    python rag_lg_agent.py --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
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
import prompts  # Централизованное хранилище промптов
from logging_config import setup_logging
from pydantic_utils import pydantic_to_markdown

logger = setup_logging("rag_lg_agent_v2")


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 5  # Максимум итераций уточнения
AUTO_EXPAND_CONTEXT = True  # Автоматическое расширение контекста через get_neighbor_chunks


# ---------------------------------------------------------------------------
# Состояние агента (State)
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """Состояние итеративного агента."""
    user_query: str
    step: int
    iteration: int  # Номер итерации (1, 2, 3)
    messages: list[dict[str, Any]]  # История LLM сообщений
    plan: list[str]  # План поиска
    tool_calls: list[dict[str, Any]]  # [{tool, input}] текущей итерации
    all_tool_results: list[dict[str, Any]]  # Все результаты всех итераций
    observation: str  # Анализ результатов текущей итерации
    needs_refinement: bool  # Нужны ли уточняющие запросы
    refinement_plan: list[str]  # План уточнения (если нужен)
    final_answer: str  # Итоговый ответ (JSON)
    # Дополнительные поля для промптов
    system_prompt: str  # Системный промпт (загружается один раз)
    MAX_ITERATIONS: int  # Максимальное количество итераций


# ---------------------------------------------------------------------------
# Pydantic модели для JSON ответов (следуют system_prompt.md)
# ---------------------------------------------------------------------------

class AgentPlan(BaseModel):
    """Ответ на этапе plan (следует system_prompt.md)."""
    status: Literal["plan"] = Field(default="plan")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение (1-2 предложения)")
    plan: list[str] = Field(
        description="Список шагов плана поиска (3-5 пунктов)"
    )


class ToolAction(BaseModel):
    """Один вызов инструмента."""
    tool: str = Field(description="Имя инструмента")
    input: dict[str, Any] = Field(description="Параметры инструмента")


class AgentAction(BaseModel):
    """Ответ на этапе action (следует system_prompt.md)."""
    status: Literal["action"] = Field(default="action")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    action: list[ToolAction] = Field(
        description="Список вызовов инструментов (2-4 штуки для параллельного выполнения)"
    )


class AgentObservation(BaseModel):
    """Ответ на этапе observation (следует system_prompt.md)."""
    status: Literal["observation"] = Field(default="observation")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    observation: str = Field(
        description="Анализ результатов: что нашли, что не нашли, что важно"
    )


class AgentRefine(BaseModel):
    """Ответ на этапе refine - решение о продолжении."""
    status: Literal["refine"] = Field(default="refine")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    needs_refinement: bool = Field(
        description="True если нужны уточняющие запросы, False если данных достаточно"
    )
    refinement_plan: list[str] = Field(
        default_factory=list,
        description="План уточнения (если needs_refinement=True)"
    )


class FinalAnswerData(BaseModel):
    """Структурированные данные в ответе."""
    entity: str
    attribute: str
    value: str


class RecommendedSection(BaseModel):
    """Рекомендованный раздел документации для изучения."""
    source: str = Field(description="Источник (файл)")
    section: str = Field(description="Название раздела")
    relevance: Literal["high", "medium"] = Field(
        description="Уровень релевантности: high - прям точно помогут, medium - вроде полезные по теме"
    )
    reason: str = Field(description="Почему этот раздел может быть полезен")


class FinalAnswer(BaseModel):
    """Финальный ответ (inner structure)."""
    summary: str = Field(description="Краткий ответ")
    details: str = Field(description="Подробное объяснение")
    data: list[FinalAnswerData] = Field(
        default_factory=list,
        description="Структурированные данные"
    )
    sources: list[str] = Field(default_factory=list, description="Источники")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность (0-1)")
    recommendations: list[RecommendedSection] = Field(
        default_factory=list,
        description="Рекомендованные разделы документации для дальнейшего изучения"
    )


class AgentFinal(BaseModel):
    """Ответ на этапе final (следует system_prompt.md)."""
    status: Literal["final"] = Field(default="final")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    final_answer: FinalAnswer = Field(description="Итоговый ответ")


# ---------------------------------------------------------------------------
# LLM call logger (singleton)
# ---------------------------------------------------------------------------

_llm_logger: LlmCallLogger | None = None


def _get_llm_logger() -> LlmCallLogger:
    global _llm_logger
    if _llm_logger is None:
        _llm_logger = LlmCallLogger(
            enabled=settings.llm_log_enabled,
            log_dir=Path(__file__).parent / "logs",
            stream_to_console=True,
        )
    return _llm_logger


# ---------------------------------------------------------------------------
# Загрузка system prompt из файла
# ---------------------------------------------------------------------------

_TOOLS_JSON_CACHE: str | None = None  # Кэш для JSON списка инструментов

def _build_tools_json(compact: bool = False) -> str:
    global _TOOLS_JSON_CACHE

    if _TOOLS_JSON_CACHE is not None:
        # Для компактного формата конвертируем кэшированный JSON
        if compact:
            try:
                tools_list = json.loads(_TOOLS_JSON_CACHE)
                return json.dumps(tools_list, ensure_ascii=False, separators=(',', ':'))
            except Exception:
                pass
        return _TOOLS_JSON_CACHE

    tool_registry = get_tool_registry()
    
    # Создаем временный экземпляр tools чтобы получить args_schema
    temp_tools = []
    try:
        import rag_chat
        temp_vectorstore = rag_chat.build_vectorstore()
        knowledge_dir = Path(rag_chat.settings.knowledge_dir)
        temp_tools = create_kb_tools(temp_vectorstore, knowledge_dir)
        logger.info(f"Получено {len(temp_tools)} инструментов для генерации JSON")
    except Exception as e:
        logger.warning(f"Не удалось получить схемы параметров инструментов: {e}")
    
    # Создаем словарь {tool_name: args_schema}
    tool_schemas = {}
    for tool in temp_tools:
        if hasattr(tool, 'name') and hasattr(tool, 'args_schema'):
            tool_schemas[tool.name] = tool.args_schema
    
    # Формируем массив инструментов с параметрами
    tools_list = []
    for tool_name, description in tool_registry.items():
        tool_info = {
            "name": tool_name,
            "description": description,
            "parameters": {}
        }

        # Добавляем схему параметров если доступна
        if tool_name in tool_schemas:
            schema = tool_schemas[tool_name]
            if schema and hasattr(schema, 'model_json_schema'):
                # Получаем JSON Schema из Pydantic
                json_schema = schema.model_json_schema()
                # Извлекаем properties и required
                tool_info["parameters"] = {
                    "type": "object",
                    "properties": json_schema.get("properties", {}),
                    "required": json_schema.get("required", [])
                }
        
        tools_list.append(tool_info)

    # Сериализуем в JSON с отступами для читаемости
    result = json.dumps(tools_list, ensure_ascii=False, indent=2)
    _TOOLS_JSON_CACHE = result
    logger.info(f"Сгенерирован JSON списка инструментов: {len(result)} символов, {len(tools_list)} инструментов")
    return result


def _load_system_prompt() -> str:
    """
    Загружает и форматирует базовый системный промпт.
    
    Теперь использует Jinja2 шаблоны из prompts/system_prompt_base.md
    Placeholder {{ available_tools }} заменяется на JSON со списком инструментов.
    """
    # Подготавливаем компактный JSON со списком инструментов
    tools_json_compact = _build_tools_json(compact=True)
    # Форматируем JSON для читаемости (добавляем переносы после каждого инструмента)
    try:
        tools_list = json.loads(tools_json_compact)
        # Красиво форматируем: каждый инструмент на новой строке
        tools_formatted = "[\n  " + ",\n  ".join(
            json.dumps(t, ensure_ascii=False, separators=(',', ':')) 
            for t in tools_list
        ) + "\n]"
    except Exception:
        tools_formatted = tools_json_compact
    
    # Создаем state для рендеринга шаблона
    state = {
        'available_tools': tools_formatted
    }
    
    # Получаем промпт из Jinja2 шаблона (с автоматической подстановкой {{ available_tools }})
    prompt = prompts.get_system_prompt_base(state)
    
    return prompt


_SYSTEM_PROMPT = _load_system_prompt()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _fix_tool_args(tool_name: str, tool_input: dict) -> dict:
    fixed = tool_input.copy()

    # find_sections_by_term: LLM часто использует 'term' вместо 'substring'
    if tool_name == "find_sections_by_term":
        if "term" in fixed and "substring" not in fixed:
            fixed["substring"] = fixed.pop("term")
        if "query" in fixed and "substring" not in fixed:
            fixed["substring"] = fixed.pop("query")

    # exact_search_in_file: LLM часто использует 'source'/'file' вместо 'source_file'
    if tool_name in ["exact_search_in_file", "exact_search_in_file_section"]:
        if "source" in fixed and "source_file" not in fixed:
            fixed["source_file"] = fixed.pop("source")
        if "file" in fixed and "source_file" not in fixed:
            fixed["source_file"] = fixed.pop("file")

    # get_section_content: LLM часто использует 'source'/'file' вместо 'source_file'
    if tool_name == "get_section_content":
        if "source" in fixed and "source_file" not in fixed:
            fixed["source_file"] = fixed.pop("source")
        if "file" in fixed and "source_file" not in fixed:
            fixed["source_file"] = fixed.pop("file")
    
    # get_neighbor_chunks: LLM часто путает с get_chunks_by_index
    # Если есть chunk_indices, но нет line_start - это ошибка LLM
    if tool_name == "get_neighbor_chunks":
        if "chunk_indices" in fixed:
            logger.warning(
                f"LLM перепутал get_neighbor_chunks с get_chunks_by_index. "
                f"get_neighbor_chunks требует line_start, а не chunk_indices. "
                f"Пропускаем некорректный вызов."
            )
            # Сообщаем об ошибке в параметрах
            raise ValueError(
                "get_neighbor_chunks requires 'line_start' parameter, not 'chunk_indices'. "
                "LLM probably confused it with get_chunks_by_index tool."
            )

    return fixed


def _auto_expand_context(
    tool_results: list[dict[str, Any]],
    tools_map: dict[str, Any],
    llm_logger: LlmCallLogger,
    iteration: int,
    max_expansions: int = 5
) -> list[dict[str, Any]]:
    if not AUTO_EXPAND_CONTEXT:
        return []
    
    if "get_neighbor_chunks" not in tools_map:
        logger.warning("get_neighbor_chunks не найден в tools_map, пропускаем расширение контекста")
        return []
    
    expansion_results = []
    expansions_count = 0
    
    # Ищем в результатах упоминания line_start и source
    for tr in tool_results:
        if expansions_count >= max_expansions:
            logger.info(f"Достигнут лимит расширений ({max_expansions})")
            break
        
        tool_name = tr['tool']
        result_str = str(tr['result'])
        
        # Пропускаем ошибки и уже расширенные контексты
        if "ERROR" in result_str or tool_name == "get_neighbor_chunks":
            continue
        
        # Простой парсинг результатов для извлечения метаданных
        # Ищем паттерны типа "[file.md] — Section (line 123)"
        import re
        
        # Паттерн для извлечения source и line_start
        pattern = r'\[([^\]]+\.md)\][^\(]*\(line (\d+)\)'
        matches = re.findall(pattern, result_str)
        
        if not matches:
            continue
        
        # Берём первую найденную пару (source, line_start)
        for source, line_start_str in matches[:2]:  # Максимум 2 расширения на результат
            if expansions_count >= max_expansions:
                break
            
            try:
                line_start = int(line_start_str)
                
                logger.info(
                    f"Автоматическое расширение контекста: "
                    f"source={source}, line_start={line_start}"
                )
                
                # Вызываем get_neighbor_chunks
                expand_input = {
                    "source": source,
                    "line_start": line_start,
                    "before": 3,  # 3 чанка до
                    "after": 3    # 3 чанка после
                }
                
                expand_result = tools_map["get_neighbor_chunks"].invoke(expand_input)
                
                expansion_results.append({
                    "tool": "get_neighbor_chunks",
                    "input": expand_input,
                    "result": str(expand_result),
                    "auto_expanded": True  # Маркер автоматического расширения
                })
                
                expansions_count += 1
                logger.info(
                    f"Контекст расширен ({expansions_count}/{max_expansions}): "
                    f"{len(str(expand_result))} символов"
                )
                
            except Exception as exc:
                logger.warning(f"Ошибка расширения контекста для {source}:{line_start_str}: {exc}")
                continue
    
    if expansion_results:
        llm_logger.log_stage(
            f"AUTO CONTEXT EXPANSION (iteration {iteration})",
            f"Автоматически расширено контекстов: {len(expansion_results)}\n"
            + "\n".join(
                f"  - {er['input']['source']}:line {er['input']['line_start']} "
                f"(±{er['input']['before']}/{er['input']['after']} chunks)"
                for er in expansion_results
            )
        )
    
    return expansion_results


# ---------------------------------------------------------------------------
# Узлы графа (Nodes) - следуют system_prompt.md
# ---------------------------------------------------------------------------

def plan_node(state: AgentState) -> AgentState:
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "PLAN NODE START",
        f"Вопрос: {state['user_query']}"
    )

    try:
        # Инициализируем LLM с structured output
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentPlan)

        # Подготовка state для промптов
        state['system_prompt'] = _SYSTEM_PROMPT
        state['MAX_ITERATIONS'] = MAX_ITERATIONS
        
        # Формируем системный промпт
        # Используем функции из prompts.py для генерации промптов
        system_message = prompts.get_plan_system_prompt(state)
        user_message = prompts.get_plan_user_prompt(state)

        # Логирование через callback
        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix="PLAN")]
            }

        # Вызов LLM (с retry при ошибках парсинга)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result: AgentPlan = structured_llm.invoke(
                    [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message}
                    ],
                    config=invoke_config,
                )
                break  # Успешно распарсили - выходим из цикла
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning(f"Ошибка парсинга JSON в plan_node (попытка {attempt + 1}/{max_retries}): {exc}")
                    
                    # Для plan_node используем простой список сообщений (пока не инициализирована история)
                    user_message += prompts.get_plan_retry_prompt(state)
                    continue
                else:
                    logger.error(f"Не удалось распарсить JSON после {max_retries} попыток: {exc}", exc_info=True)
                    raise

        # Обновляем state
        state["step"] = 1
        state["iteration"] = 1
        state["plan"] = result.plan
        state["messages"] = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": pydantic_to_markdown(result)}
        ]

        logger.info(
            f"Plan node завершён\n"
            f"  Thought: {result.thought}\n"
            f"  План ({len(result.plan)} шагов): {result.plan}"
        )

        llm_logger.log_stage(
            "PLAN NODE COMPLETE",
            f"Thought: {result.thought}\n"
            + "\n".join(f"  {i+1}. {step}" for i, step in enumerate(result.plan))
        )

    except Exception as exc:
        logger.error(f"Ошибка plan_node: {exc}", exc_info=True)
        state["plan"] = ["Поиск по ключевым словам"]
        llm_logger.log_stage("PLAN NODE ERROR", f"Ошибка: {exc}")

    return state


def action_node(state: AgentState) -> AgentState:
    llm_logger = _get_llm_logger()
    iteration = state.get("iteration", 1)
    llm_logger.log_stage(
        f"ACTION NODE START (iteration {iteration})",
        f"План: {state.get('refinement_plan') or state['plan']}"
    )

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentAction)

        # Подготовка state для промптов
        state['system_prompt'] = _SYSTEM_PROMPT
        state['MAX_ITERATIONS'] = MAX_ITERATIONS
        
        # Используем функции из prompts.py для генерации промптов
        system_message = prompts.get_action_system_prompt(state)
        user_message = prompts.get_action_user_prompt(state)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix=f"ACTION_{iteration}")]
            }

        # Формируем список сообщений: system message + история + новый user message
        messages = [{"role": "system", "content": system_message}]
        # Добавляем историю (пропускаем первый system message из plan_node)
        for msg in state["messages"]:
            if msg["role"] != "system":  # Пропускаем старые system messages
                messages.append(msg)
        # Добавляем текущий user message
        messages.append({"role": "user", "content": user_message})
        
        # Сохраняем user message в историю
        state["messages"].append({"role": "user", "content": user_message})

        # Вызов LLM с полной историей (с retry при ошибках парсинга)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result: AgentAction = structured_llm.invoke(messages, config=invoke_config)
                break  # Успешно распарсили - выходим из цикла
            except Exception as exc:
                if attempt < max_retries - 1:
                    # Не последняя попытка - пробуем еще раз с более строгими инструкциями
                    logger.warning(f"Ошибка парсинга JSON (попытка {attempt + 1}/{max_retries}): {exc}")
                    
                    # Добавляем сообщение об ошибке и требование правильного формата
                    error_message = prompts.get_action_retry_prompt(state)
                    
                    messages.append({"role": "user", "content": error_message})
                    continue
                else:
                    # Последняя попытка - выбрасываем исключение
                    logger.error(f"Не удалось распарсить JSON после {max_retries} попыток: {exc}", exc_info=True)
                    raise

        # Обновляем state
        state["step"] = state.get("step", 1) + 1
        state["tool_calls"] = [
            {"tool": action.tool, "input": action.input}
            for action in result.action
        ]
        state["messages"].append({
            "role": "assistant",
            "content": pydantic_to_markdown(result)
        })

        logger.info(
            f"Action node (iteration {iteration}) завершён\n"
            f"  Thought: {result.thought}\n"
            f"  Выбрано инструментов: {len(result.action)}"
        )

        llm_logger.log_stage(
            f"ACTION NODE COMPLETE (iteration {iteration})",
            f"Thought: {result.thought}\n"
            + "\n".join(
                f"  - {a.tool}({json.dumps(a.input, ensure_ascii=False)})"
                for a in result.action
            )
        )

        # Выполняем инструменты
        vectorstore = build_vectorstore(force_reindex=False)
        knowledge_dir = Path(settings.knowledge_dir)
        tools_list = create_kb_tools(
            vectorstore=vectorstore,
            knowledge_dir=knowledge_dir,
            semantic_top_k=settings.retriever_top_k,
            llm_logger=llm_logger,
        )

        # Создаём маппинг tool_name -> tool
        tools_map = {t.name: t for t in tools_list}

        # Выполняем все tool calls
        tool_results = []
        for tc in state["tool_calls"]:
            tool_name = tc["tool"]
            tool_input = tc["input"]

            # Автоисправление некорректных параметров от LLM
            try:
                tool_input = _fix_tool_args(tool_name, tool_input)
            except ValueError as e:
                logger.error(f"Некорректные параметры для {tool_name}: {e}")
                tool_results.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "result": f"ERROR: {e}"
                })
                continue

            if tool_name in tools_map:
                try:
                    logger.info(f"Выполнение {tool_name} с параметрами: {tool_input}")
                    result_raw = tools_map[tool_name].invoke(tool_input)
                    
                    # Сохраняем результат как структурированный объект (dict/list), НЕ как строку
                    if hasattr(result_raw, "model_dump"):
                        # Pydantic модель → dict (сохраняет структуру!)
                        result_dict = result_raw.model_dump()
                    elif isinstance(result_raw, (dict, list)):
                        # Уже dict/list - оставляем как есть
                        result_dict = result_raw
                    else:
                        # Примитивный тип (str, int, etc.) - оборачиваем в dict
                        result_dict = {"value": str(result_raw)}

                    tool_results.append({
                        "tool": tool_name,
                        "input": tool_input,
                        "result": result_dict  # ← Храним как dict/list, НЕ как строку!
                    })
                    
                    # Для логов считаем размер JSON
                    result_size = len(json.dumps(result_dict, ensure_ascii=False))
                    logger.info(f"{tool_name} завершён, результат: {result_size} байт JSON")
                except Exception as exc:
                    logger.error(f"Ошибка выполнения {tool_name}: {exc}", exc_info=True)
                    tool_results.append({
                        "tool": tool_name,
                        "input": tool_input,
                        "result": f"ERROR: {exc}"
                    })
            else:
                logger.warning(f"Инструмент {tool_name} не найден")
                tool_results.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "result": f"ERROR: Tool {tool_name} not found"
                })

        # Добавляем результаты ко всем результатам
        if "all_tool_results" not in state:
            state["all_tool_results"] = []
        state["all_tool_results"].extend(tool_results)

        # Добавляем tool messages в историю
        # Храним result как dict, НЕ как JSON строку
        for idx, tr in enumerate(tool_results):
            state["messages"].append({
                "role": "tool",
                "name": tr["tool"],
                "tool_call_id": f"call_{iteration}_{idx}",
                "content": tr  # ← Храним весь tr (dict) как есть, НЕ json.dumps!
            })

        llm_logger.log_stage(
            f"TOOLS EXECUTION COMPLETE (iteration {iteration})",
            f"Выполнено {len(tool_results)} инструментов\n"
            + "\n".join(
                f"  - {tr['tool']}: {len(str(tr['result']))} символов"
                for tr in tool_results
            )
        )

        # Автоматическое расширение контекста
        try:
            expansion_results = _auto_expand_context(
                tool_results,
                tools_map,
                llm_logger,
                iteration
            )
            state["all_tool_results"].extend(expansion_results)
        except Exception as e:
            logger.warning(f"Ошибка при автоматическом расширении контекста: {e}")

    except Exception as exc:
        logger.error(f"Ошибка action_node: {exc}", exc_info=True)
        state["tool_calls"] = []
        llm_logger.log_stage(f"ACTION NODE ERROR (iteration {iteration})", f"Ошибка: {exc}")

    return state


def observation_node(state: AgentState) -> AgentState:
    llm_logger = _get_llm_logger()
    iteration = state.get("iteration", 1)
    llm_logger.log_stage(
        f"OBSERVATION NODE START (iteration {iteration})",
        f"Результатов: {len(state.get('all_tool_results', []))}"
    )

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentObservation)

        # Последние результаты (текущей итерации)
        current_results = state["all_tool_results"][-len(state["tool_calls"]):]
        
        # Формируем JSON с результатами (БЕЗ ОБРЕЗКИ!)
        tools_data = []
        for tr in current_results:
            tool_entry = {
                "tool": tr['tool'],
                "input": tr['input'],
                "result": tr['result']  # Уже dict, не нужно конвертировать
            }
            tools_data.append(tool_entry)
        
        # Сериализуем в JSON для LLM
        tools_json = json.dumps(tools_data, ensure_ascii=False, indent=2)

        # Подготовка state для промптов
        state['system_prompt'] = _SYSTEM_PROMPT
        state['MAX_ITERATIONS'] = MAX_ITERATIONS
        state['tools_json'] = tools_json
        
        # Используем функции из prompts.py для генерации промптов
        system_message = prompts.get_observation_system_prompt(state)
        user_message = prompts.get_observation_user_prompt(state)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix=f"OBSERVATION_{iteration}")]
            }

        # Формируем список сообщений: system message + история + новый user message
        messages = [{"role": "system", "content": system_message}]
        # Добавляем историю (пропускаем старые system messages)
        for msg in state["messages"]:
            if msg["role"] != "system":
                messages.append(msg)
        # Добавляем текущий user message
        messages.append({"role": "user", "content": user_message})
        
        # Сохраняем user message в историю
        state["messages"].append({"role": "user", "content": user_message})

        # Вызов LLM с полной историей (с retry при ошибках парсинга)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result: AgentObservation = structured_llm.invoke(messages, config=invoke_config)
                break  # Успешно распарсили - выходим из цикла
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning(f"Ошибка парсинга JSON в observation_node (попытка {attempt + 1}/{max_retries}): {exc}")
                    
                    error_message = f"""⚠️ ОШИБКА ПАРСИНГА JSON!

ОБЯЗАТЕЛЬНАЯ структура для этапа observation:
{{
  "status": "observation",  ← ОБЯЗАТЕЛЬНО "observation"!
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение",
  "observation": "подробный анализ результатов"  ← ОБЯЗАТЕЛЬНО строка!
}}

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше."""
                    
                    messages.append({"role": "user", "content": error_message})
                    continue
                else:
                    logger.error(f"Не удалось распарсить JSON после {max_retries} попыток: {exc}", exc_info=True)
                    raise

        state["step"] = state.get("step", 1) + 1
        state["observation"] = result.observation
        state["messages"].append({
            "role": "assistant",
            "content": pydantic_to_markdown(result)
        })

        logger.info(
            f"Observation node (iteration {iteration}) завершён\n"
            f"  Thought: {result.thought}\n"
            f"  Observation: {result.observation[:200]}..."
        )

        llm_logger.log_stage(
            f"OBSERVATION NODE COMPLETE (iteration {iteration})",
            f"Thought: {result.thought}\n"
            f"Observation:\n{result.observation}"
        )

    except Exception as exc:
        logger.error(f"Ошибка observation_node: {exc}", exc_info=True)
        state["observation"] = "Ошибка анализа результатов"
        llm_logger.log_stage(f"OBSERVATION NODE ERROR (iteration {iteration})", f"Ошибка: {exc}")

    return state


def refine_node(state: AgentState) -> AgentState:
    llm_logger = _get_llm_logger()
    iteration = state.get("iteration", 1)
    llm_logger.log_stage(
        f"REFINE NODE START (iteration {iteration})",
        f"Observation: {state['observation'][:200]}..."
    )

    # Проверка лимита итераций
    if iteration >= MAX_ITERATIONS:
        logger.info(f"Достигнут лимит итераций ({MAX_ITERATIONS}), переход к final")
        state["needs_refinement"] = False
        state["refinement_plan"] = []
        return state

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentRefine)

        # Подготовка state для промптов
        state['system_prompt'] = _SYSTEM_PROMPT
        state['MAX_ITERATIONS'] = MAX_ITERATIONS
        
        # Используем функции из prompts.py для генерации промптов
        system_message = prompts.get_refine_system_prompt(state)
        user_message = prompts.get_refine_user_prompt(state)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix=f"REFINE_{iteration}")]
            }

        # Формируем список сообщений: system message + история + новый user message
        messages = [{"role": "system", "content": system_message}]
        # Добавляем историю (пропускаем старые system messages)
        for msg in state["messages"]:
            if msg["role"] != "system":
                messages.append(msg)
        # Добавляем текущий user message
        messages.append({"role": "user", "content": user_message})
        
        # Сохраняем user message в историю
        state["messages"].append({"role": "user", "content": user_message})

        # Вызов LLM с полной историей (с retry при ошибках парсинга)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result: AgentRefine = structured_llm.invoke(messages, config=invoke_config)
                break  # Успешно распарсили - выходим из цикла
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning(f"Ошибка парсинга JSON в refine_node (попытка {attempt + 1}/{max_retries}): {exc}")
                    
                    error_message = prompts.get_refine_retry_prompt(state)
                    
                    messages.append({"role": "user", "content": error_message})
                    continue
                else:
                    logger.error(f"Не удалось распарсить JSON после {max_retries} попыток: {exc}", exc_info=True)
                    raise

        state["step"] = state.get("step", 1) + 1
        state["needs_refinement"] = result.needs_refinement
        state["refinement_plan"] = result.refinement_plan
        state["messages"].append({
            "role": "assistant",
            "content": pydantic_to_markdown(result)
        })

        logger.info(
            f"Refine node (iteration {iteration}) завершён\n"
            f"  Thought: {result.thought}\n"
            f"  Needs refinement: {result.needs_refinement}\n"
            f"  Refinement plan: {result.refinement_plan if result.needs_refinement else 'N/A'}"
        )

        llm_logger.log_stage(
            f"REFINE NODE COMPLETE (iteration {iteration})",
            f"Thought: {result.thought}\n"
            f"Needs refinement: {result.needs_refinement}\n"
            + (f"Refinement plan:\n" + "\n".join(f"  {i+1}. {step}" for i, step in enumerate(result.refinement_plan)) if result.needs_refinement else "Данных достаточно")
        )

        # Увеличиваем счётчик итерации если продолжаем
        if result.needs_refinement:
            state["iteration"] = iteration + 1

    except Exception as exc:
        logger.error(f"Ошибка refine_node: {exc}", exc_info=True)
        state["needs_refinement"] = False
        state["refinement_plan"] = []
        llm_logger.log_stage(f"REFINE NODE ERROR (iteration {iteration})", f"Ошибка: {exc}")

    return state


def final_node(state: AgentState) -> AgentState:
    llm_logger = _get_llm_logger()
    total_tools = len(state.get("all_tool_results", []))
    llm_logger.log_stage(
        "FINAL NODE START",
        f"Всего выполнено инструментов: {total_tools}, итераций: {state.get('iteration', 1)}\n"
        f"Модель: {settings.ollama_final_model} (более мощная для финального ответа)"
    )

    try:
        # Используем более мощную модель для финального ответа
        llm = rag_chat.build_llm(model=settings.ollama_final_model)
        structured_llm = llm.with_structured_output(AgentFinal)
        
        logger.info(f"Final node использует модель: {settings.ollama_final_model}")

        # Подготовка полного контекста - все результаты как JSON (БЕЗ ОБРЕЗКИ!)
        all_results_data = []
        for tr in state.get("all_tool_results", []):
            result_entry = {
                "tool": tr['tool'],
                "input": tr['input'],
                "result": tr['result']  # Уже dict, не нужно конвертировать
            }
            all_results_data.append(result_entry)
        
        # Сериализуем в JSON для LLM
        all_results_json = json.dumps(all_results_data, ensure_ascii=False, indent=2)

        # Подготовка state для промптов
        state['system_prompt'] = _SYSTEM_PROMPT
        state['MAX_ITERATIONS'] = MAX_ITERATIONS
        state['total_tools'] = total_tools
        state['all_results_json'] = all_results_json
        
        # Используем функции из prompts.py для генерации промптов
        system_message = prompts.get_final_system_prompt(state)
        user_message = prompts.get_final_user_prompt(state)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix="FINAL")]
            }

        # Формируем список сообщений: system message + история + новый user message
        messages = [{"role": "system", "content": system_message}]
        # Добавляем историю (пропускаем старые system messages)
        for msg in state["messages"]:
            if msg["role"] != "system":
                messages.append(msg)
        # Добавляем текущий user message
        messages.append({"role": "user", "content": user_message})
        
        # Сохраняем user message в историю
        state["messages"].append({"role": "user", "content": user_message})

        # Вызов LLM с полной историей (с retry при ошибках парсинга)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result: AgentFinal = structured_llm.invoke(messages, config=invoke_config)
                break  # Успешно распарсили - выходим из цикла
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning(f"Ошибка парсинга JSON в final_node (попытка {attempt + 1}/{max_retries}): {exc}")
                    
                    error_message = f"""⚠️ ОШИБКА ПАРСИНГА JSON!

ОБЯЗАТЕЛЬНАЯ структура для этапа final:
{{
  "status": "final",  ← ОБЯЗАТЕЛЬНО "final"!
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение",
  "final_answer": {{  ← ОБЯЗАТЕЛЬНО объект с полями ниже!
    "summary": "краткий ответ",
    "details": "подробности",
    "data": [],  ← Массив объектов (может быть пустым)
    "sources": [],  ← Массив строк (может быть пустым)
    "confidence": 0.85  ← Число от 0.0 до 1.0
  }}
}}

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше."""
                    
                    messages.append({"role": "user", "content": error_message})
                    continue
                else:
                    logger.error(f"Не удалось распарсить JSON после {max_retries} попыток: {exc}", exc_info=True)
                    raise

        state["step"] = state.get("step", 1) + 1
        state["final_answer"] = result.model_dump_json(indent=2)  # JSON для парсинга в print_result
        state["messages"].append({
            "role": "assistant",
            "content": pydantic_to_markdown(result)  # Markdown для читаемости в логах
        })

        logger.info(
            f"Final node завершён\n"
            f"  Summary: {result.final_answer.summary}\n"
            f"  Confidence: {result.final_answer.confidence:.2f}\n"
            f"  Sources: {len(result.final_answer.sources)}\n"
            f"  Итераций: {state.get('iteration', 1)}"
        )

        llm_logger.log_stage(
            "FINAL NODE COMPLETE",
            f"Summary: {result.final_answer.summary}\n"
            f"Confidence: {result.final_answer.confidence:.2f}\n"
            f"Data entries: {len(result.final_answer.data)}\n"
            f"Sources: {len(result.final_answer.sources)}\n"
            f"Итераций: {state.get('iteration', 1)}"
        )

    except Exception as exc:
        logger.error(f"Ошибка final_node: {exc}", exc_info=True)
        state["final_answer"] = json.dumps({
            "status": "error",
            "error": str(exc)
        }, ensure_ascii=False, indent=2)
        llm_logger.log_stage("FINAL NODE ERROR", f"Ошибка: {exc}")

    return state


# ---------------------------------------------------------------------------
# Условный роутинг
# ---------------------------------------------------------------------------

def should_refine(state: AgentState) -> str:
    if state.get("needs_refinement", False) and state.get("iteration", 1) < MAX_ITERATIONS:
        logger.info(f"Продолжаем уточнение: iteration {state['iteration']}")
        return "action"
    else:
        logger.info(f"Переход к финалу: needs_refinement={state.get('needs_refinement')}, iteration={state.get('iteration')}")
        return "final"


# ---------------------------------------------------------------------------
# Построение графа
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    workflow = StateGraph(AgentState)

    # Добавляем узлы
    workflow.add_node("plan", plan_node)
    workflow.add_node("action", action_node)
    workflow.add_node("observation", observation_node)
    workflow.add_node("refine", refine_node)
    workflow.add_node("final", final_node)

    # Определяем последовательность
    workflow.set_entry_point("plan")
    workflow.add_edge("plan", "action")
    workflow.add_edge("action", "observation")
    workflow.add_edge("observation", "refine")

    # Условный роутинг от refine
    workflow.add_conditional_edges(
        "refine",
        should_refine,
        {
            "action": "action",  # Цикл обратно к action
            "final": "final"      # Переход к финалу
        }
    )

    workflow.add_edge("final", END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Выполнение запроса
# ---------------------------------------------------------------------------

def run_query(question: str, verbose: bool = False) -> dict:
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "QUERY START",
        f"""Вопрос: {question}
Режим: {'verbose' if verbose else 'normal'}
Максимум итераций: {MAX_ITERATIONS}"""
    )

    # Создаем граф
    graph = build_graph()

    # Инициализируем состояние
    initial_state: AgentState = {
        "user_query": question,
        "step": 0,
        "iteration": 1,
        "messages": [],
        "plan": [],
        "tool_calls": [],
        "all_tool_results": [],
        "observation": "",
        "needs_refinement": False,
        "refinement_plan": [],
        "final_answer": "",
    }

    # Запускаем граф
    try:
        final_state = graph.invoke(initial_state)

        llm_logger.log_stage(
            "QUERY COMPLETE",
            f"Шагов выполнено: {final_state['step']}\n"
            f"Итераций: {final_state.get('iteration', 1)}\n"
            f"Messages: {len(final_state['messages'])}\n"
            f"Tools executed: {len(final_state.get('all_tool_results', []))}\n"
            f"Длина ответа: {len(final_state['final_answer'])} символов"
        )

        return final_state

    except Exception as exc:
        logger.error(f"Ошибка выполнения графа: {exc}", exc_info=True)
        llm_logger.log_stage("QUERY ERROR", f"Ошибка: {exc}")
        raise


# ---------------------------------------------------------------------------
# Вывод результатов
# ---------------------------------------------------------------------------

def print_result(question: str, state: dict, verbose: bool = False) -> None:
    SEP = "=" * 80

    print(f"\n{SEP}")
    print(f"Вопрос: {question}")
    print(f"Шагов: {state['step']}")
    print(f"Итераций: {state.get('iteration', 1)}/{MAX_ITERATIONS}")
    print(f"Messages: {len(state['messages'])}")
    print(f"Tools executed: {len(state.get('all_tool_results', []))}")
    print(SEP)

    if verbose:
        print("\nПервоначальный план:")
        for i, step in enumerate(state['plan'], 1):
            print(f"  {i}. {step}")

        print(f"\nВсего выполнено инструментов: {len(state.get('all_tool_results', []))}")

        print(f"\nФинальная observation:")
        print(f"  {state['observation'][:300]}...")

        print(f"\nMessages history:")
        for i, msg in enumerate(state['messages'], 1):
            role = msg.get('role', 'unknown')
            content_len = len(str(msg.get('content', '')))
            print(f"  {i}. {role}: {content_len} символов")

    print(f"\n{SEP}")
    print("ОТВЕТ:")
    print(SEP)

    # Парсим и красиво выводим final_answer
    try:
        answer_data = json.loads(state['final_answer'])
        final_ans = answer_data.get('final_answer', {})

        print(f"\n📝 Summary:")
        print(f"  {final_ans.get('summary', 'N/A')}")

        print(f"\n📋 Details:")
        print(f"  {final_ans.get('details', 'N/A')}")

        if final_ans.get('data'):
            print(f"\n📊 Data:")
            for item in final_ans['data']:
                print(f"  - {item.get('entity')}: {item.get('attribute')} = {item.get('value')}")

        if final_ans.get('sources'):
            print(f"\n📚 Sources:")
            for src in final_ans['sources']:
                print(f"  - {src}")

        if final_ans.get('recommendations'):
            print(f"\n💡 Рекомендованные разделы для изучения:")
            
            # Разделы с высокой релевантностью
            high_relevance = [r for r in final_ans['recommendations'] if r.get('relevance') == 'high']
            if high_relevance:
                print(f"\n  🔥 Прям точно помогут:")
                for rec in high_relevance:
                    print(f"    📄 [{rec.get('source')}] — {rec.get('section')}")
                    print(f"       ℹ️  {rec.get('reason')}")
            
            # Разделы со средней релевантностью
            medium_relevance = [r for r in final_ans['recommendations'] if r.get('relevance') == 'medium']
            if medium_relevance:
                print(f"\n  📌 Вроде полезные по теме:")
                for rec in medium_relevance:
                    print(f"    📄 [{rec.get('source')}] — {rec.get('section')}")
                    print(f"       ℹ️  {rec.get('reason')}")

        print(f"\n🎯 Confidence: {final_ans.get('confidence', 0):.2%}")
        print(f"🔄 Iterations: {state.get('iteration', 1)}")

    except Exception as exc:
        print(state['final_answer'])
        logger.error(f"Ошибка парсинга final_answer: {exc}")

    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный режим
# ---------------------------------------------------------------------------

def run_interactive(verbose: bool = False) -> None:
    SEP = "=" * 80
    print(f"\n{SEP}")
    print(f"Iterative RAG-агент (следует system_prompt.md, max {MAX_ITERATIONS} iterations)")
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
            state = run_query(question, verbose=verbose)
            print_result(question, state, verbose=verbose)
        except Exception as exc:
            logger.error(f"Ошибка выполнения: {exc}", exc_info=True)
            print(f"\n[!] Ошибка: {exc}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Iterative RAG-агент (max {MAX_ITERATIONS} iterations)"
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="Вопрос (если не указан - интерактивный режим)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Подробный вывод"
    )
    return parser.parse_args()


def clear_logs_directory() -> None:
    logs_dir = Path(__file__).parent / "logs"
    if not logs_dir.exists():
        return
    
    # Удаляем файлы с нумерацией: 001_*.log, 002_*.log, и т.д.
    deleted_count = 0
    for log_file in logs_dir.glob("[0-9][0-9][0-9]_*.log"):
        try:
            log_file.unlink()
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Не удалось удалить {log_file.name}: {e}")
    
    # Удаляем единый файл логов (старый формат)
    old_log_file = logs_dir / "_rag_llm.log"
    if old_log_file.exists():
        try:
            old_log_file.unlink()
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Не удалось удалить _rag_llm.log: {e}")
    
    if deleted_count > 0:
        logger.info(f"Очищена директория logs: удалено {deleted_count} файлов")


def main() -> None:
    args = parse_args()
    
    # Очищаем логи перед запуском
    clear_logs_directory()

    logger.info(
        f"Запуск Iterative RAG-агента (max {MAX_ITERATIONS} iterations)\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  ClickHouse:  {settings.clickhouse_host}:{settings.clickhouse_port}"
    )

    if args.question:
        question = " ".join(args.question)
        state = run_query(question, verbose=args.verbose)
        print_result(question, state, verbose=args.verbose)
    else:
        run_interactive(verbose=args.verbose)


if __name__ == "__main__":
    main()

