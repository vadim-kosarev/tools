#!/usr/bin/env python3
"""
LLM DTO: Pydantic models for building LLM requests
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage


# ============================================================================
# BASE DTO CLASS WITH FILE LOADING
# ============================================================================

class BaseDTO(BaseModel):
    """
    Базовый класс для всех DTO с поддержкой загрузки из файла

    Использование:
        # Загрузка из файла
        obj = MyDTO(load_from_file="path/to/file.json")

        # Обычное создание
        obj = MyDTO(field1="value1", field2="value2")
    """

    @model_validator(mode='before')
    @classmethod
    def load_from_file_if_specified(cls, data: Any) -> Dict[str, Any]:
        """
        Загружает данные из файла если указан параметр load_from_file
        Поля, не указанные в файле, получают дефолтные значения
        """
        if isinstance(data, dict) and 'load_from_file' in data:
            file_path = data.pop('load_from_file')

            if file_path:
                path = Path(file_path)
                if not path.exists():
                    raise FileNotFoundError(f"File not found: {file_path}")

                # Загружаем данные из файла
                with open(path, 'r', encoding='utf-8') as f:
                    file_data = json.load(f)

                # Объединяем данные из файла с переданными параметрами
                # Приоритет у параметров, переданных явно
                merged_data = {**file_data, **data}
                return merged_data

        return data

    def save_to_file(self, file_path: Union[str, Path]) -> None:
        """
        Сохраняет DTO объект в JSON файл

        Args:
            file_path: Путь к файлу для сохранения
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.model_dump_json(indent=2))


# ============================================================================
# PYDANTIC MODELS FOR STRUCTURED PROMPTS
# ============================================================================

class ToolDefinition(BaseDTO):
    """Определение инструмента"""
    name: str = Field(..., description="Название инструмента")
    description: str = Field(..., description="Описание функциональности")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Параметры инструмента")


class SystemPromptConfig(BaseDTO):
    """Конфигурация системного промпта"""
    assistant_name: str = Field(default="Grok 4 от xAI", description="Название ассистента")
    instructions: str = Field(
        default="Отвечай честно, кратко, по делу.",
        description="Основные инструкции"
    )
    tool_usage_instructions: str = Field(
        default="Если нужно получить информацию извне или выполнить вычисления — используй инструменты.",
        description="Инструкции по использованию инструментов"
    )
    current_date: str = Field(
        default_factory=lambda: datetime.now().strftime("%d %B %Y %H:%M:%S"),
        description="Текущая дата"
    )
    available_tools: List[ToolDefinition] = Field(
        default_factory=list,
        description="Доступные инструменты"
    )

    def render(self) -> str:
        """Рендерит системный промпт в текстовый формат"""
        # Build system prompt content
        system_content = f"Ты — {self.assistant_name}. {self.instructions}"

        # Add tool usage instructions if present
        if self.tool_usage_instructions:
            system_content += f"\n{self.tool_usage_instructions}"

        # Add current date
        system_content += f"\nТекущая дата: {self.current_date}."

        # Add tools section if tools are available
        if self.available_tools:
            system_content += "\nДоступны инструменты — используй их только когда действительно нужно."
            system_content += "\nНе объясняй механику инструментов, если об этом не спрашивают явно."

        return f"<system>\n{system_content}\n</system>"



class UserProfile(BaseDTO):
    """Профиль пользователя"""
    name: str = Field(..., description="Имя пользователя")
    location: str = Field(..., description="Местоположение пользователя")
    style: str = Field(..., description="Стиль общения пользователя")
    additional_info: Dict[str, str] = Field(
        default_factory=dict,
        description="Дополнительная информация о пользователе"
    )

    def render(self) -> str:
        """Рендерит профиль пользователя в текстовый формат"""
        user_info = f"DisplayName: {self.name}\n"
        user_info += f"Location: {self.location}\n"
        user_info += f"Preferred language: {self.style}"

        # Add additional info if present
        if self.additional_info:
            for key, value in self.additional_info.items():
                user_info += f"\n{key}: {value}"

        return f"<user_info>\n{user_info}\n</user_info>"


class ConversationSummary(BaseDTO):
    """Суммарная информация о разговоре"""
    summary_text: str = Field(..., description="Текст суммари предыдущих диалогов")
    key_topics: List[str] = Field(
        default_factory=list,
        description="Ключевые темы обсуждения"
    )
    last_updated: Optional[str] = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        description="Timestamp последнего обновления в ISO format"
    )

    def render(self) -> str:
        """Рендерит суммари разговора в текстовый формат"""
        summary_content = self.summary_text

        if self.key_topics:
            summary_content += f"\nПоследние темы: {', '.join(self.key_topics)}."

        return f'<running_summary last_updated="{self.last_updated}">\n{summary_content}\n</running_summary>'


class ChatMessage(BaseDTO):
    """Сообщение в чате"""
    role: str = Field(..., description="Роль отправителя: 'user', 'assistant', 'system'")
    content: str = Field(..., description="Содержание сообщения")
    timestamp: Optional[str] = Field(
        None,
        description="Timestamp сообщения в ISO format (опционально)"
    )

    def to_langchain_message(self):
        """Конвертирует в соответствующий LangChain message тип"""
        if self.role == 'user':
            return HumanMessage(content=self.content)
        elif self.role == 'assistant':
            return AIMessage(content=self.content)
        elif self.role == 'system':
            return SystemMessage(content=self.content)
        else:
            raise ValueError(f"Unknown role: {self.role}")


class LLMRequest(BaseDTO):
    """Полный запрос к LLM с всеми компонентами"""
    system_prompt: SystemPromptConfig = Field(..., description="Конфигурация системного промпта")
    user_profile: Optional[UserProfile] = Field(None, description="Профиль пользователя")
    conversation_summary: Optional[ConversationSummary] = Field(None, description="Суммари разговора")
    key_facts: List[str] = Field(
        default_factory=list,
        description="Ключевые факты о пользователе/контексте"
    )
    user_query: str = Field(..., description="Текущий запрос пользователя")
    chat_history: List[ChatMessage] = Field(
        default_factory=list,
        description="История чата в виде списка ChatMessage объектов"
    )

    class Config:
        arbitrary_types_allowed = True

    def get_system_prompt_text(self) -> str:
        """Получить полный текст системного промпта"""
        return self.system_prompt.render()

    def get_user_profile_text(self) -> Optional[str]:
        """Получить текст профиля пользователя"""
        return self.user_profile.render() if self.user_profile else None

    def get_conversation_summary_text(self) -> Optional[str]:
        """Получить текст суммари разговора"""
        return self.conversation_summary.render() if self.conversation_summary else None

    def get_key_facts_text(self) -> Optional[str]:
        """Получить текст ключевых фактов"""
        if not self.key_facts:
            return None

        facts_text = "\n".join(f"• {fact}" for fact in self.key_facts)
        return f"<key_facts>\n{facts_text}\n</key_facts>"

    def get_chat_history_text(self) -> Optional[str]:
        """Получить текст истории чата в формате <chat_history>"""
        if not self.chat_history:
            return None

        history_messages = []
        for chat_msg in self.chat_history:
            # Add timestamp if available in content or use placeholder
            timestamp = getattr(chat_msg, 'timestamp', '')
            timestamp_attr = f' timestamp="{timestamp}"' if timestamp else ''
            history_messages.append(
                f'<msg role="{chat_msg.role}"{timestamp_attr}>\n{chat_msg.content}\n</msg>'
            )

        return f"<chat_history>\n{''.join(history_messages)}\n</chat_history>"

    def to_langchain_messages(self) -> List[Any]:
        """Конвертирует запрос в список LangChain сообщений"""
        messages = [SystemMessage(content=self.get_system_prompt_text())]

        # Добавляем профиль пользователя
        if self.user_profile:
            messages.append(SystemMessage(content=self.get_user_profile_text()))

        # Добавляем суммари разговора
        if self.conversation_summary:
            messages.append(SystemMessage(content=self.get_conversation_summary_text()))

        # Добавляем историю чата используя ChatMessage.to_langchain_message()
        for chat_msg in self.chat_history:
            messages.append(chat_msg.to_langchain_message())

        # Добавляем текущий запрос
        messages.append(HumanMessage(content=self.user_query))

        return messages

