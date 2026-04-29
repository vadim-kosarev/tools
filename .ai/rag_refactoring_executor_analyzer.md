# Рефакторинг разделения ответственности: tool_executor vs analyzer

**Дата:** 2026-04-29  
**Файл:** `RAG/rag_lg_agent.py`

## Суть изменения

Изменена логика разделения ответственности между `tool_executor_node` и `analyzer_node`:

### Было:
- **tool_executor** — выполняет инструменты + сразу добавляет результаты в `state["context"]`
- **analyzer** — читает из `state["context"]` и создает текстовую сводку

### Стало:
- **tool_executor** — **только выполняет инструменты**, сохраняет результаты в `history`
- **analyzer** — извлекает результаты из `history`, **анализирует и решает что добавить в `context`**

---

## Измененные функции

### 1. `tool_executor_node`

**Было:**
```python
state["context"] = context
state["history"] = history
```

**Стало:**
```python
# НЕ сохраняем context в state - это сделает analyzer после анализа
state["history"] = history
```

**Docstring:**
```python
"""Выполняет инструменты из tool_instructions и сохраняет результаты в history. Без LLM."""
```

---

### 2. `analyzer_node`

**Было:**
```python
context = state.get("context", [])  # читал готовый context
```

**Стало:**
```python
# Извлекаем результаты tool execution из history
history = list(state.get("history", []))
tool_executions = [h for h in history if h.get("type") == "tool_execution"]

# Собираем context из tool execution записей
context = []
for te in tool_executions:
    context.append({
        "tool": te["tool"],
        "input": te["args"],
        "result": te["result"],
        "result_md": te["result_md"]
    })

# ... анализ ...

# ТЕПЕРЬ сохраняем проанализированный context в state
state["context"] = context
state["history"] = history
```

**Docstring:**
```python
"""Анализирует результаты инструментов из history и добавляет их в context. Без LLM."""
```

---

### 3. `HistoryToolExecution`

**Добавлено поле `result`:**
```python
class HistoryToolExecution(BaseModel):
    type: Literal["tool_execution"] = "tool_execution"
    call_id: str
    tool: str
    args: dict[str, Any]
    result: Any  # Оригинальный сериализованный результат
    result_md: str  # dict_to_markdown(tool_result)
```

**Причина:** `final_node` требует доступ к оригинальному `result` для формирования JSON.

---

## Документация (header)

**Обновлены описания узлов:**
```
tool_executor - выполняет инструменты, сохраняет результаты только в history, без LLM
analyzer      - анализирует результаты из history и добавляет в context после фильтрации, без LLM
```

**Обновлено описание AgentState:**
```
context : list[dict] — проанализированные результаты тулов (от analyzer), содержат result и result_md
```

---

## Преимущества

1. **Четкое разделение ответственности:**
   - `tool_executor` = чистое выполнение инструментов
   - `analyzer` = интеллектуальный анализ и фильтрация результатов

2. **Гибкость для будущего:**
   - В `analyzer` легко добавить логику фильтрации
   - Можно решать, что добавлять в context, а что нет
   - Возможность приоритизации/ранжирования результатов

3. **TODO для будущего:**
```python
# TODO: здесь в будущем можно добавить логику фильтрации/анализа того, что добавлять
```

---

## Баг-фикс: KeyError 'result' в final_node

**Дата:** 2026-04-29 20:18

### Проблема:
```python
KeyError: 'result'
File "C:\dev\github.com\vadim-kosarev\tools.0\RAG\rag_lg_agent.py", line 815, in final_node
```

### Причина:
После рефакторинга `analyzer_node` восстанавливал из `history` только 3 поля:
- `tool`
- `input` (из `te["args"]`)
- `result_md`

Но `final_node` требовал доступ к `result` для формирования JSON.

### Решение:
1. ✅ Добавлено поле `result: Any` в `HistoryToolExecution`
2. ✅ Обновлены все места создания `HistoryToolExecution` (5 мест):
   - Успешное выполнение
   - Ошибка валидации параметров
   - Инструмент не найден
   - Ошибка выполнения
   - Автоматическое расширение контекста
3. ✅ Обновлён `analyzer_node` - восстанавливает и `result`, и `result_md`

### Проверка:
```bash
python -u rag_lg_agent.py "Что такое RAG?"
# ✅ Работает без ошибок
```

---

## Проверка

✅ Код компилируется без ошибок  
✅ Docstring'и обновлены  
✅ Документация в header обновлена  
✅ Логика работы не нарушена (результаты всё так же попадают в context, но через analyzer)  
✅ Исправлен баг KeyError 'result' в final_node

---

## Следующие шаги (опционально)

1. Реализовать интеллектуальную фильтрацию в `analyzer`:
   - Дедупликация похожих результатов
   - Ранжирование по релевантности
   - Ограничение по размеру (токенам)
   - Удаление ошибочных/пустых результатов

2. Добавить метрики:
   - Сколько результатов отфильтровано
   - Причины фильтрации
   - Статистика по типам инструментов

3. Логирование решений analyzer:
   - Почему результат добавлен/исключен
   - Confidence score для каждого результата

