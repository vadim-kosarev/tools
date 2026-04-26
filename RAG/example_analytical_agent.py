"""
Пример аналитического агента с JSON-структурой ответов.

Демонстрирует использование system_prompt.md и LLMConversation
для итеративного поиска с структурированными ответами.

Агент работает по схеме:
  plan → action → observation → final

Каждый шаг возвращает структурированный JSON.

Запуск:
    python example_analytical_agent.py "какие СУБД используются?"
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

from clickhouse_store import ClickHouseVectorStore
from kb_tools import create_kb_tools
from llm_messages import LLMConversation, execute_tool_calls
from rag_chat import build_llm, build_vectorstore, settings
from system_prompts import get_prompt_by_name
from logging_config import setup_logging

load_dotenv()

logger = setup_logging("example_analytical_agent")


# ---------------------------------------------------------------------------
# Pydantic модели для структурированных ответов агента
# ---------------------------------------------------------------------------

class AgentAction(BaseModel):
    """Действие агента - вызов инструмента"""
    tool: str = Field(description="Имя инструмента")
    input: dict = Field(description="Аргументы для инструмента")


class FinalAnswer(BaseModel):
    """Финальный ответ агента"""
    summary: str = Field(description="Краткий ответ")
    details: str = Field(description="Подробное объяснение")
    data: list[dict] = Field(default_factory=list, description="Структурированные данные")
    sources: list[str] = Field(default_factory=list, description="Источники")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность в ответе (0-1)")


class AgentResponse(BaseModel):
    """Структурированный ответ агента"""
    status: str = Field(description="Статус: plan | action | final | error")
    step: int = Field(description="Номер шага")
    thought: str = Field(description="Краткое рассуждение")
    plan: list[str] = Field(default_factory=list, description="План действий")
    action: AgentAction | None = Field(default=None, description="Действие для выполнения")
    observation: str = Field(default="", description="Результат предыдущего шага")
    final_answer: FinalAnswer | None = Field(default=None, description="Финальный ответ")
    error: str = Field(default="", description="Описание ошибки")


# ---------------------------------------------------------------------------
# Аналитический агент
# ---------------------------------------------------------------------------

def run_analytical_agent(
    question: str,
    max_iterations: int = 6,
    verbose: bool = True,
) -> AgentResponse:
    """
    Запускает аналитического агента с JSON-структурой ответов.

    Агент работает итеративно:
      1. status="plan" → строит план
      2. status="action" → вызывает инструмент
      3. status="observation" → обрабатывает результат
      4. status="final" → формирует финальный ответ

    Args:
        question: Вопрос пользователя
        max_iterations: Максимум итераций
        verbose: Подробный вывод

    Returns:
        AgentResponse с финальным ответом
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"АНАЛИТИЧЕСКИЙ АГЕНТ: {question}")
    logger.info(f"{'=' * 80}\n")

    # Инициализация
    vectorstore = build_vectorstore(force_reindex=False)
    knowledge_dir = Path(settings.knowledge_dir)
    llm = build_llm()

    # Создаём инструменты
    tools = create_kb_tools(
        vectorstore=vectorstore,
        knowledge_dir=knowledge_dir,
        semantic_top_k=10,
    )

    # Загружаем системный промпт из system_prompt.md
    system_prompt = get_prompt_by_name("analytical_agent")

    if verbose:
        logger.info(f"📝 Системный промпт загружен: {len(system_prompt)} символов")
        logger.info(f"🔧 Доступно инструментов: {len(tools)}")
        logger.info("")

    # Создаём диалог
    conv = LLMConversation(
        system_prompt=system_prompt,
        user_prompt=question,
        available_tools=tools,
    )

    current_step = 0
    last_response: AgentResponse | None = None

    # Итеративный цикл
    for iteration in range(1, max_iterations + 1):
        current_step += 1

        logger.info(f"[ШАГ {current_step}] Вызов LLM...")

        # Привязываем инструменты для tool calling
        llm_with_tools = llm.bind_tools(tools)

        # Вызываем LLM
        response = llm_with_tools.invoke(conv.get_messages_for_llm())
        conv.add_assistant_from_langchain(response)

        # Парсим ответ LLM
        last_msg = conv.get_last_assistant_message()
        if not last_msg:
            logger.error("LLM не вернул сообщение")
            break

        # Пытаемся распарсить JSON из ответа
        try:
            # Удаляем markdown блоки если есть
            content = last_msg.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            response_data = json.loads(content.strip())
            last_response = AgentResponse(**response_data)

            if verbose:
                logger.info(f"  Status: {last_response.status}")
                logger.info(f"  Thought: {last_response.thought}")

                if last_response.plan:
                    logger.info(f"  Plan:")
                    for i, step in enumerate(last_response.plan, 1):
                        logger.info(f"    {i}. {step}")

        except Exception as exc:
            logger.warning(f"Не удалось распарсить JSON ответ: {exc}")
            logger.debug(f"Содержимое ответа: {last_msg.content[:500]}")

            # Fallback: если LLM не вернул JSON, но вернул tool_calls
            if last_msg.tool_calls:
                last_response = AgentResponse(
                    status="action",
                    step=current_step,
                    thought="LLM запросил инструменты",
                    action=AgentAction(
                        tool=last_msg.tool_calls[0].function["name"],
                        input=json.loads(last_msg.tool_calls[0].function["arguments"])
                    )
                )
            else:
                # Завершаем с ошибкой
                return AgentResponse(
                    status="error",
                    step=current_step,
                    thought="",
                    error=f"Не удалось распарсить ответ LLM: {exc}"
                )

        # Обработка статуса
        if last_response.status == "final":
            logger.info(f"\n✅ Финальный ответ получен на шаге {current_step}")
            break

        elif last_response.status == "error":
            logger.error(f"\n❌ Ошибка: {last_response.error}")
            break

        elif last_response.status == "action":
            # Выполняем tool calls
            if conv.has_pending_tool_calls():
                tool_calls = conv.get_pending_tool_calls()

                logger.info(f"  Выполнение {len(tool_calls)} инструмент(ов):")
                for tc in tool_calls:
                    logger.info(f"    → {tc.function['name']}")

                results = execute_tool_calls(tool_calls, tools)
                conv.add_tool_results(results)

                # Показываем краткие результаты
                if verbose:
                    for res in results:
                        if res.error:
                            logger.warning(f"    ✗ {res.tool_name}: {res.error}")
                        else:
                            preview = res.result[:150].replace("\n", " ")
                            logger.info(f"    ✓ {res.tool_name}: {preview}...")
            else:
                logger.warning("  Status=action но tool_calls отсутствуют")

        logger.info("")

    # Если достигли лимита итераций
    if iteration >= max_iterations and last_response and last_response.status != "final":
        logger.warning(f"⚠️  Достигнут лимит итераций ({max_iterations})")
        return AgentResponse(
            status="error",
            step=current_step,
            thought="",
            error=f"Достигнут лимит итераций ({max_iterations}), ответ не получен"
        )

    return last_response or AgentResponse(
        status="error",
        step=current_step,
        thought="",
        error="Агент не вернул ответ"
    )


def print_final_answer(response: AgentResponse) -> None:
    """Выводит финальный ответ агента в читаемом формате"""
    print(f"\n{'=' * 80}")
    print("ФИНАЛЬНЫЙ ОТВЕТ")
    print(f"{'=' * 80}\n")

    if response.status == "error":
        print(f"❌ ОШИБКА: {response.error}\n")
        return

    if not response.final_answer:
        print("⚠️  Финальный ответ отсутствует\n")
        return

    ans = response.final_answer

    print(f"📝 SUMMARY:")
    print(f"  {ans.summary}\n")

    print(f"📄 DETAILS:")
    for line in ans.details.split("\n"):
        print(f"  {line}")
    print()

    if ans.data:
        print(f"📊 DATA ({len(ans.data)} записей):")
        for i, item in enumerate(ans.data[:10], 1):
            entity = item.get("entity", "?")
            attr = item.get("attribute", "?")
            value = item.get("value", "?")
            print(f"  {i}. {entity} — {attr}: {value}")
        if len(ans.data) > 10:
            print(f"  ... и ещё {len(ans.data) - 10} записей")
        print()

    if ans.sources:
        print(f"📚 SOURCES:")
        for src in ans.sources:
            print(f"  • {src}")
        print()

    print(f"🎯 CONFIDENCE: {ans.confidence:.0%}")
    print(f"\n{'=' * 80}\n")


def main() -> None:
    """CLI для запуска аналитического агента"""
    import argparse

    parser = argparse.ArgumentParser(description="Аналитический агент с JSON-структурой")
    parser.add_argument("question", nargs="*", help="Вопрос для агента")
    parser.add_argument("--max-iter", type=int, default=6, help="Максимум итераций")
    parser.add_argument("--quiet", action="store_true", help="Минимальный вывод")

    args = parser.parse_args()

    if not args.question:
        question = input("Вопрос: ").strip()
        if not question:
            print("Вопрос не задан")
            return
    else:
        question = " ".join(args.question)

    # Запускаем агента
    response = run_analytical_agent(
        question=question,
        max_iterations=args.max_iter,
        verbose=not args.quiet,
    )

    # Выводим результат
    print_final_answer(response)

    # Сохраняем JSON для отладки
    output_file = Path("last_agent_response.json")
    output_file.write_text(
        response.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8"
    )
    logger.info(f"Полный ответ сохранён в {output_file}")


if __name__ == "__main__":
    main()

