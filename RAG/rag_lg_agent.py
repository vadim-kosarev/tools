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
    """Returns the process-level LlmCallLogger instance (lazy init)."""
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
    """
    Генерирует JSON со списком доступных инструментов и их параметрами.
    Использует Pydantic model_json_schema() для получения полной схемы параметров.
    Результат кэшируется - генерация происходит ОДИН РАЗ при загрузке промпта.
    
    Args:
        compact: Если True, возвращает компактный JSON без отступов (для промптов LLM)
    
    Returns:
        JSON строка с массивом инструментов
    """
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
    Загружает system_prompt.md как базовый промпт для агента.
    Подставляет динамический список инструментов в компактном JSON формате.
    Оборачивает tools в секцию [AVAILABLE_TOOLS] для явной структурированности.
    """
    prompt_path = Path(__file__).parent / "system_prompt.md"
    if prompt_path.exists():
        prompt_template = prompt_path.read_text(encoding="utf-8")
        
        # Подставляем компактный JSON со списком инструментов
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
        
        # Заменяем placeholder с добавлением секции
        prompt = prompt_template.replace(
            "{available_tools}",
            f"\n[AVAILABLE_TOOLS]\n{tools_formatted}\n"
        )
        
        return prompt
    else:
        logger.warning(f"system_prompt.md не найден в {prompt_path}")
        return "Ты - аналитический AI-агент"


_SYSTEM_PROMPT = _load_system_prompt()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _fix_tool_args(tool_name: str, tool_input: dict) -> dict:
    """
    Автоматическое исправление некорректных параметров от LLM.

    LLM часто использует интуитивные, но неправильные имена параметров.
    Эта функция исправляет распространенные ошибки.

    Args:
        tool_name: Имя инструмента
        tool_input: Параметры от LLM

    Returns:
        Исправленные параметры
    """
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
    max_expansions: int = 5  # Максимум расширений на итерацию
) -> list[dict[str, Any]]:
    """
    Автоматически расширяет контекст найденных чанков через get_neighbor_chunks.
    
    Анализирует результаты инструментов поиска и для каждого найденного чанка
    с line_start вызывает get_neighbor_chunks для получения контекста.
    
    Args:
        tool_results: Результаты выполненных инструментов
        tools_map: Маппинг имя_инструмента -> инструмент
        llm_logger: Логгер для записи операций
        iteration: Номер текущей итерации
        max_expansions: Максимум вызовов get_neighbor_chunks
        
    Returns:
        Список дополнительных результатов с расширенным контекстом
    """
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
    """
    Узел 1: PLANq
    Анализирует вопрос и формирует план поиска.
    Возвращает статус 'plan' с перечнем шагов.
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "PLAN NODE START",
        f"Вопрос: {state['user_query']}"
    )

    try:
        # Инициализируем LLM с structured output
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentPlan)

        # Формируем системный промпт
        system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: plan


Сформируй план поиска для ответа на вопрос пользователя.
Учти, что после первого поиска будет возможность сделать уточняющие запросы.

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа plan:
```json
{{
  "status": "plan",
  "step": 1,
  "thought": "краткое рассуждение о стратегии поиска",
  "plan": [
    "шаг 1 - какие инструменты использовать",
    "шаг 2 - какие данные искать",
    "шаг 3 - как структурировать результаты"
  ]
}}
```

НЕ ИСПОЛЬЗУЙ поля "action", "observation" или "final_answer" на этом этапе!"""

        user_message = f"Вопрос: {state['user_query']}"

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
                    user_message += f"""

⚠️ ОШИБКА ПАРСИНГА JSON!

ОБЯЗАТЕЛЬНАЯ структура для этапа plan:
{{
  "status": "plan",  ← ОБЯЗАТЕЛЬНО "plan"!
  "step": 1,
  "thought": "краткое рассуждение",
  "plan": ["шаг 1", "шаг 2", "шаг 3"]  ← ОБЯЗАТЕЛЬНО массив строк!
}}

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше."""
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
    """
    Узел 2: ACTION
    Выбирает и вызывает инструменты на основе плана или refinement_plan.
    """
    llm_logger = _get_llm_logger()
    iteration = state.get("iteration", 1)
    llm_logger.log_stage(
        f"ACTION NODE START (iteration {iteration})",
        f"План: {state.get('refinement_plan') or state['plan']}"
    )

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentAction)

        # Используем refinement_plan если есть, иначе основной plan
        current_plan = state.get('refinement_plan') or state['plan']
        plan_text = "\n".join(f"{i+1}. {step}" for i, step in enumerate(current_plan))

        # Подготовка контекста из предыдущих результатов
        previous_results_summary = ""
        if state.get("all_tool_results"):
            results_count = len(state["all_tool_results"])
            previous_results_summary = f"\n\nРЕЗУЛЬТАТЫ ПРЕДЫДУЩИХ ПОИСКОВ ({results_count} инструментов):\n"
            for i, tr in enumerate(state["all_tool_results"][-10:], 1):
                tool_name = tr['tool']
                result_preview = str(tr['result'])[:200]
                previous_results_summary += f"{i}. {tool_name}: {result_preview}...\n"

        system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: action (итерация {iteration}/{MAX_ITERATIONS})

План {"уточнения" if state.get('refinement_plan') else "поиска"}:
{plan_text}
{previous_results_summary}

Выбери 2-4 инструмента для {"уточняющего" if iteration > 1 else "первичного"} поиска.
{"Используй targeted tools: find_relevant_sections, get_chunks_by_index, exact_search_in_file_section" if iteration > 1 else ""}

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа action:
```json
{{
  "status": "action",
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение о выборе инструментов",
  "action": [
    {{
      "tool": "имя_инструмента",
      "input": {{"параметр": "значение"}}
    }}
  ]
}}
```

Примеры параметров инструментов:
- semantic_search: {{"query": "текст запроса", "top_k": 10}}
- exact_search: {{"substring": "точная подстрока", "limit": 30}}
- exact_search_in_file_section: {{"substring": "термин", "source_file": "file.md", "section": "Section"}}
- find_relevant_sections: {{"query": "описание темы", "exact_terms": ["term1"], "limit": 10}}
- get_chunks_by_index: {{"source": "file.md", "section": "Section", "chunk_indices": [0,1,2]}}
- get_section_content: {{"source_file": "file.md", "section": "Section"}}
- read_table: {{"section": "Section with table", "limit": 50}}

НЕ ИСПОЛЬЗУЙ поля "observation" или "final_answer" на этом этапе!"""

        user_message = f"Вопрос пользователя: {state['user_query']}"

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
                    error_message = f"""⚠️ ОШИБКА ПАРСИНГА JSON!

Твой предыдущий ответ не соответствует требуемому формату.

ОБЯЗАТЕЛЬНАЯ структура для этапа action:
{{
  "status": "action",  ← ОБЯЗАТЕЛЬНО "action", НЕ "observation"!
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение",
  "action": [  ← ОБЯЗАТЕЛЬНО массив с инструментами!
    {{
      "tool": "имя_инструмента",
      "input": {{"параметр": "значение"}}
    }}
  ]
}}

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше."""
                    
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
                    
                    # Конвертируем result в строку (может быть Pydantic модель)
                    if hasattr(result_raw, "model_dump_json"):
                        # Pydantic модель - используем pydantic_to_markdown() для читаемости
                        result_str = pydantic_to_markdown(result_raw)
                    else:
                        # Обычная строка или другой объект
                        result_str = str(result_raw)
                    
                    tool_results.append({
                        "tool": tool_name,
                        "input": tool_input,
                        "result": result_str
                    })
                    logger.info(f"{tool_name} завершён, результат: {len(result_str)} символов")
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
        for idx, tr in enumerate(tool_results):
            state["messages"].append({
                "role": "tool",
                "name": tr["tool"],
                "tool_call_id": f"call_{iteration}_{idx}",
                "content": json.dumps(tr, ensure_ascii=False, indent=2)
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
    """
    Узел 3: OBSERVATION
    Анализирует результаты выполнения инструментов текущей итерации.
    """
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
        tools_summary = "\n\n".join(
            f"### {tr['tool']}\n"
            f"Параметры: {json.dumps(tr['input'], ensure_ascii=False)}\n"
            f"Результат:\n```\n{str(tr['result'])[:2000]}\n```"
            for tr in current_results
        )

        system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: observation (итерация {iteration}/{MAX_ITERATIONS})

Проанализируй результаты выполнения инструментов.
Извлеки ТОЛЬКО информацию из результатов инструментов
НЕ придумывай, НЕ домысливай.

Опиши:
1. Что найдено (конкретные факты из результатов)
2. Что НЕ найдено (какие пробелы остались)
3. Достаточно ли данных для полного ответа

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа observation:
```json
{{
  "status": "observation",
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение о результатах",
  "observation": "подробный анализ: что найдено, что не найдено, достаточно ли данных"
}}
```

НЕ ИСПОЛЬЗУЙ поля "action" или "final_answer" на этом этапе!"""

        user_message = f"""Вопрос пользователя: {state['user_query']}

Результаты выполнения инструментов (итерация {iteration}):
{tools_summary}"""

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
    """
    Узел 4: REFINE
    Решает, нужны ли уточняющие запросы или данных достаточно.
    """
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

        system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: refine (итерация {iteration}/{MAX_ITERATIONS})

Проанализируй observation и реши:
1. Достаточно ли данных для полного ответа на вопрос?
2. Остались ли неотвеченные аспекты вопроса?
3. Нужны ли уточняющие запросы?

Если нужны уточнения (needs_refinement=True), составь refinement_plan:
- Какие конкретные данные не хватает
- Какие инструменты использовать для уточнения
- Какие параметры передать (source, section, chunk_indices если известны)

Используй targeted tools для уточнений:
- find_relevant_sections (если нужно найти конкретные разделы)
- get_chunks_by_index (если известны source, section, indices)
- exact_search_in_file_section (если известен файл и раздел)
- get_section_content (если нужен полный текст раздела)

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа refine:
```json
{{
  "status": "refine",
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение о достаточности данных",
  "needs_refinement": true,  // или false
  "refinement_plan": [       // если needs_refinement=true
    "что именно нужно уточнить",
    "какие инструменты использовать"
  ]
}}
```

НЕ ИСПОЛЬЗУЙ поля "action", "observation" или "final_answer" на этом этапе!"""

        user_message = f"""Вопрос пользователя: {state['user_query']}

Текущая observation:
{state['observation']}

Всего найдено результатов: {len(state.get('all_tool_results', []))} инструментов

Решение: нужны ли уточнения?"""

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
                    
                    error_message = f"""⚠️ ОШИБКА ПАРСИНГА JSON!

ОБЯЗАТЕЛЬНАЯ структура для этапа refine:
{{
  "status": "refine",  ← ОБЯЗАТЕЛЬНО "refine"!
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение",
  "needs_refinement": true,  ← ОБЯЗАТЕЛЬНО boolean!
  "refinement_plan": ["шаг 1", "шаг 2"]  ← Массив строк (может быть пустым если needs_refinement=false)
}}

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше."""
                    
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
    """
    Узел 5: FINAL
    Формирует финальный ответ на основе всех собранных данных.
    """
    llm_logger = _get_llm_logger()
    total_tools = len(state.get("all_tool_results", []))
    llm_logger.log_stage(
        "FINAL NODE START",
        f"Всего выполнено инструментов: {total_tools}, итераций: {state.get('iteration', 1)}"
    )

    try:
        llm = rag_chat.build_llm()
        structured_llm = llm.with_structured_output(AgentFinal)

        # Подготовка полного контекста
        all_results_summary = "\n\n".join(
            f"### {tr['tool']}\n{str(tr['result'])[:1500]}"
            for tr in state.get("all_tool_results", [])
        )

        system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: final

Сформируй итоговый ответ на вопрос пользователя.

СТРОГИЕ ПРАВИЛА:
- Используй ТОЛЬКО информацию из результатов инструментов
- НЕ придумывай, НЕ домысливай
- Если данных нет - честно признай это
- Указывай источники для каждого факта
- Структурируй ответ (summary, details, data, sources)

Всего выполнено {total_tools} инструментов за {state.get('iteration', 1)} итераций.

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа final:
```json
{{
  "status": "final",
  "step": {state.get("step", 1) + 1},
  "thought": "краткое рассуждение о полноте ответа",
  "final_answer": {{
    "summary": "краткий ответ на вопрос",
    "details": "подробное объяснение с фактами из документации",
    "data": [
      {{
        "entity": "название сущности",
        "attribute": "атрибут",
        "value": "значение"
      }}
    ],
    "sources": ["источник 1", "источник 2"],
    "confidence": 0.85
  }}
}}
```

НЕ ИСПОЛЬЗУЙ поля "action", "observation" или "plan" на этом этапе!"""

        user_message = f"""Вопрос пользователя: {state['user_query']}

Первоначальный план:
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(state['plan']))}

Финальная observation:
{state['observation']}

ВСЕ результаты инструментов ({total_tools}):
{all_results_summary}"""

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
    """
    Решает, нужно ли продолжить уточнение или перейти к финалу.

    Returns:
        "action" если needs_refinement=True
        "final" если needs_refinement=False или достигнут лимит
    """
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
    """
    Собирает LangGraph граф для итеративного агента.

    Pipeline:
        START -> plan -> action -> observation -> refine
                           ↑                       ↓
                           +------ [да] ----------+
                                    ↓ [нет]
                                  final -> END
    """
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
    """
    Выполняет один запрос через итеративный агент.

    Args:
        question: Вопрос пользователя
        verbose: Режим подробного вывода

    Returns:
        Финальное состояние агента
    """
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
    """Выводит результаты работы агента."""
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
    """Запускает интерактивный режим для многократных вопросов."""
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
    """
    Очищает директорию logs от файлов с нумерацией перед запуском агента.
    
    Удаляет:
      - [0-9][0-9][0-9]_*.log - отдельные файлы запросов/ответов
      - _rag_llm.log - единый файл логов (старый формат)
    
    Сохраняет:
      - Другие файлы (README, test/, и т.д.)
    """
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

