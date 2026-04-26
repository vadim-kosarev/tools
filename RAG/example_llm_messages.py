"""
Пример использования нового формата messages для общения с LLM.

Демонстрирует полный цикл:
  1. Создание диалога с system prompt и user query
  2. Первый вызов LLM → получение tool_calls
  3. Выполнение инструментов → добавление результатов
  4. Второй вызов LLM с учётом результатов → финальный ответ

Запуск:
    python example_llm_messages.py
"""
import logging
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from clickhouse_store import ClickHouseVectorStore
from kb_tools import create_kb_tools
from llm_messages import LLMConversation, execute_tool_calls
from rag_chat import build_llm, build_vectorstore, settings
from logging_config import setup_logging

load_dotenv()

logger = setup_logging("example_llm_messages")


def example_multi_round_search() -> None:
    """
    Пример многораундового поиска с использованием LLMConversation.

    Сценарий:
      1. Пользователь задаёт вопрос: "какие СУБД используются?"
      2. LLM решает использовать multi_term_exact_search для поиска по терминам
      3. Инструмент возвращает найденные упоминания PostgreSQL, MySQL, MongoDB
      4. LLM формирует финальный ответ на основе результатов
    """
    logger.info("=" * 80)
    logger.info("ПРИМЕР: Многораундовый поиск с LLMConversation")
    logger.info("=" * 80)

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

    # Системный промпт - задаёт роль и инструкции
    system_prompt = """Ты - эксперт-аналитик по технической документации системы СОИБ КЦОИ Банка России.

Твоя задача - находить информацию в документации используя доступные инструменты.

СТРОГИЕ ПРАВИЛА:
  [!] ЗАПРЕЩЕНО придумывать, домысливать или использовать общие знания
  [+] Работай ТОЛЬКО с информацией из инструментов (tools)
  [+] Используй историю из messages для понимания контекста
  [+] Если какой-то инструмент не дал результатов - попробуй другой подход
  [+] Каждое утверждение должно иметь источник из найденных данных

Доступные инструменты:
  - semantic_search: семантический поиск по эмбеддингам
  - exact_search: точный поиск по подстроке
  - multi_term_exact_search: поиск по списку терминов с ранжированием
  - find_sections_by_term: поиск разделов содержащих термин
  - find_relevant_sections: двухэтапный поиск разделов
  - get_section_content: полный текст раздела из файла
  - read_table: чтение строк таблицы
  - regex_search: regex-поиск по файлам

Отвечай на русском языке, структурированно, с указанием источников."""

    user_prompt = "какие типы СУБД упоминаются в документации?"

    # Создаём диалог
    conv = LLMConversation(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        available_tools=tools,
    )

    logger.info(f"\nВопрос пользователя: {user_prompt}")
    logger.info("=" * 80)

    # Раунд 1: LLM анализирует вопрос и выбирает инструменты
    logger.info("\n[РАУНД 1] Первый вызов LLM...")

    # Привязываем инструменты к LLM (tool calling)
    llm_with_tools = llm.bind_tools(tools)

    # Вызываем LLM с историей messages
    response = llm_with_tools.invoke(conv.get_messages_for_llm())
    conv.add_assistant_from_langchain(response)

    # Показываем ответ LLM
    last_msg = conv.get_last_assistant_message()
    if last_msg:
        logger.info(f"\nОтвет LLM: {last_msg.content[:300]}...")

        if last_msg.tool_calls:
            logger.info(f"\nLLM запросил {len(last_msg.tool_calls)} инструмент(а):")
            for i, tc in enumerate(last_msg.tool_calls, 1):
                logger.info(
                    f"  {i}. {tc.function['name']}({tc.function['arguments'][:100]}...)"
                )

    # Раунд 2: Выполняем tool calls если есть
    if conv.has_pending_tool_calls():
        logger.info("\n[РАУНД 2] Выполнение инструментов...")

        tool_calls = conv.get_pending_tool_calls()
        results = execute_tool_calls(tool_calls, tools)

        # Показываем результаты
        for i, res in enumerate(results, 1):
            if res.error:
                logger.warning(f"  {i}. {res.tool_name}: ОШИБКА - {res.error}")
            else:
                result_preview = res.result[:200].replace("\n", " ")
                logger.info(f"  {i}. {res.tool_name}: {result_preview}...")

        # Добавляем результаты в диалог
        conv.add_tool_results(results)

        # Раунд 3: LLM формирует финальный ответ на основе результатов
        logger.info("\n[РАУНД 3] Финальный вызов LLM с результатами инструментов...")

        response = llm.invoke(conv.get_messages_for_llm())
        conv.add_assistant_from_langchain(response)

    # Показываем финальный ответ
    logger.info("\n" + "=" * 80)
    logger.info("ФИНАЛЬНЫЙ ОТВЕТ:")
    logger.info("=" * 80)

    final_msg = conv.get_last_assistant_message()
    if final_msg:
        logger.info(f"\n{final_msg.content}")

    # Показываем статистику диалога
    logger.info("\n" + "=" * 80)
    logger.info("СТАТИСТИКА ДИАЛОГА:")
    logger.info("=" * 80)
    logger.info(f"  Всего сообщений: {len(conv.messages)}")
    logger.info(f"  - system: 1")
    logger.info(f"  - user: {sum(1 for m in conv.messages if m.role == 'user')}")
    logger.info(f"  - assistant: {sum(1 for m in conv.messages if m.role == 'assistant')}")
    logger.info(f"  - tool: {sum(1 for m in conv.messages if m.role == 'tool')}")

    # Можем получить всю историю в JSON для логирования
    # logger.debug(f"\nПолная история диалога:\n{conv.get_conversation_json()}")


def example_simple_query() -> None:
    """
    Простой пример: один вопрос → один ответ без tool calls.
    """
    logger.info("\n" + "=" * 80)
    logger.info("ПРИМЕР: Простой запрос без tool calls")
    logger.info("=" * 80)

    llm = build_llm()

    conv = LLMConversation(
        system_prompt="Ты - дружелюбный ассистент. Отвечай кратко и по делу.",
        user_prompt="Объясни что такое RAG в двух предложениях",
    )

    logger.info(f"\nВопрос: {conv.user_prompt}")

    response = llm.invoke(conv.get_messages_for_llm())
    conv.add_assistant_from_langchain(response)

    final_msg = conv.get_last_assistant_message()
    if final_msg:
        logger.info(f"\nОтвет: {final_msg.content}")


def main() -> None:
    """Запускает примеры использования LLMConversation"""

    # Пример 1: Многораундовый поиск с tool calls
    example_multi_round_search()

    # Пример 2: Простой запрос
    # example_simple_query()


if __name__ == "__main__":
    main()

