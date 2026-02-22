#!/usr/bin/env python3
"""
LangChain + DeepSeek LLM implementation with tool calling
Использует структурированный промпт с плейсхолдерами
"""

import os
import json
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

# Импорт конфигурации логирования
from logging_config import get_logger

# Импорт конфигурации
from llm_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE,
    LANGSMITH_API_KEY,
    LANGSMITH_PROJECT,
    traceable,
    LANGSMITH_AVAILABLE
)

# Импорт Pydantic DTO моделей
from llm_dto import (
    ToolDefinition,
    SystemPromptConfig,
    UserProfile,
    ConversationSummary,
    ChatMessage,
    LLMRequest
)

logger = get_logger(__name__)



# ============================================================================
# LOGGING HELPERS
# ============================================================================

def dump_request(messages: List[Any], iteration: Optional[int] = None) -> None:
    """
    Логирует запрос к LLM в формате JSON

    Args:
        messages: Список LangChain сообщений
        iteration: Номер итерации (опционально)
    """
    logger.info("=" * 80)
    if iteration is not None:
        logger.info(f">>> SENDING REQUEST TO LLM (Iteration {iteration}) >>>")
    else:
        logger.info(">>> SENDING REQUEST TO LLM >>>")
    logger.info(f"Total messages in request: {len(messages)}")
    logger.info("-" * 80)
    for idx, msg in enumerate(messages):
        logger.info(f"Message [{idx+1}/{len(messages)}]: {type(msg).__name__}")
        logger.info(json.dumps(msg.dict(), indent=2, ensure_ascii=False))
    logger.info("=" * 80)


def dump_response(response: Any, iteration: Optional[int] = None) -> None:
    """
    Логирует ответ от LLM в формате JSON

    Args:
        response: Ответ от LLM
        iteration: Номер итерации (опционально)
    """
    logger.info("=" * 80)
    if iteration is not None:
        logger.info(f"<<< RECEIVED RESPONSE FROM LLM (Iteration {iteration}) <<<")
    else:
        logger.info("<<< RECEIVED RESPONSE FROM LLM <<<")
    logger.info(json.dumps(response.dict(), indent=2, ensure_ascii=False))
    logger.info("=" * 80)


# ============================================================================
# TOOLS DEFINITION (LangChain format)
# ============================================================================

@tool
def web_search(query: str, num_results: int = 10) -> str:
    """
    Поиск в интернете. Возвращает результаты поиска.

    Args:
        query: Поисковый запрос
        num_results: Количество результатов (по умолчанию 10)
    """
    # Placeholder - здесь должна быть реальная реализация поиска
    return f"Результаты поиска по запросу '{query}' (найдено {num_results} результатов)"


@tool
def code_execution(code: str) -> str:
    """
    Выполнение Python-кода в изолированной среде.

    Args:
        code: Python код для выполнения
    """
    # Placeholder - здесь должна быть безопасная изоляция
    try:
        # WARNING: Это небезопасно в production! Используйте sandbox
        result = eval(code, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"Ошибка выполнения: {str(e)}"


@tool
def browse_page(url: str, instructions: str) -> str:
    """
    Загрузка и суммирование конкретной страницы по URL.

    Args:
        url: URL страницы для загрузки
        instructions: Инструкции для обработки страницы
    """
    # Placeholder - здесь должна быть реальная загрузка и обработка
    return f"Контент страницы {url} обработан согласно инструкциям: {instructions}"


# ============================================================================
# LANGCHAIN SETUP
# ============================================================================

@traceable(name="create_deepseek_llm")
def create_deepseek_llm(
    api_key: str = DEEPSEEK_API_KEY,
    base_url: str = DEEPSEEK_BASE_URL,
    model: str = DEEPSEEK_MODEL,
    temperature: float = DEEPSEEK_TEMPERATURE
) -> ChatOpenAI:
    """
    Создает LangChain LLM для DeepSeek

    Args:
        api_key: API ключ DeepSeek
        base_url: Base URL для DeepSeek API
        model: Название модели
        temperature: Температура генерации

    Returns:
        Настроенный ChatOpenAI instance для DeepSeek API
    """
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=temperature,
    )


def create_prompt_template_from_request(request: LLMRequest) -> ChatPromptTemplate:
    """
    Создает промпт шаблон из Pydantic модели LLMRequest

    Args:
        request: LLMRequest с полной конфигурацией

    Returns:
        ChatPromptTemplate с плейсхолдерами
    """
    messages = [
        ("system", request.get_system_prompt_text())
    ]

    if request.user_profile:
        messages.append(("system", request.get_user_profile_text()))

    if request.conversation_summary:
        messages.append(("system", request.get_conversation_summary_text()))

    # Плейсхолдер для истории чата
    messages.append(MessagesPlaceholder(variable_name="chat_history", optional=True))

    # Плейсхолдер для запроса пользователя
    messages.append(("human", "{user_query}"))

    # Плейсхолдер для agent scratchpad (для tool calling)
    messages.append(MessagesPlaceholder(variable_name="agent_scratchpad"))

    return ChatPromptTemplate.from_messages(messages)


def create_prompt_template(
    system_prompt: str = None,
    user_profile: Optional[str] = None,
    conversation_summary: Optional[str] = None
) -> ChatPromptTemplate:
    """
    Создает промпт шаблон с плейсхолдерами (legacy метод для обратной совместимости)

    Args:
        system_prompt: Системный промпт
        user_profile: Профиль пользователя (опционально)
        conversation_summary: Суммари разговора (опционально)

    Returns:
        ChatPromptTemplate с плейсхолдерами
    """
    if system_prompt is None:
        system_prompt = SystemPromptConfig().render()

    messages = [
        ("system", system_prompt)
    ]

    if user_profile:
        messages.append(("system", user_profile))

    if conversation_summary:
        messages.append(("system", conversation_summary))

    # Плейсхолдер для истории чата
    messages.append(MessagesPlaceholder(variable_name="chat_history", optional=True))

    # Плейсхолдер для запроса пользователя
    messages.append(("human", "{user_query}"))

    # Плейсхолдер для agent scratchpad (для tool calling)
    messages.append(MessagesPlaceholder(variable_name="agent_scratchpad"))

    return ChatPromptTemplate.from_messages(messages)


# ============================================================================
# SIMPLE TOOL CALLING (without AgentExecutor)
# ============================================================================

def create_deepseek_with_tools(
    llm: ChatOpenAI,
    tools: List[Any]
) -> ChatOpenAI:
    """
    Создает LLM с привязанными инструментами для tool calling

    Args:
        llm: LangChain ChatOpenAI instance
        tools: Список инструментов

    Returns:
        ChatOpenAI с привязанными tools
    """
    return llm.bind_tools(tools)


@traceable(name="invoke_with_tools")
def invoke_with_tools(
    llm_with_tools: ChatOpenAI,
    request: LLMRequest,
    tools_dict: Dict[str, Any],
    max_iterations: int = 5
) -> str:
    """
    Вызывает LLM с tool calling в ручном режиме

    Args:
        llm_with_tools: LLM с привязанными инструментами
        request: LLMRequest запрос
        tools_dict: Словарь {tool_name: tool_function}
        max_iterations: Максимальное количество итераций

    Returns:
        Финальный ответ от LLM
    """
    messages = request.to_langchain_messages()

    # Логируем начальный запрос используя Pydantic сериализацию
    logger.info("=" * 80)
    logger.info("Initial LLMRequest (Pydantic):")
    logger.info(request.model_dump_json(indent=2))
    logger.info("=" * 80)

    logger.info("Initial Request Messages (LangChain):")
    for i, msg in enumerate(messages):
        logger.info(f"  Message {i+1}: {type(msg).__name__}")
        logger.info(json.dumps(msg.dict(), indent=2, ensure_ascii=False))
    logger.info("=" * 80)

    for iteration in range(max_iterations):
        logger.info(f"Iteration {iteration + 1}/{max_iterations}")

        # Логируем запрос к LLM
        dump_request(messages, iteration + 1)

        # Вызов LLM
        response = llm_with_tools.invoke(messages)

        # Логируем ответ от LLM
        dump_response(response, iteration + 1)

        # Проверяем, нужно ли вызывать инструменты
        if not response.tool_calls:
            # Нет tool calls - возвращаем ответ
            logger.info("No tool calls - returning final answer")
            return response.content

        # Есть tool calls - вызываем их
        logger.info(f"LLM wants to call {len(response.tool_calls)} tool(s)")

        messages.append(response)

        for tool_call in response.tool_calls:
            tool_name = tool_call['name']
            tool_args = tool_call['args']

            logger.info(f"Calling tool: {tool_name} with args: {tool_args}")

            if tool_name in tools_dict:
                try:
                    tool_result = tools_dict[tool_name].invoke(tool_args)
                    logger.info(f"Tool result: {tool_result}")
                except Exception as e:
                    tool_result = f"Error calling tool: {str(e)}"
                    logger.error(tool_result)
            else:
                tool_result = f"Unknown tool: {tool_name}"
                logger.warning(tool_result)

            messages.append(ToolMessage(
                content=str(tool_result),
                tool_call_id=tool_call['id']
            ))

            # Логируем добавленное tool message
            logger.info(f"Added ToolMessage to conversation (tool_call_id: {tool_call['id']})")

    # Достигли максимума итераций
    logger.warning(f"Reached max iterations ({max_iterations})")
    return "Reached maximum iterations without final answer"


# ============================================================================
# MAIN USAGE EXAMPLE
# ============================================================================

@traceable(name="main_example")
def main():
    """Пример использования с Pydantic моделями"""

    logger.info("=== DeepSeek LLM with LangChain + Pydantic Models ===")

    # 1. Создаем LLM
    llm = create_deepseek_llm()

    # 2. Определяем инструменты
    tools = [web_search, code_execution, browse_page]

    # 3. Создаем структурированный запрос через Pydantic

    # ...existing code...

    # Определяем доступные инструменты
    tool_definitions = [
        ToolDefinition(
            name="web_search",
            description="Поиск в интернете. Возвращает результаты поиска.",
            parameters={
                "query": {"type": "string", "required": True},
                "num_results": {"type": "integer", "required": False, "default": 10}
            }
        ),
        ToolDefinition(
            name="code_execution",
            description="Выполнение Python-кода в изолированной среде.",
            parameters={
                "code": {"type": "string", "required": True}
            }
        ),
        ToolDefinition(
            name="browse_page",
            description="Загрузка и суммирование конкретной страницы по URL.",
            parameters={
                "url": {"type": "string", "required": True},
                "instructions": {"type": "string", "required": True}
            }
        )
    ]

    # Создаем системный промпт
    system_prompt_config = SystemPromptConfig(
        assistant_name="Grok 4 от xAI",
        instructions="Отвечай честно, кратко, по делу.",
        tool_usage_instructions="Если нужно получить информацию извне или выполнить вычисления — используй инструменты.",
        current_date="14 февраля 2026",
        available_tools=tool_definitions
    )

    # Создаем профиль пользователя
    user_profile = UserProfile(
        name="Vadim",
        location="Poznań, PL",
        style="технический, без воды, любит конкретику"
    )

    # Создаем суммари разговора
    conversation_summary = ConversationSummary(
        summary_text="Vadim интересуется внутренней механикой LLM-систем, tool calling, структурой промптов.",
        key_topics=["LLM mechanics", "tool calling", "prompt engineering"]
    )

    # Создаем полный запрос
    llm_request = LLMRequest(
        system_prompt=system_prompt_config,
        user_profile=user_profile,
        conversation_summary=conversation_summary,
        user_query="Сколько будет 2+2*2? Используй code_execution для вычисления.",
        chat_history=[]
    )

    # 4. Создаем LLM с инструментами
    llm_with_tools = create_deepseek_with_tools(llm, tools)

    # Создаем словарь инструментов для быстрого доступа
    tools_dict = {
        "web_search": web_search,
        "code_execution": code_execution,
        "browse_page": browse_page
    }

    # 5. Выполняем запрос
    logger.info(f"User Query: {llm_request.user_query}")
    result = invoke_with_tools(llm_with_tools, llm_request, tools_dict)
    logger.info(f"Agent Response: {result}")

    # 6. Пример с историей чата через Pydantic
    logger.info("=== Example with Chat History (Pydantic) ===")

    llm_request_2 = LLMRequest(
        system_prompt=system_prompt_config,
        user_profile=user_profile,
        conversation_summary=conversation_summary,
        user_query="Как меня зовут?",
        chat_history=[
            ChatMessage(role="user", content="Привет, меня зовут Vadim"),
            ChatMessage(role="assistant", content="Привет, Vadim! Чем могу помочь?")
        ]
    )

    logger.info(f"User Query: {llm_request_2.user_query}")
    result2 = invoke_with_tools(llm_with_tools, llm_request_2, tools_dict)
    logger.info(f"Agent Response: {result2}")

    # 7. Демонстрация сериализации/десериализации
    logger.info("=== Pydantic Serialization Example ===")

    # Сериализуем запрос в JSON
    request_json = llm_request.model_dump_json(indent=2)
    logger.info("Request as JSON:")
    logger.info(request_json)

    # Десериализуем обратно
    llm_request_restored = LLMRequest.model_validate_json(request_json)
    logger.info(f"Restored request user_query: {llm_request_restored.user_query}")


# ============================================================================
# MANUAL TOOL CALLING (без agent loop)
# ============================================================================

@traceable(name="manual_tool_calling_example")
def manual_tool_calling_example():
    """
    Пример ручного вызова инструментов без agent loop используя Pydantic
    Вы сами контролируете процесс вызова инструментов
    """
    logger.info("=== Manual Tool Calling Example (Pydantic) ===")

    llm = create_deepseek_llm()

    # Bind tools к LLM
    llm_with_tools = llm.bind_tools([web_search, code_execution, browse_page])

    # Создаем запрос через Pydantic
    system_prompt_config = SystemPromptConfig(
        current_date="14 февраля 2026"
    )

    llm_request = LLMRequest(
        system_prompt=system_prompt_config,
        user_query="Найди информацию о LangChain в интернете",
        chat_history=[]
    )

    # Конвертируем в messages
    messages = llm_request.to_langchain_messages()

    # Логируем начальный запрос
    logger.info("=" * 60)
    logger.info("Initial Request:")
    logger.info(f"Query: {llm_request.user_query}")
    logger.info(f"Total messages: {len(messages)}")
    for i, msg in enumerate(messages):
        logger.info(f"  Message {i+1}: {type(msg).__name__}")
        logger.info(f"    Content: {msg.content}")
    logger.info("=" * 60)

    logger.info("Step 1: Initial LLM call")

    # Логируем запрос к LLM
    dump_request(messages)

    response = llm_with_tools.invoke(messages)

    # Логируем ответ от LLM
    dump_response(response)

    # Проверяем, хочет ли LLM вызвать инструмент
    if response.tool_calls:
        logger.info(f"Step 2: LLM wants to call tools: {len(response.tool_calls)} tool(s)")

        # Вызываем инструмент вручную
        for tool_call in response.tool_calls:
            tool_name = tool_call['name']
            tool_args = tool_call['args']

            logger.info(f"Calling tool: {tool_name}")
            logger.info(f"Args: {tool_args}")

            # Вызываем соответствующий инструмент
            if tool_name == "web_search":
                tool_result = web_search.invoke(tool_args)
            elif tool_name == "code_execution":
                tool_result = code_execution.invoke(tool_args)
            elif tool_name == "browse_page":
                tool_result = browse_page.invoke(tool_args)
            else:
                tool_result = f"Unknown tool: {tool_name}"

            logger.info(f"Tool result: {tool_result}")

            # Добавляем результат в messages и вызываем LLM снова
            messages.append(response)
            messages.append(ToolMessage(
                content=str(tool_result),
                tool_call_id=tool_call['id']
            ))

            logger.info(f"Added ToolMessage to conversation (total messages now: {len(messages)})")

        # Step 3: Вызываем LLM с результатами инструмента
        logger.info("Step 3: LLM call with tool results")

        # Логируем запрос к LLM
        dump_request(messages)

        final_response = llm_with_tools.invoke(messages)

        # Логируем ответ от LLM
        dump_response(final_response)
    else:
        logger.info(f"LLM response without tools: {response.content}")


if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("LangChain + DeepSeek LLM with Tool Calling")
    logger.info("=" * 80)

    if not DEEPSEEK_API_KEY:
        logger.warning("⚠️  WARNING: DEEPSEEK_API_KEY not set!")
        logger.warning("Please set it: export DEEPSEEK_API_KEY='your-api-key'")
        logger.warning("Running in demo mode with placeholders...")

    # Запускаем примеры
    try:
        main()
        manual_tool_calling_example()
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        logger.info("Make sure you have installed required packages:")
        logger.info("  pip install langchain langchain-openai")

