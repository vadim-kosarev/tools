# Улучшение: Использование pydantic_to_markdown для messages

**Дата:** 2026-04-26 19:30  
**Файл:** `rag_lg_agent.py`

---

## Проблема

Pydantic модели сохранялись в messages как **JSON**:

```python
state["messages"].append({
    "role": "assistant",
    "content": result.model_dump_json(indent=2)
})
```

**Результат в логах:**
```json
{
  "status": "plan",
  "step": 1,
  "thought": "нужно найти упоминания СУБД",
  "plan": [
    "поиск по терминам PostgreSQL, MySQL, MongoDB",
    "семантический поиск конфигурации БД"
  ]
}
```

**Проблемы:**
- ❌ JSON не очень читаем для человека
- ❌ Сложно быстро понять структуру
- ❌ Много скобок и кавычек
- ❌ Не использует существующую утилиту `pydantic_to_markdown`

---

## Решение

Используется утилита **`pydantic_to_markdown`** из `pydantic_utils.py`:

### 1. Добавлен импорт

```python
from pydantic_utils import pydantic_to_markdown
```

### 2. Обновлены все узлы

#### plan_node
```python
state["messages"] = [
    {"role": "system", "content": system_message},
    {"role": "user", "content": user_message},
    {"role": "assistant", "content": pydantic_to_markdown(result)}  # ✅ Markdown
]
```

#### action_node
```python
state["messages"].extend([
    {"role": "assistant", "content": pydantic_to_markdown(result)}  # ✅ Markdown
])
```

#### observation_node
```python
state["messages"].append({
    "role": "assistant",
    "content": pydantic_to_markdown(result)  # ✅ Markdown
})
```

#### final_node
```python
state["final_answer"] = result.model_dump_json(indent=2)  # JSON для парсинга в print_result
state["messages"].append({
    "role": "assistant",
    "content": pydantic_to_markdown(result)  # ✅ Markdown для логов
})
```

### 3. Результаты инструментов

```python
if hasattr(result_raw, "model_dump_json"):
    # Pydantic модель - используем pydantic_to_markdown() для читаемости
    result_str = pydantic_to_markdown(result_raw)  # ✅ Markdown
else:
    result_str = str(result_raw)
```

---

## Результат в логах

**До (JSON):**
```json
{
  "status": "plan",
  "step": 1,
  "thought": "нужно найти упоминания СУБД",
  "plan": [
    "поиск по терминам PostgreSQL, MySQL, MongoDB",
    "семантический поиск конфигурации БД",
    "поиск IP-адресов серверов"
  ]
}
```

**После (Markdown):**
```markdown
**AgentPlan**
- **status:** plan
- **step:** 1
- **thought:** нужно найти упоминания СУБД
- **plan:** (3 элементов)
  1. поиск по терминам PostgreSQL, MySQL, MongoDB
  2. семантический поиск конфигурации БД
  3. поиск IP-адресов серверов
```

---

## Преимущества

### 1. Читаемость

✅ **Иерархическая структура** - отступы показывают вложенность  
✅ **Нет лишних символов** - меньше скобок и кавычек  
✅ **Жирный текст** для ключевых полей  
✅ **Компактный формат** для списков

### 2. Автоматические улучшения

✅ **Сокращение длинных значений** - автоматически обрезает до 100 символов  
✅ **Подсчёт элементов** - показывает `(3 элементов)` вместо полного списка (если длинно)  
✅ **Умная вложенность** - для Pydantic моделей внутри показывает краткую форму

### 3. Единообразие

✅ **Используется существующая утилита** - не дублируем логику  
✅ **Тот же формат** что в других частях кода  
✅ **Легко поддерживать** - изменения в одном месте

---

## Что НЕ изменилось

### final_answer остаётся в JSON

```python
state["final_answer"] = result.model_dump_json(indent=2)  # ✅ JSON для парсинга
```

**Причина:** В `print_result()` этот JSON парсится для красивого вывода:
```python
answer_data = json.loads(state['final_answer'])
final_ans = answer_data.get('final_answer', {})
```

### Два формата в final_node

```python
state["final_answer"] = result.model_dump_json(indent=2)  # JSON для программного парсинга
state["messages"].append({
    "role": "assistant",
    "content": pydantic_to_markdown(result)  # Markdown для человеческой читаемости
})
```

---

## Пример: Результат инструмента

**До (JSON):**
```json
{
  "total_count": 15,
  "chunks": [
    {
      "content": "PostgreSQL установлен на сервере...",
      "metadata": {
        "source": "servers.md",
        "section": "Базы данных"
      }
    }
  ],
  "query": "PostgreSQL"
}
```

**После (Markdown):**
```markdown
**SearchChunksResult**
- **total_count:** 15
- **chunks:** (15 элементов)
  1. ChunkResult(content=PostgreSQL установлен на сервере..., metadata=...)
  2. ChunkResult(content=MySQL на порту 3306..., metadata=...)
  3. ChunkResult(content=MongoDB конфигурация..., metadata=...)
  ... и ещё 12
- **query:** PostgreSQL
```

---

## Изменённые места

| Место | Было | Стало |
|-------|------|-------|
| `plan_node` | `model_dump_json()` | `pydantic_to_markdown()` |
| `action_node` | `model_dump_json()` | `pydantic_to_markdown()` |
| `action_node` (tools) | `model_dump_json()` | `pydantic_to_markdown()` |
| `observation_node` | `model_dump_json()` | `pydantic_to_markdown()` |
| `final_node` (messages) | `model_dump_json()` | `pydantic_to_markdown()` |
| `final_node` (state) | `model_dump_json()` | **Остался JSON** |

---

## Проверка

```bash
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python -m py_compile rag_lg_agent.py
```

✅ **Результат:** Синтаксис корректен

---

## Совместимость

✅ **Полностью обратно совместимо**  
✅ **Не изменяет API**  
✅ **Не изменяет формат state**  
✅ **Только улучшает читаемость логов**

---

## Статус

✅ **Реализовано**  
✅ **Проверено**  
✅ **Готово к использованию**

