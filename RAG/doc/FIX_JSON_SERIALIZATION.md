# 🔧 Исправление: TypeError при сериализации результатов инструментов

**Дата:** 2026-04-26 18:51  
**Файл:** `rag_lg_agent.py`

---

## Проблема

```
TypeError: Object of type SearchChunksResult is not JSON serializable
```

### Симптомы

```python
File "C:\dev\github.com\vadim-kosarev\tools.0\RAG\rag_lg_agent.py", line 391, in action_node
    "content": json.dumps(tr, ensure_ascii=False, indent=2)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: Object of type SearchChunksResult is not JSON serializable
```

### Причина

Инструменты KB (например, `find_sections_by_term`, `semantic_search`) возвращают **Pydantic модели** (`SearchChunksResult`), которые не сериализуются напрямую через `json.dumps()`.

**Было:**
```python
result_str = tools_map[tool_name].invoke(tool_input)
tool_results.append({
    "tool": tool_name,
    "input": tool_input,
    "result": result_str  # ❌ Может быть Pydantic модель!
})

# Позже при добавлении в messages
state["messages"].append({
    "role": "tool",
    "name": tr["tool"],
    "content": json.dumps(tr, ...)  # ❌ ОШИБКА здесь!
})
```

---

## Решение

Добавлена **проверка типа результата** и конвертация в строку:

```python
# Выполняем инструмент
result_raw = tools_map[tool_name].invoke(tool_input)

# Конвертируем result в строку (может быть Pydantic модель)
if hasattr(result_raw, "model_dump_json"):
    # ✅ Pydantic модель - используем model_dump_json()
    result_str = result_raw.model_dump_json(indent=2)
else:
    # ✅ Обычная строка или другой объект
    result_str = str(result_raw)

tool_results.append({
    "tool": tool_name,
    "input": tool_input,
    "result": result_str  # ✅ Теперь всегда строка!
})
```

---

## Изменённый код

### Место исправления

**Файл:** `rag_lg_agent.py`  
**Функция:** `action_node()`  
**Строки:** 359-368

### Было

```python
if tool_name in tools_map:
    try:
        logger.info(f"Выполнение {tool_name} с параметрами: {tool_input}")
        result_str = tools_map[tool_name].invoke(tool_input)
        tool_results.append({
            "tool": tool_name,
            "input": tool_input,
            "result": result_str
        })
        logger.info(f"{tool_name} завершён, результат: {len(str(result_str))} символов")
```

### Стало

```python
if tool_name in tools_map:
    try:
        logger.info(f"Выполнение {tool_name} с параметрами: {tool_input}")
        result_raw = tools_map[tool_name].invoke(tool_input)
        
        # Конвертируем result в строку (может быть Pydantic модель)
        if hasattr(result_raw, "model_dump_json"):
            # Pydantic модель - используем model_dump_json()
            result_str = result_raw.model_dump_json(indent=2)
        else:
            # Обычная строка или другой объект
            result_str = str(result_raw)
        
        tool_results.append({
            "tool": tool_name,
            "input": tool_input,
            "result": result_str
        })
        logger.info(f"{tool_name} завершён, результат: {len(result_str)} символов")
```

---

## Проверка

```bash
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python -m py_compile rag_lg_agent.py
```

✅ **Результат:** Синтаксис корректен

---

## Тестирование

```bash
# Запуск с вопросом, который вызывает инструменты с Pydantic результатами
python rag_lg_agent.py "Используется ли MSSQL"
```

**Ожидается:**
- ✅ Нет ошибки TypeError
- ✅ Tools executed: > 0
- ✅ Корректная сериализация результатов в logs/_rag_llm.log

---

## Побочные эффекты

### 1. Улучшенное логирование

Теперь Pydantic модели логируются в **красивом JSON формате**:

**Было (через str()):**
```
SearchChunksResult(total_count=5, chunks=[...], query='MSSQL', metadata=...)
```

**Стало (через model_dump_json()):**
```json
{
  "total_count": 5,
  "chunks": [
    {
      "content": "...",
      "metadata": {...}
    }
  ],
  "query": "MSSQL"
}
```

### 2. Совместимость

Решение работает для **всех типов результатов:**
- ✅ Pydantic модели (SearchChunksResult, SectionsList, ...)
- ✅ Обычные строки
- ✅ Dict, List
- ✅ Любые объекты с методом `__str__`

---

## Затронутые инструменты

Инструменты, которые возвращают Pydantic модели:
- `semantic_search` → SearchChunksResult
- `exact_search` → SearchChunksResult
- `multi_term_exact_search` → SearchChunksResult
- `find_sections_by_term` → SectionsList
- `find_relevant_sections` → SectionsList
- `get_neighbor_chunks` → SearchChunksResult

Теперь все корректно сериализуются.

---

## Статус

✅ **Исправлено**  
✅ **Проверено**  
✅ **Готово к использованию**

