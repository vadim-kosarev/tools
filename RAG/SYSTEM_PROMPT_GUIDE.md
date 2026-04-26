# Системный промпт для аналитического агента — Quick Start

## 📋 Обзор

Файл [`system_prompt.md`](./system_prompt.md) содержит системный промпт для аналитического AI-агента, работающего по итеративной схеме: **plan → action → observation → final**

## 🎯 Основной принцип

Агент **НЕ отвечает сразу**, а работает пошагово:

1. **plan** — анализирует задачу, строит план действий
2. **action** — вызывает инструменты (tools)
3. **observation** — обрабатывает результаты
4. **final** — формирует итоговый ответ

## 📊 Формат ответа (JSON)

```json
{
  "status": "plan | action | final | error",
  "step": 1,
  "thought": "краткое рассуждение",
  "plan": ["шаг 1", "шаг 2"],
  "action": {
    "tool": "semantic_search",
    "input": {"query": "..."}
  },
  "observation": "результат предыдущего шага",
  "final_answer": {
    "summary": "краткий ответ",
    "details": "подробное объяснение",
    "data": [
      {"entity": "PostgreSQL", "attribute": "ip", "value": "10.0.0.1"}
    ],
    "sources": ["doc1.md", "doc2.md"],
    "confidence": 0.87
  }
}
```

## 🔧 Использование

### 1. Загрузка промпта

```python
from system_prompts import load_system_prompt, get_prompt_by_name

# Из файла system_prompt.md
prompt = load_system_prompt()

# Или по имени
prompt = get_prompt_by_name("analytical_agent")
```

### 2. Создание агента

```python
from llm_messages import LLMConversation

conv = LLMConversation(
    system_prompt=prompt,
    user_prompt="какие СУБД используются?",
    available_tools=tools,
)
```

### 3. Итеративная работа

```python
# Цикл: plan → action → observation → final
for step in range(max_iterations):
    # Вызов LLM
    response = llm.invoke(conv.get_messages_for_llm())
    conv.add_assistant_from_langchain(response)
    
    # Парсинг JSON ответа
    agent_response = parse_json_response(response.content)
    
    if agent_response.status == "final":
        break
    
    elif agent_response.status == "action":
        # Выполнение tool calls
        tool_calls = conv.get_pending_tool_calls()
        results = execute_tool_calls(tool_calls, tools)
        conv.add_tool_results(results)
```

## 🚀 Готовый пример

Файл [`example_analytical_agent.py`](./example_analytical_agent.py) содержит полную реализацию:

```bash
# Запуск агента
python example_analytical_agent.py "какие СУБД используются?"

# С ограничением итераций
python example_analytical_agent.py "найди все IP серверов" --max-iter 10

# Минимальный вывод
python example_analytical_agent.py "что такое КЦОИ" --quiet
```

## 📚 Доступные промпты

| Имя | Назначение | Размер |
|-----|------------|--------|
| `analytical_agent` | Аналитический агент с JSON структурой | 5,083 символов |
| `simple_chat` | Простой чат без структуры | 1,024 символов |
| `query_expansion` | Расширение запроса | 641 символов |
| `answer_evaluation` | Оценка качества ответа | 674 символов |

```bash
# Показать список промптов
python system_prompts.py --list

# Показать конкретный промпт
python system_prompts.py --show analytical_agent
```

## 🎓 Пример пошагового выполнения

### Шаг 1: Планирование

```json
{
  "status": "plan",
  "step": 1,
  "thought": "нужно найти информацию о СУБД в документации",
  "plan": [
    "поиск по ключевым терминам (PostgreSQL, MySQL, Oracle)",
    "если найдено - извлечь IP-адреса",
    "сформировать структурированный список"
  ]
}
```

### Шаг 2: Действие

```json
{
  "status": "action",
  "step": 2,
  "thought": "выполняю multi_term_exact_search по названиям СУБД",
  "action": {
    "tool": "multi_term_exact_search",
    "input": {
      "terms": ["PostgreSQL", "MySQL", "Oracle", "MongoDB"]
    }
  }
}
```

### Шаг 3: Финальный ответ

```json
{
  "status": "final",
  "step": 3,
  "thought": "найдены данные о 3 СУБД, достаточно для ответа",
  "final_answer": {
    "summary": "В документации упоминаются 3 СУБД: PostgreSQL, MySQL, MongoDB",
    "details": "PostgreSQL используется для основных данных (2 сервера), MySQL - для кэширования (1 сервер), MongoDB - для логов (1 сервер)",
    "data": [
      {"entity": "PostgreSQL", "attribute": "ip", "value": "10.0.1.10"},
      {"entity": "PostgreSQL", "attribute": "ip", "value": "10.0.1.11"},
      {"entity": "MySQL", "attribute": "ip", "value": "10.0.2.20"},
      {"entity": "MongoDB", "attribute": "ip", "value": "10.0.3.30"}
    ],
    "sources": [
      "Приложение_И.md",
      "Инфраструктура_СУБД.md"
    ],
    "confidence": 0.92
  }
}
```

## 🚫 Важные ограничения

**Агент НЕ должен:**
- ❌ Выдумывать данные
- ❌ Пропускать этап `plan` при сложной задаче
- ❌ Давать ответ без проверки через tools

**Агент ОБЯЗАН:**
- ✅ Работать ТОЛЬКО с данными из tools
- ✅ Возвращать только валидный JSON
- ✅ Учитывать историю сообщений (messages)
- ✅ Указывать источники (sources)

## 🔗 См. также

- [`system_prompt.md`](./system_prompt.md) — полный текст промпта
- [`system_prompts.py`](./system_prompts.py) — модуль загрузки промптов
- [`example_analytical_agent.py`](./example_analytical_agent.py) — готовый пример агента
- [`llm_messages.py`](./llm_messages.py) — работа с messages
- [`README.md`](./README.md#-системный-промпт-для-аналитического-агента) — основная документация

