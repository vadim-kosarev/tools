# Формат общения с LLM — Quick Reference

## 📦 Структура объекта

```python
{
    "SYSTEM_PROMPT": "ты ассистент, твоя задача....",
    "USER_PROMPT": "я как пользователь хочу найти...",
    "available_tools": [...],  # JSON tools
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "...", "tool_calls": [...]},
        {"role": "tool", "content": "...", "tool_call_id": "..."},
        ...
    ]
}
```

## 🎭 Роли сообщений

| Роль | Назначение | Поля |
|------|------------|------|
| `system` | Системный промпт | `content` |
| `user` | Запрос пользователя | `content` |
| `assistant` | Ответ LLM | `content`, `tool_calls?` |
| `tool` | Результат инструмента | `content`, `tool_call_id`, `name` |

## 🔧 Основные функции

### Создание диалога

```python
from llm_messages import LLMConversation

conv = LLMConversation(
    system_prompt="Ты ассистент для поиска в документации",
    user_prompt="найди все СУБД",
    available_tools=tools,
)
```

### Цикл взаимодействия

```python
# 1. Вызов LLM
llm_with_tools = llm.bind_tools(tools)
response = llm_with_tools.invoke(conv.get_messages_for_llm())
conv.add_assistant_from_langchain(response)

# 2. Выполнение tool calls (если есть)
if conv.has_pending_tool_calls():
    tool_calls = conv.get_pending_tool_calls()
    results = execute_tool_calls(tool_calls, tools)
    conv.add_tool_results(results)
    
    # 3. Финальный ответ
    response = llm.invoke(conv.get_messages_for_llm())
    conv.add_assistant_from_langchain(response)

# 4. Получение результата
answer = conv.get_last_assistant_message().content
```

## 📊 Формат tool_calls

```python
{
    "id": "call_123",                    # Уникальный ID
    "type": "function",                  # Всегда "function"
    "function": {
        "name": "semantic_search",       # Имя инструмента
        "arguments": "{\"query\": ...}"  # JSON строка с аргументами
    }
}
```

## 📊 Формат tool результата

```python
{
    "role": "tool",
    "content": "Найдено: PostgreSQL в разделе 3.2, MySQL в 4.1...",
    "tool_call_id": "call_123",          # Связь с tool_calls
    "name": "semantic_search"            # Имя инструмента
}
```

## 🎯 Преимущества

1. **Полная история** - все сообщения в одном объекте
2. **Прозрачность** - явная связь tool_call → результат через `tool_call_id`
3. **Отладка** - полная трассировка всех шагов диалога
4. **Универсальность** - единый формат для всех агентов
5. **Переиспользование** - история сохраняется между раундами

## 📝 Пример полного диалога

```python
messages = [
    # Инициализация
    {"role": "system", "content": "Ты ассистент для поиска..."},
    {"role": "user", "content": "какие СУБД используются?"},
    
    # Раунд 1: LLM анализирует и запрашивает инструменты
    {
        "role": "assistant",
        "content": "Поищу информацию о СУБД в документации...",
        "tool_calls": [
            {
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "multi_term_exact_search",
                    "arguments": "{\"terms\": [\"PostgreSQL\", \"MySQL\", \"MongoDB\", \"Oracle\"]}"
                }
            }
        ]
    },
    
    # Результат инструмента
    {
        "role": "tool",
        "content": "Найдено 45 упоминаний:\n  • PostgreSQL - 20 раз\n  • MySQL - 15 раз\n  • MongoDB - 10 раз",
        "tool_call_id": "call_123",
        "name": "multi_term_exact_search"
    },
    
    # Раунд 2: LLM формирует финальный ответ
    {
        "role": "assistant",
        "content": "В документации упоминаются следующие типы СУБД:\n\n1. **PostgreSQL** - 20 упоминаний\n2. **MySQL** - 15 упоминаний\n3. **MongoDB** - 10 упоминаний\n\nИсточники: [найдено через multi_term_exact_search]"
    }
]
```

## 🔗 См. также

- [`llm_messages.py`](./llm_messages.py) - исходный код модуля
- [`example_llm_messages.py`](./example_llm_messages.py) - рабочий пример использования
- [`README.md`](./README.md#-формат-общения-с-llm) - подробная документация

