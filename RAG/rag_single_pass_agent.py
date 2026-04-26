"""
Single-pass LangGraph агент для поиска и анализа данных.

Архитектура: линейный граф без итераций.

Pipeline (один проход):
    START -> query_analyzer -> tool_executor -> data_analyzer -> answer_generator -> answer_evaluator -> END

Узлы:
    1. query_analyzer     - анализирует вопрос, выбирает инструменты (status: plan + action)
    2. tool_executor      - выполняет все выбранные инструменты параллельно
    3. data_analyzer      - анализирует результаты, извлекает данные (status: observation)
    4. answer_generator   - формирует финальный ответ (status: final)
    5. answer_evaluator   - оценивает качество ответа

Состояние агента (AgentState):
    - user_query: исходный вопрос
    - plan: план поиска
    - tool_calls: список инструментов для вызова
    - tool_results: результаты выполнения инструментов
    - found_data: структурированные данные
    - evidence: доказательства из документов
    - final_answer: итоговый ответ
    - evaluation: оценка качества ответа

Принципы (из analytical_agent):
    [!] ЗАПРЕЩЕНО придумывать, домысливать или использовать общие знания
    [+] Работа ТОЛЬКО с информацией из инструментов (tools)
    [+] Все утверждения должны иметь источник
    [+] Честное признание отсутствия данных

Использование:
    python rag_single_pass_agent.py "найди все СУБД"
    python rag_single_pass_agent.py --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, TypedDict

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
from rag_chat import build_vectorstore, settings, build_llm
from kb_tools import create_kb_tools
from llm_call_logger import LangChainFileLogger, LlmCallLogger
from llm_messages import LLMConversation, execute_tool_calls
from system_prompts import get_prompt_by_name
from logging_config import setup_logging

logger = setup_logging("rag_single_pass_agent")


# ---------------------------------------------------------------------------
# Состояние агента (State)
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """Состояние single-pass агента."""
    user_query: str
    plan: list[str]  # План поиска
    tool_calls: list[dict[str, Any]]  # Вызовы инструментов [{tool, args}]
    tool_results: list[dict[str, Any]]  # Результаты [{tool, result}]
    found_data: list[dict[str, Any]]  # Структурированные данные
    evidence: list[str]  # Доказательства из документов
    gaps: list[str]  # Что не найдено
    final_answer: str  # Итоговый ответ
    evaluation: dict[str, Any]  # Оценка качества


# ---------------------------------------------------------------------------
# Pydantic модели для структурированных ответов
# ---------------------------------------------------------------------------

class QueryPlan(BaseModel):
    """План поиска с выбором инструментов."""
    thought: str = Field(description="Краткое рассуждение о стратегии поиска")
    plan: list[str] = Field(description="Шаги плана поиска (3-5 пунктов)")
    tool_calls: list[dict[str, Any]] = Field(
        description=(
            "Список вызовов инструментов:\n"
            "[{\"tool\": \"semantic_search\", \"args\": {\"query\": \"...\", \"top_k\": 10}}, ...]\n"
            "Доступные инструменты:\n"
            "  - semantic_search: {\"query\": \"текст\", \"top_k\": 10}\n"
            "  - exact_search: {\"substring\": \"текст\", \"limit\": 30}\n"
            "  - multi_term_exact_search: {\"terms\": [\"term1\", \"term2\"], \"limit\": 30}\n"
            "  - regex_search: {\"pattern\": \"regex\", \"max_results\": 50}\n"
            "  - read_table: {\"section\": \"название\", \"limit\": 50}\n"
            "  - get_section_content: {\"source_file\": \"file.md\", \"section\": \"название\"}\n"
            "Выбирай 2-4 инструмента, которые дополняют друг друга"
        )
    )


class DataAnalysis(BaseModel):
    """Анализ найденных данных."""
    found_data: list[dict[str, Any]] = Field(
        description=(
            "Структурированные данные ТОЛЬКО из результатов инструментов\n"
            "Формат: [{\"entity\": \"PostgreSQL\", \"attribute\": \"ip\", \"value\": \"10.0.0.1\", "
            "\"source\": \"file.md\", \"context\": \"цитата\"}]\n"
            "ВАЖНО: Если ничего не найдено - список пустой []"
        )
    )
    evidence: list[str] = Field(
        description=(
            "ТОЧНЫЕ цитаты из результатов инструментов\n"
            "Каждая цитата должна быть дословной"
        )
    )
    gaps: list[str] = Field(
        description=(
            "Что НЕ найдено из вопроса пользователя\n"
            "Будь честен - если данных нет, укажи это"
        )
    )
    summary: str = Field(description="Краткая сводка (2-3 предложения)")


class FinalAnswer(BaseModel):
    """Финальный ответ с метриками."""
    summary: str = Field(description="Краткий ответ (1-2 предложения)")
    details: str = Field(description="Подробное объяснение")
    data: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Структурированные данные"
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Список источников [file.md]"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Уверенность в ответе (0-1)"
    )


class AnswerEvaluation(BaseModel):
    """Оценка качества ответа."""
    relevance: int = Field(ge=1, le=5, description="Релевантность вопросу (1-5)")
    completeness: int = Field(ge=1, le=5, description="Полнота ответа (1-5)")
    accuracy: int = Field(ge=1, le=5, description="Точность (наличие источников) (1-5)")
    overall_score: float = Field(ge=0.0, le=5.0, description="Общая оценка (среднее)")
    missing: list[str] = Field(
        default_factory=list,
        description="Что отсутствует в ответе"
    )
    feedback: str = Field(description="Краткий комментарий (1-2 предложения)")


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
# Системные промпты
# ---------------------------------------------------------------------------

def _build_query_analyzer_system() -> str:
    """Системный промпт для query_analyzer (plan + action)."""
    return """Ты - эксперт по анализу вопросов и планированию поиска в технической документации.

ОСНОВНОЙ ПРИНЦИП (из analytical_agent):
  - Статусы: plan + action - анализ вопроса и выбор инструментов
  - Работа ТОЛЬКО с доступными tools
  - ЗАПРЕЩЕНО придумывать данные
  - Выбирай 2-4 инструмента, которые дополняют друг друга

ТВОЯ ЗАДАЧА:
  1. Проанализируй вопрос пользователя
  2. Сформируй план поиска (3-5 шагов)
  3. Выбери 2-4 инструмента для параллельного выполнения

СТРАТЕГИЯ ВЫБОРА ИНСТРУМЕНТОВ:
  - Комбинируй semantic_search (концептуальный) + exact_search (точный)
  - Для перечислений используй multi_term_exact_search
  - Для IP/портов используй regex_search
  - Если нужны таблицы - добавь read_table

ВАЖНО:
  - Выбирай инструменты, которые дополняют друг друга
  - Не дублируй однотипные запросы
  - Формулируй аргументы конкретно и чётко

Отвечай ТОЛЬКО структурированным объектом QueryPlan.
Никаких пояснений вне структуры."""


def _build_data_analyzer_system() -> str:
    """Системный промпт для data_analyzer (observation)."""
    return """Ты - аналитик данных по технической документации.

ОСНОВНОЙ ПРИНЦИП (из analytical_agent):
  - Статус: observation - обработка результатов инструментов
  - Извлекай ТОЛЬКО информацию из результатов
  - ЗАПРЕЩЕНО придумывать, домысливать или добавлять от себя
  - Честно признавай отсутствие данных

ТВОЯ ЗАДАЧА:
  Изучи результаты всех инструментов и извлеки:
    1. Структурированные данные (entity, attribute, value, source, context)
    2. Доказательства (ТОЧНЫЕ цитаты из документов)
    3. Пробелы (что НЕ найдено из вопроса)
    4. Краткую сводку

КРИТИЧЕСКИ ВАЖНО:
  - Если инструмент ничего не нашел - found_data пустой
  - Доказательства = дословные цитаты из результатов
  - Gaps = честное признание того, чего нет

Отвечай ТОЛЬКО структурированным объектом DataAnalysis.
Никаких пояснений вне структуры."""


def _build_answer_generator_system() -> str:
    """Системный промпт для answer_generator (final)."""
    return """Ты - эксперт-аналитик по технической документации.

ОСНОВНОЙ ПРИНЦИП (из analytical_agent):
  - Статус: final - формирование итогового ответа
  - Работа ТОЛЬКО с найденными данными
  - ЗАПРЕЩЕНО придумывать или использовать общие знания
  - Честное признание недостаточности данных

СТРОГИЕ ПРАВИЛА:
  [!] ЗАПРЕЩЕНО придумывать, домысливать или добавлять информацию от себя
  [!] ЗАПРЕЩЕНО использовать общие знания или предположения
  [+] Используй ИСКЛЮЧИТЕЛЬНО данные из found_data и evidence
  [+] Каждое утверждение должно иметь источник
  [+] Структурируй ответ (списки, таблицы, подзаголовки)
  [+] Если данных нет - честно скажи об этом
  [+] Отвечай на русском языке

ФОРМАТ ОТВЕТА:
  - summary: краткий ответ (1-2 предложения)
  - details: подробное объяснение с цитатами и источниками
  - data: структурированные данные из found_data
  - sources: список уникальных источников
  - confidence: уверенность (0-1, зависит от количества и качества данных)

Если found_data пуст:
  - summary: "В документации не найдено информации по запросу"
  - details: объясни что искалось и почему не найдено
  - confidence: 0.1-0.3

Отвечай ТОЛЬКО структурированным объектом FinalAnswer.
Никаких пояснений вне структуры."""


def _build_evaluator_system() -> str:
    """Системный промпт для answer_evaluator."""
    return """Ты - строгий аналитик качества ответов.

ОЦЕНИ ОТВЕТ ПО КРИТЕРИЯМ:
  1. Релевантность (1-5): отвечает ли на вопрос пользователя
  2. Полнота (1-5): достаточно ли информации
  3. Точность (1-5): подтверждены ли факты источниками

ПРАВИЛА ОЦЕНКИ:
  - Оценивай ТОЛЬКО на основе предоставленных данных
  - НЕ домысливай что "могло бы быть"
  - Если источники не указаны - снижай accuracy
  - Если данных мало - снижай completeness
  - Если ответ не по теме - снижай relevance

ФОРМАТ:
  - relevance: 1-5
  - completeness: 1-5
  - accuracy: 1-5
  - overall_score: среднее арифметическое
  - missing: список того, чего не хватает
  - feedback: краткий комментарий

Отвечай ТОЛЬКО структурированным объектом AnswerEvaluation.
Никаких пояснений вне структуры."""


# ---------------------------------------------------------------------------
# Узлы графа (Nodes)
# ---------------------------------------------------------------------------

def query_analyzer(state: AgentState) -> AgentState:
    """
    Узел 1: Анализирует вопрос и выбирает инструменты.

    Соответствует статусам: plan + action
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "QUERY_ANALYZER START",
        f"Вопрос: {state['user_query']}"
    )

    try:
        llm = build_llm()
        structured_llm = llm.with_structured_output(QueryPlan)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix="QUERY_ANALYZER")]
            }

        result: QueryPlan = structured_llm.invoke(
            [
                {"role": "system", "content": _build_query_analyzer_system()},
                {"role": "user", "content": f"Вопрос: {state['user_query']}"}
            ],
            config=invoke_config,
        )

        state["plan"] = result.plan
        state["tool_calls"] = result.tool_calls

        logger.info(
            f"Query analyzer завершил анализ\n"
            f"  Thought: {result.thought}\n"
            f"  План: {len(result.plan)} шагов\n"
            f"  Инструменты: {len(result.tool_calls)}"
        )

        llm_logger.log_stage(
            "QUERY_ANALYZER COMPLETE",
            f"План:\n" + "\n".join(f"  {i+1}. {p}" for i, p in enumerate(result.plan)) +
            f"\n\nИнструменты ({len(result.tool_calls)}):\n" +
            "\n".join(f"  - {tc['tool']}" for tc in result.tool_calls)
        )

    except Exception as exc:
        logger.error(f"Ошибка query_analyzer: {exc}", exc_info=True)
        # Fallback: semantic search
        state["plan"] = ["Семантический поиск по вопросу"]
        state["tool_calls"] = [
            {"tool": "semantic_search", "args": {"query": state["user_query"], "top_k": 10}}
        ]

    return state


def tool_executor(state: AgentState) -> AgentState:
    """
    Узел 2: Выполняет все выбранные инструменты параллельно.

    Технический узел - выполнение без LLM.
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "TOOL_EXECUTOR START",
        f"Выполнение {len(state['tool_calls'])} инструмент(ов)"
    )

    try:
        # Получаем vectorstore и tools
        vectorstore = build_vectorstore(force_reindex=False)
        knowledge_dir = Path(settings.knowledge_dir)
        tools_list = create_kb_tools(
            vectorstore=vectorstore,
            knowledge_dir=knowledge_dir,
            semantic_top_k=settings.retriever_top_k,
            llm_logger=llm_logger,
        )

        tools_map = {tool.name: tool for tool in tools_list}

        results = []
        for tc in state["tool_calls"]:
            tool_name = tc["tool"]
            tool_args = tc["args"]

            tool = tools_map.get(tool_name)
            if not tool:
                logger.warning(f"Инструмент {tool_name} не найден")
                results.append({
                    "tool": tool_name,
                    "result": f"Инструмент {tool_name} не найден",
                    "error": True
                })
                continue

            try:
                logger.info(f"Выполнение {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]}...)")
                result = tool.invoke(tool_args)
                results.append({
                    "tool": tool_name,
                    "result": str(result),
                    "error": False
                })
                logger.info(f"  Результат: {str(result)[:200]}...")
            except Exception as exc:
                logger.error(f"Ошибка выполнения {tool_name}: {exc}")
                results.append({
                    "tool": tool_name,
                    "result": f"Ошибка: {exc}",
                    "error": True
                })

        state["tool_results"] = results

        logger.info(f"Tool executor завершил выполнение {len(results)} инструментов")
        
        summary_lines = []
        for i, r in enumerate(results):
            status = "ERROR" if r["error"] else f"{len(r.get('result', ''))} символов"
            summary_lines.append(f"  {i+1}. {r['tool']}: {status}")
        
        llm_logger.log_stage(
            "TOOL_EXECUTOR COMPLETE",
            f"Выполнено: {len(results)} инструментов\n" + "\n".join(summary_lines)
        )
        
    except Exception as exc:
        logger.error(f"Ошибка tool_executor: {exc}", exc_info=True)
        state["tool_results"] = []

    return state


def data_analyzer(state: AgentState) -> AgentState:
    """
    Узел 3: Анализирует результаты инструментов.

    Соответствует статусу: observation
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "DATA_ANALYZER START",
        f"Анализ {len(state['tool_results'])} результатов"
    )

    try:
        llm = build_llm()
        structured_llm = llm.with_structured_output(DataAnalysis)

        # Формируем контекст с результатами всех инструментов
        context_parts = [
            f"Вопрос пользователя: {state['user_query']}\n",
            f"Выполнено инструментов: {len(state['tool_results'])}\n",
            "\nРезультаты инструментов:\n"
        ]

        for i, res in enumerate(state["tool_results"], 1):
            context_parts.append(f"\n[{i}] Инструмент: {res['tool']}")
            if res["error"]:
                context_parts.append(f"    ОШИБКА: {res['result']}")
            else:
                # Ограничиваем длину результата
                result_preview = res["result"][:2000]
                context_parts.append(f"    Результат:\n{result_preview}")
                if len(res["result"]) > 2000:
                    context_parts.append(f"    ... (всего {len(res['result'])} символов)")

        context = "\n".join(context_parts)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix="DATA_ANALYZER")]
            }

        result: DataAnalysis = structured_llm.invoke(
            [
                {"role": "system", "content": _build_data_analyzer_system()},
                {"role": "user", "content": context}
            ],
            config=invoke_config,
        )

        state["found_data"] = result.found_data
        state["evidence"] = result.evidence
        state["gaps"] = result.gaps

        logger.info(
            f"Data analyzer завершил анализ\n"
            f"  Найдено данных: {len(result.found_data)}\n"
            f"  Доказательств: {len(result.evidence)}\n"
            f"  Пробелов: {len(result.gaps)}\n"
            f"  Сводка: {result.summary}"
        )

        llm_logger.log_stage(
            "DATA_ANALYZER COMPLETE",
            f"Найдено данных: {len(result.found_data)}\n"
            f"Доказательств: {len(result.evidence)}\n"
            f"Пробелов: {len(result.gaps)}\n"
            f"Сводка: {result.summary}"
        )

    except Exception as exc:
        logger.error(f"Ошибка data_analyzer: {exc}", exc_info=True)
        state["found_data"] = []
        state["evidence"] = []
        state["gaps"] = ["Не удалось проанализировать результаты"]

    return state


def answer_generator(state: AgentState) -> AgentState:
    """
    Узел 4: Формирует финальный ответ.

    Соответствует статусу: final
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "ANSWER_GENERATOR START",
        f"Формирование ответа на основе {len(state['found_data'])} данных"
    )

    try:
        llm = build_llm()
        structured_llm = llm.with_structured_output(FinalAnswer)

        # Формируем контекст с найденными данными
        context_parts = [
            f"Вопрос пользователя: {state['user_query']}\n",
            f"\nНайдено данных: {len(state['found_data'])}",
        ]

        if state["found_data"]:
            context_parts.append("\nСтруктурированные данные:")
            for i, data in enumerate(state["found_data"][:20], 1):
                context_parts.append(f"  {i}. {json.dumps(data, ensure_ascii=False)}")

        if state["evidence"]:
            context_parts.append(f"\nДоказательства ({len(state['evidence'])}):")
            for i, ev in enumerate(state["evidence"][:10], 1):
                context_parts.append(f"  {i}. {ev[:200]}")

        if state["gaps"]:
            context_parts.append("\nПробелы (что не найдено):")
            for gap in state["gaps"]:
                context_parts.append(f"  - {gap}")

        context = "\n".join(context_parts)

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix="ANSWER_GENERATOR")]
            }

        result: FinalAnswer = structured_llm.invoke(
            [
                {"role": "system", "content": _build_answer_generator_system()},
                {"role": "user", "content": context}
            ],
            config=invoke_config,
        )

        state["final_answer"] = json.dumps(result.model_dump(), ensure_ascii=False, indent=2)

        logger.info(
            f"Answer generator сформировал ответ\n"
            f"  Summary: {result.summary[:100]}\n"
            f"  Data: {len(result.data)} записей\n"
            f"  Sources: {len(result.sources)}\n"
            f"  Confidence: {result.confidence:.2f}"
        )

        llm_logger.log_stage(
            "ANSWER_GENERATOR COMPLETE",
            f"Confidence: {result.confidence:.2f}\n"
            f"Data: {len(result.data)} записей\n"
            f"Sources: {', '.join(result.sources)}"
        )

    except Exception as exc:
        logger.error(f"Ошибка answer_generator: {exc}", exc_info=True)
        fallback = FinalAnswer(
            summary="Не удалось сформировать ответ из-за ошибки",
            details=f"Ошибка: {exc}",
            confidence=0.0
        )
        state["final_answer"] = json.dumps(fallback.model_dump(), ensure_ascii=False, indent=2)

    return state


def answer_evaluator(state: AgentState) -> AgentState:
    """
    Узел 5: Оценивает качество ответа.
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage("ANSWER_EVALUATOR START", "Оценка качества ответа")

    try:
        llm = build_llm()
        structured_llm = llm.with_structured_output(AnswerEvaluation)

        # Формируем контекст для оценки
        context = (
            f"Вопрос пользователя: {state['user_query']}\n\n"
            f"Ответ агента:\n{state['final_answer']}"
        )

        invoke_config = {}
        if llm_logger._enabled:
            invoke_config = {
                "callbacks": [LangChainFileLogger(llm_logger, step_prefix="ANSWER_EVALUATOR")]
            }

        result: AnswerEvaluation = structured_llm.invoke(
            [
                {"role": "system", "content": _build_evaluator_system()},
                {"role": "user", "content": context}
            ],
            config=invoke_config,
        )

        state["evaluation"] = result.model_dump()

        logger.info(
            f"Answer evaluator завершил оценку\n"
            f"  Relevance: {result.relevance}/5\n"
            f"  Completeness: {result.completeness}/5\n"
            f"  Accuracy: {result.accuracy}/5\n"
            f"  Overall: {result.overall_score:.1f}/5\n"
            f"  Feedback: {result.feedback}"
        )

        llm_logger.log_stage(
            "ANSWER_EVALUATOR COMPLETE",
            f"Relevance: {result.relevance}/5\n"
            f"Completeness: {result.completeness}/5\n"
            f"Accuracy: {result.accuracy}/5\n"
            f"Overall: {result.overall_score:.1f}/5\n"
            f"Feedback: {result.feedback}"
        )

    except Exception as exc:
        logger.error(f"Ошибка answer_evaluator: {exc}", exc_info=True)
        state["evaluation"] = {
            "relevance": 0,
            "completeness": 0,
            "accuracy": 0,
            "overall_score": 0.0,
            "missing": ["Не удалось оценить"],
            "feedback": f"Ошибка оценки: {exc}"
        }

    return state


# ---------------------------------------------------------------------------
# Построение графа
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    """
    Создает LangGraph для single-pass агента.

    Линейный граф без итераций:
      START -> query_analyzer -> tool_executor -> data_analyzer -> answer_generator -> answer_evaluator -> END
    """
    workflow = StateGraph(AgentState)

    # Добавляем узлы
    workflow.add_node("query_analyzer", query_analyzer)
    workflow.add_node("tool_executor", tool_executor)
    workflow.add_node("data_analyzer", data_analyzer)
    workflow.add_node("answer_generator", answer_generator)
    workflow.add_node("answer_evaluator", answer_evaluator)

    # Линейная последовательность
    workflow.set_entry_point("query_analyzer")
    workflow.add_edge("query_analyzer", "tool_executor")
    workflow.add_edge("tool_executor", "data_analyzer")
    workflow.add_edge("data_analyzer", "answer_generator")
    workflow.add_edge("answer_generator", "answer_evaluator")
    workflow.add_edge("answer_evaluator", END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Основная функция выполнения
# ---------------------------------------------------------------------------

def run_query(question: str, verbose: bool = False) -> dict:
    """
    Выполняет single-pass поиск по вопросу.

    Args:
        question: Вопрос пользователя
        verbose: Режим подробного вывода

    Returns:
        Финальное состояние агента
    """
    llm_logger = _get_llm_logger()
    llm_logger.log_stage(
        "QUERY START",
        f"Вопрос: {question}\nРежим: {'verbose' if verbose else 'normal'}"
    )

    # Создаем граф
    graph = build_graph()

    # Инициализируем состояние
    initial_state: AgentState = {
        "user_query": question,
        "plan": [],
        "tool_calls": [],
        "tool_results": [],
        "found_data": [],
        "evidence": [],
        "gaps": [],
        "final_answer": "",
        "evaluation": {},
    }

    # Запускаем граф
    try:
        final_state = graph.invoke(initial_state)

        llm_logger.log_stage(
            "QUERY COMPLETE",
            f"Найдено данных: {len(final_state['found_data'])}\n"
            f"Доказательств: {len(final_state['evidence'])}\n"
            f"Оценка: {final_state['evaluation'].get('overall_score', 0):.1f}/5"
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
    print(SEP)

    if verbose:
        print("\nПлан:")
        for i, step in enumerate(state["plan"], 1):
            print(f"  {i}. {step}")

        print(f"\nВызовы инструментов: {len(state['tool_calls'])}")
        for i, tc in enumerate(state["tool_calls"], 1):
            print(f"  {i}. {tc['tool']}")

        print(f"\nНайдено данных: {len(state['found_data'])}")
        if state["found_data"]:
            for i, data in enumerate(state["found_data"][:5], 1):
                print(f"  {i}. {json.dumps(data, ensure_ascii=False)[:150]}...")

        if state["gaps"]:
            print("\nПробелы:")
            for gap in state["gaps"]:
                print(f"  - {gap}")

    print(f"\n{SEP}")
    print("ОТВЕТ:")
    print(SEP)

    try:
        answer = json.loads(state["final_answer"])
        print(f"\nSummary: {answer['summary']}")
        print(f"\nDetails:\n{answer['details']}")

        if answer.get("data"):
            print(f"\nData ({len(answer['data'])} записей):")
            for i, item in enumerate(answer["data"][:10], 1):
                print(f"  {i}. {json.dumps(item, ensure_ascii=False)[:150]}")

        if answer.get("sources"):
            print(f"\nSources: {', '.join(answer['sources'])}")

        print(f"\nConfidence: {answer['confidence']:.0%}")
    except:
        print(state["final_answer"])

    print(f"\n{SEP}")
    print("ОЦЕНКА:")
    print(SEP)

    eval_data = state["evaluation"]
    print(f"  Relevance:    {eval_data.get('relevance', 0)}/5")
    print(f"  Completeness: {eval_data.get('completeness', 0)}/5")
    print(f"  Accuracy:     {eval_data.get('accuracy', 0)}/5")
    print(f"  Overall:      {eval_data.get('overall_score', 0):.1f}/5")
    print(f"\n  Feedback: {eval_data.get('feedback', '')}")

    if eval_data.get("missing"):
        print("\n  Missing:")
        for item in eval_data["missing"]:
            print(f"    - {item}")

    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный режим
# ---------------------------------------------------------------------------

def run_interactive(verbose: bool = False) -> None:
    """Запускает интерактивный режим."""
    SEP = "=" * 80
    print(f"\n{SEP}")
    print("Single-pass RAG-агент")
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
        description="Single-pass RAG-агент для поиска и анализа"
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="Вопрос (если не указан — интерактивный режим)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать детали процесса",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info(f"""Запуск Single-pass RAG-агента
  LLM:         {settings.ollama_model}
  Эмбеддинги:  {settings.ollama_embed_model}
  Источники:   {settings.knowledge_dir}
  ClickHouse:  {settings.clickhouse_host}:{settings.clickhouse_port}
  Логирование: {'включено' if settings.llm_log_enabled else 'отключено'}""")

    if args.question:
        question = " ".join(args.question)
        state = run_query(question, verbose=args.verbose)
        print_result(question, state, verbose=args.verbose)
    else:
        run_interactive(verbose=args.verbose)


if __name__ == "__main__":
    main()

