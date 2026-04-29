# Структура LLM Запросов

## Явные Секции в Логах

Все запросы к LLM теперь структурированы с явными секциями для лучшей читаемости и анализа.

### Основные Секции

#### 1. **[SYSTEM]**
Системный промпт - определяет роль и инструкции для AI агента.

```
[SYSTEM]
# System Prompt для Аналитического AI-Агента

## Роль
Ты — **аналитический AI-агент**, работающий с документацией через tools.
...
```

#### 2. **[AVAILABLE_TOOLS]**
Список доступных инструментов в компактном JSON формате.

```
[AVAILABLE_TOOLS]
[
  {"name":"semantic_search","description":"Семантический поиск по эмбеддингам","parameters":{...}},
  {"name":"exact_search","description":"Точный поиск по подстроке","parameters":{...}},
  ...
]
```

**Особенности:**
- Компактный JSON (без лишних пробелов)
- Каждый инструмент на отдельной строке
- Полные JSON Schema параметров

#### 3. **[MESSAGES]**
История диалога (если есть предыдущие сообщения).

**Особенности:**
- [TOOL_CALLS] в компактном JSON (как и [AVAILABLE_TOOLS])
- Последовательная история всех взаимодействий

```
[MESSAGES]

[USER]
найди все СУБД

[ASSISTANT]
{"status":"action","step":2,...}

[TOOL_CALLS]
[{"name":"exact_search","args":{"substring":"СУБД","limit":30},"id":"call_abc123"}]

[TOOL_RESULT: exact_search]
**SearchChunksResult**
...
```

### Подсекции в [MESSAGES]

#### **[USER]**
Запрос пользователя.

```
[USER]
перечень СУБД в документации
```

#### **[ASSISTANT]**
Ответ AI агента (обычно JSON).

```
[ASSISTANT]
{
  "status": "action",
  "step": 2,
  "thought": "выполняю поиск по термину СУБД",
  "action": [...]
}
```

#### **[TOOL_CALLS]**
Вызовы инструментов в компактном JSON (если есть в [ASSISTANT]).

```
[TOOL_CALLS]
[{"name":"exact_search","args":{"substring":"СУБД","limit":30},"id":"call_def456","type":"tool_call"}]
```

**Особенности:**
- Компактный JSON (без лишних пробелов и отступов)
- Экономия токенов
- Легко парсится

#### **[TOOL_RESULT: tool_name]**
Результат выполнения конкретного инструмента.

```
[TOOL_RESULT: exact_search]
**SearchChunksResult**

- **query:** СУБД
- **chunks:** (5 элементов)
  1. ChunkResult(source=database.md, section=Архитектура > СУБД, line=150, content='...')
  2. ...
- **total_found:** 5
```

## Полный Пример Структуры Запроса

```
Model: ChatOllama
----------------------------------------

[SYSTEM]
# System Prompt для Аналитического AI-Агента
...

[AVAILABLE_TOOLS]
[
  {"name":"semantic_search","description":"Семантический поиск...","parameters":{...}},
  {"name":"exact_search","description":"Точный поиск...","parameters":{...}},
  ...
]

[MESSAGES]

[USER]
найди все СУБД

[ASSISTANT]
{"status":"action","step":2,"thought":"ищу СУБД","action":[{"tool":"exact_search","input":{"substring":"СУБД"}}]}

[TOOL_CALLS]
[{"name":"exact_search","args":{"substring":"СУБД","limit":30},"id":"call_123","type":"tool_call"}]

[TOOL_RESULT: exact_search]
**SearchChunksResult**
- **query:** СУБД
- **chunks:** (5 элементов)
  1. ChunkResult(source=database.md, section=Архитектура > СУБД, line=150, content='PostgreSQL...')
- **total_found:** 5
```

## Преимущества Структурирования

### ✅ Читаемость
- Каждая секция явно обозначена
- Легко найти нужную информацию в логах

### ✅ Анализ
- Понятна структура запроса
- Видно, какие tools доступны
- Прослеживается история диалога

### ✅ Отладка
- Быстро находишь проблему
- Видно, какие параметры передаются
- Понятно, что получено от tools

### ✅ Компактность
- Tools в компактном JSON (экономия токенов)
- Tool calls тоже в компактном JSON (без отступов)
- Структура не мешает чтению
- История логично группируется

## Где Используется

### Логи
Файл: `logs/_rag_llm.log`

Каждый запрос к LLM записывается с полной структурой.

### Коллбэки LangChain
`llm_call_logger.py` → `LangChainFileLogger` → `_fmt_message_list`

### System Prompt
`rag_lg_agent.py` → `_load_system_prompt()` → подстановка `[AVAILABLE_TOOLS]`

## Соответствие Стандартам

Структура соответствует:
- OpenAI Chat Completions API format
- LangChain messages format
- Читаемому Markdown формату для логов

## Дополнительные Секции (если нужны)

### **[CONTEXT]**
Дополнительный контекст (можно добавить при необходимости).

### **[EXAMPLES]**
Примеры для few-shot learning (можно добавить).

### **[CONSTRAINTS]**
Ограничения и правила (можно вынести отдельно).

---

**Обновлено:** 2026-04-27  
**Версия:** 1.0  
**Файлы:** `llm_call_logger.py`, `rag_lg_agent.py`

