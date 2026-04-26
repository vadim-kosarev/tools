"""
Формат общения с LLM через структурированные messages.

Поддерживает полный цикл взаимодействия с LLM:
  - system промпт
  - user запрос
  - assistant ответ с tool_calls
  - tool результаты с привязкой к tool_call_id
  - доступные tools в JSON формате

Формат messages:
    [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "какие СУБД упоминаются в документации"},
        {"role": "assistant", "content": "...", "tool_calls": [{"id": "call_123", "function": {...}}]},
        {"role": "tool", "content": "...", "tool_call_id": "call_123"},
        ...
    ]

Использование:
    >>> conv = LLMConversation(
    ...     system_prompt="Ты ассистент для поиска в документации",
    ...     available_tools=[...],  # список LangChain tools
    ... )
    >>> conv.add_user_message("найди все СУБД")
    >>> response = llm.invoke(conv.get_messages())
    >>> conv.add_assistant_message(response)
    >>> if conv.has_pending_tool_calls():
    ...     results = execute_tools(conv.pending_tool_calls, tools)
    ...     conv.add_tool_results(results)
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic модели для структурированных messages
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    """Вызов инструмента из assistant message"""
    id: str = Field(description="Уникальный ID вызова (для связи с tool response)")
    type: str = Field(default="function", description="Тип вызова (всегда 'function')")
    function: dict[str, Any] = Field(
        description="Функция и аргументы: {'name': '...', 'arguments': '{...}'}"
    )


class Message(BaseModel):
    """Одно сообщение в диалоге"""
    role: str = Field(description="Роль: system | user | assistant | tool")
    content: str = Field(description="Содержимое сообщения")
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        description="Список вызовов инструментов (только для assistant)"
    )
    tool_call_id: str | None = Field(
        default=None,
        description="ID вызова инструмента (только для tool role)"
    )
    name: str | None = Field(
        default=None,
        description="Имя инструмента (только для tool role)"
    )


class ToolCallResult(BaseModel):
    """Результат выполнения одного tool call"""
    tool_call_id: str = Field(description="ID вызова из assistant message")
    tool_name: str = Field(description="Имя инструмента")
    result: str = Field(description="Результат выполнения (строка или JSON)")
    error: str | None = Field(default=None, description="Ошибка выполнения (если была)")


# ---------------------------------------------------------------------------
# LLMConversation - управление историей диалога с LLM
# ---------------------------------------------------------------------------

class LLMConversation:
    """
    Управление историей диалога с LLM в структурированном формате.

    Хранит:
      - system_prompt: системный промпт (задаёт роль и инструкции для LLM)
      - user_prompt: текущий запрос пользователя
      - available_tools: список доступных инструментов в LangChain формате
      - messages: список всех сообщений в хронологическом порядке

    Поддерживает:
      - Добавление user/assistant/tool messages
      - Автоматическую генерацию tool_call_id
      - Конвертацию в LangChain BaseMessage формат для invoke
      - Извлечение pending tool calls для выполнения
      - Добавление результатов tool calls

    Args:
        system_prompt: Системный промпт (роль и инструкции для LLM)
        user_prompt: Начальный запрос пользователя (опционально)
        available_tools: Список LangChain BaseTool инструментов
        max_history: Максимум сообщений в истории (0 = без ограничений)
    """

    def __init__(
        self,
        system_prompt: str,
        user_prompt: str | None = None,
        available_tools: list[BaseTool] | None = None,
        max_history: int = 0,
    ) -> None:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt or ""
        self.available_tools = available_tools or []
        self.max_history = max_history
        self.messages: list[Message] = []

        # Добавляем system message
        if system_prompt:
            self.messages.append(Message(role="system", content=system_prompt))

        # Добавляем начальный user message если задан
        if user_prompt:
            self.messages.append(Message(role="user", content=user_prompt))

    # ── Добавление сообщений ──────────────────────────────────────────────

    def add_user_message(self, content: str) -> None:
        """Добавляет user message в историю"""
        self.messages.append(Message(role="user", content=content))
        self._trim_history()
        logger.debug(f"Добавлено user message: {content[:100]}...")

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Добавляет assistant message в историю.

        Args:
            content: Текст ответа ассистента
            tool_calls: Список вызовов инструментов в формате:
                [{"id": "call_123", "type": "function",
                  "function": {"name": "tool_name", "arguments": "{...}"}}]
        """
        parsed_tool_calls = None
        if tool_calls:
            parsed_tool_calls = [ToolCall(**tc) for tc in tool_calls]

        self.messages.append(Message(
            role="assistant",
            content=content,
            tool_calls=parsed_tool_calls,
        ))
        self._trim_history()

        if tool_calls:
            logger.debug(
                f"Добавлено assistant message с {len(tool_calls)} tool_calls: "
                f"{content[:100]}..."
            )
        else:
            logger.debug(f"Добавлено assistant message: {content[:100]}...")

    def add_assistant_from_langchain(self, message: AIMessage) -> None:
        """
        Добавляет assistant message из LangChain AIMessage.

        Автоматически извлекает content и tool_calls из AIMessage.
        """
        content = message.content if isinstance(message.content, str) else str(message.content)

        # Извлекаем tool_calls если есть
        tool_calls_list = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls_list = []
            for tc in message.tool_calls:
                # LangChain tool_call формат:
                # {"name": "...", "args": {...}, "id": "...", "type": "tool_call"}
                tc_id = tc.get("id") or f"call_{uuid4().hex[:8]}"

                # Конвертируем args в JSON строку для function.arguments
                args = tc.get("args", {})
                args_json = json.dumps(args, ensure_ascii=False)

                tool_calls_list.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tc.get("name", "unknown"),
                        "arguments": args_json,
                    }
                })

        self.add_assistant_message(content, tool_calls_list)

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> None:
        """
        Добавляет результат выполнения одного инструмента.

        Args:
            tool_call_id: ID вызова из assistant message
            tool_name: Имя инструмента
            result: Результат выполнения (строка или JSON)
        """
        self.messages.append(Message(
            role="tool",
            content=result,
            tool_call_id=tool_call_id,
            name=tool_name,
        ))
        self._trim_history()
        logger.debug(
            f"Добавлен tool result для {tool_name} (call_id={tool_call_id}): "
            f"{result[:100]}..."
        )

    def add_tool_results(self, results: list[ToolCallResult]) -> None:
        """
        Добавляет результаты выполнения нескольких инструментов.

        Args:
            results: Список результатов в формате ToolCallResult
        """
        for res in results:
            content = res.error if res.error else res.result
            self.add_tool_result(res.tool_call_id, res.tool_name, content)

    # ── Извлечение данных ─────────────────────────────────────────────────

    def get_messages(self) -> list[Message]:
        """Возвращает все messages в структурированном формате"""
        return self.messages.copy()

    def get_messages_for_llm(self) -> list[BaseMessage]:
        """
        Конвертирует messages в LangChain BaseMessage формат для invoke.

        Returns:
            Список BaseMessage (SystemMessage, HumanMessage, AIMessage, ToolMessage)
        """
        result: list[BaseMessage] = []

        for msg in self.messages:
            if msg.role == "system":
                result.append(SystemMessage(content=msg.content))

            elif msg.role == "user":
                result.append(HumanMessage(content=msg.content))

            elif msg.role == "assistant":
                # AIMessage с tool_calls если есть
                if msg.tool_calls:
                    # Конвертируем обратно в LangChain формат
                    lc_tool_calls = []
                    for tc in msg.tool_calls:
                        # Парсим arguments из JSON строки
                        try:
                            args = json.loads(tc.function["arguments"])
                        except Exception:
                            args = {"raw": tc.function["arguments"]}

                        lc_tool_calls.append({
                            "name": tc.function["name"],
                            "args": args,
                            "id": tc.id,
                            "type": "tool_call",
                        })

                    result.append(AIMessage(content=msg.content, tool_calls=lc_tool_calls))
                else:
                    result.append(AIMessage(content=msg.content))

            elif msg.role == "tool":
                result.append(ToolMessage(
                    content=msg.content,
                    tool_call_id=msg.tool_call_id or "",
                    name=msg.name or "",
                ))

        return result

    def has_pending_tool_calls(self) -> bool:
        """Проверяет, есть ли невыполненные tool calls в последнем assistant message"""
        if not self.messages:
            return False

        last_msg = self.messages[-1]
        return last_msg.role == "assistant" and last_msg.tool_calls is not None

    def get_pending_tool_calls(self) -> list[ToolCall]:
        """
        Возвращает список невыполненных tool calls из последнего assistant message.

        Returns:
            Список ToolCall или пустой список
        """
        if not self.has_pending_tool_calls():
            return []

        last_msg = self.messages[-1]
        return last_msg.tool_calls or []

    # ── Утилиты ───────────────────────────────────────────────────────────

    def _trim_history(self) -> None:
        """Обрезает историю до max_history сообщений (сохраняет system message)"""
        if self.max_history <= 0:
            return

        if len(self.messages) <= self.max_history:
            return

        # Сохраняем system message (первое)
        system_msg = self.messages[0] if self.messages[0].role == "system" else None

        # Берём последние max_history-1 сообщений
        if system_msg:
            self.messages = [system_msg] + self.messages[-(self.max_history - 1):]
        else:
            self.messages = self.messages[-self.max_history:]

        logger.debug(f"История обрезана до {len(self.messages)} сообщений")

    def clear_messages(self) -> None:
        """Очищает всю историю сообщений (включая system message)"""
        self.messages.clear()
        logger.debug("История сообщений очищена")

    def get_last_assistant_message(self) -> Message | None:
        """Возвращает последнее assistant message или None"""
        for msg in reversed(self.messages):
            if msg.role == "assistant":
                return msg
        return None

    def get_conversation_json(self) -> str:
        """Возвращает всю историю в JSON формате для логирования"""
        data = {
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "available_tools": [t.name for t in self.available_tools],
            "messages": [msg.model_dump(exclude_none=True) for msg in self.messages],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def format_for_display(self, max_content_len: int = 200) -> str:
        """
        Форматирует историю для отображения в логах.

        Args:
            max_content_len: Максимальная длина content для вывода

        Returns:
            Строка с форматированной историей
        """
        lines = [
            "=" * 80,
            f"LLM Conversation ({len(self.messages)} messages)",
            "=" * 80,
        ]

        for i, msg in enumerate(self.messages, 1):
            content = msg.content[:max_content_len]
            if len(msg.content) > max_content_len:
                content += "..."

            lines.append(f"\n[{i}] {msg.role.upper()}")
            lines.append(f"    {content}")

            if msg.tool_calls:
                lines.append(f"    tool_calls: {len(msg.tool_calls)}")
                for tc in msg.tool_calls[:3]:
                    lines.append(f"      → {tc.function['name']}({tc.id})")

            if msg.tool_call_id:
                lines.append(f"    tool_call_id: {msg.tool_call_id}")

        lines.append("=" * 80)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Утилиты для работы с tool calls
# ---------------------------------------------------------------------------

def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[BaseTool],
) -> list[ToolCallResult]:
    """
    Выполняет список tool calls и возвращает результаты.

    Args:
        tool_calls: Список вызовов инструментов из assistant message
        tools: Список доступных LangChain BaseTool инструментов

    Returns:
        Список результатов выполнения
    """
    tools_map = {tool.name: tool for tool in tools}
    results: list[ToolCallResult] = []

    for tc in tool_calls:
        tool_name = tc.function["name"]
        tool = tools_map.get(tool_name)

        if not tool:
            logger.warning(f"Инструмент не найден: {tool_name}")
            results.append(ToolCallResult(
                tool_call_id=tc.id,
                tool_name=tool_name,
                result="",
                error=f"Инструмент '{tool_name}' не найден",
            ))
            continue

        # Парсим аргументы из JSON строки
        try:
            args = json.loads(tc.function["arguments"])
        except Exception as exc:
            logger.error(f"Ошибка парсинга аргументов для {tool_name}: {exc}")
            results.append(ToolCallResult(
                tool_call_id=tc.id,
                tool_name=tool_name,
                result="",
                error=f"Ошибка парсинга аргументов: {exc}",
            ))
            continue

        # Вызываем инструмент
        try:
            logger.info(
                f"Вызов инструмента {tool_name}\n"
                f"  Аргументы: {json.dumps(args, ensure_ascii=False)[:200]}"
            )
            result = tool.invoke(args)

            # Конвертируем результат в строку
            if isinstance(result, (dict, list)):
                result_str = json.dumps(result, ensure_ascii=False)
            else:
                result_str = str(result)

            results.append(ToolCallResult(
                tool_call_id=tc.id,
                tool_name=tool_name,
                result=result_str,
            ))

            logger.info(
                f"Результат {tool_name}: {result_str[:200]}..."
            )

        except Exception as exc:
            logger.error(f"Ошибка выполнения {tool_name}: {exc}", exc_info=True)
            results.append(ToolCallResult(
                tool_call_id=tc.id,
                tool_name=tool_name,
                result="",
                error=f"Ошибка выполнения: {exc}",
            ))

    return results


def convert_langchain_tools_to_json(tools: list[BaseTool]) -> list[dict[str, Any]]:
    """
    Конвертирует LangChain BaseTool в JSON Schema формат для available_tools.

    Args:
        tools: Список LangChain инструментов

    Returns:
        Список словарей в формате OpenAI tools
    """
    result: list[dict[str, Any]] = []

    for tool in tools:
        # Извлекаем JSON schema из tool.args_schema
        if hasattr(tool, "args_schema") and tool.args_schema:
            schema = tool.args_schema.model_json_schema()
        else:
            schema = {"type": "object", "properties": {}}

        result.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": schema,
            }
        })

    return result

