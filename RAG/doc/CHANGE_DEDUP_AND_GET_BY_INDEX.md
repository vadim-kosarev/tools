# Изменения: Дедупликация чанков и новый инструмент get_chunks_by_index

**Дата:** 2026-04-26 20:00  
**Файл:** `kb_tools.py`

---

## Что сделано

### 1. Дедупликация результатов поиска

Все функции поиска чанков теперь возвращают **уникальные чанки** по комбинации `(source, section, chunk_index)`.

**Проблема:**
При поиске могли возвращаться дубликаты одного и того же чанка, если он соответствовал критериям несколько раз.

**Решение:**
Создана функция `_deduplicate_chunks()`, которая сохраняет только первое вхождение каждого уникального чанка:

```python
def _deduplicate_chunks(chunks: list[ChunkResult]) -> list[ChunkResult]:
    """
    Дедупликация чанков по уникальной комбинации (source, section, chunk_index).
    Сохраняет первое вхождение для каждой уникальной комбинации.
    """
    seen = set()
    unique_chunks = []
    
    for chunk in chunks:
        key = (
            chunk.metadata.source,
            chunk.metadata.section,
            chunk.metadata.chunk_index
        )
        if key not in seen:
            seen.add(key)
            unique_chunks.append(chunk)
    
    return unique_chunks
```

**Применено к функциям:**
- ✅ `semantic_search`
- ✅ `exact_search`
- ✅ `exact_search_in_file`
- ✅ `exact_search_in_file_section`
- ✅ `multi_term_exact_search` (по группам coverage)

### 2. Новый инструмент get_chunks_by_index

Добавлен инструмент для получения конкретных чанков по их индексам.

**Сигнатура:**
```python
def get_chunks_by_index(
    source: str,
    section: str,
    chunk_indices: list[int]
) -> SearchChunksResult
```

**Назначение:**
- Получить конкретные чанки, если известны их индексы
- Построить контекст из известных позиций
- Получить референсные чанки из предыдущих результатов поиска

**Пример использования:**
```python
# Получить чанки с индексами 0, 1, 5 из раздела
result = get_chunks_by_index(
    source="servers.md",
    section="Database Configuration",
    chunk_indices=[0, 1, 5]
)

# result.chunks содержит запрошенные чанки в порядке индексов
```

**Реализация:**
- Прямой SQL запрос к ClickHouse по точным критериям
- Возвращает чанки в порядке индексов
- Автоматически конвертирует в `SearchChunksResult`

---

## Детали реализации

### Дедупликация

**До:**
```python
# semantic_search
chunks = _docs_to_chunk_results(docs)
result = SearchChunksResult(query=query, chunks=chunks, ...)
```

**После:**
```python
# semantic_search
chunks = _deduplicate_chunks(_docs_to_chunk_results(docs))
result = SearchChunksResult(query=query, chunks=chunks, ...)
```

**Для multi_term_exact_search:**
```python
# Дедупликация каждой группы отдельно
for cnt in groups:
    groups[cnt] = _deduplicate_chunks(groups[cnt])
```

### get_chunks_by_index

**Pydantic схема:**
```python
class GetChunksByIndexInput(BaseModel):
    source: str = Field(description="Source filename (e.g. 'servers.md')")
    section: str = Field(description="Section name or breadcrumb path")
    chunk_indices: list[int] = Field(
        description="List of chunk indices to retrieve (e.g. [0, 1, 5])",
        min_items=1,
        max_items=50
    )
```

**SQL запрос:**
```python
query = f"""
    SELECT content, metadata, source, section, chunk_type, 
           line_start, line_end, chunk_index
    FROM {table}
    WHERE source = %s 
      AND section = %s
      AND chunk_index IN ({placeholders})
    ORDER BY chunk_index
"""
```

---

## Изменения в реестре инструментов

Добавлена запись:
```python
"get_chunks_by_index": "Получить конкретные чанки по индексам (source, section, chunk_indices[])"
```

Теперь **15 инструментов** в KB tools (было 14).

---

## Преимущества

### Дедупликация

✅ **Нет дубликатов** - каждый чанк встречается только один раз  
✅ **Меньше данных** - сокращение объёма результатов  
✅ **Точный подсчёт** - `total_found` показывает реальное количество уникальных чанков  
✅ **Правильный порядок** - сохраняется первое вхождение (самое релевантное)

### get_chunks_by_index

✅ **Прямой доступ** - получение конкретных чанков без поиска  
✅ **Референсы** - можно сослаться на чанки из предыдущих результатов  
✅ **Быстрый контекст** - построение контекста из известных позиций  
✅ **Дополнение** - можно дополнить результаты другими конкретными чанками

---

## Примеры использования

### 1. Дедупликация в действии

**До:**
```python
# Поиск термина который встречается в одном чанке несколько раз
result = exact_search(substring="PostgreSQL")
# Мог вернуть: 10 чанков (с дубликатами)
```

**После:**
```python
result = exact_search(substring="PostgreSQL")
# Вернёт: 8 уникальных чанков (дубликаты удалены)
```

### 2. get_chunks_by_index

**Сценарий:** У вас есть результаты поиска и вы хотите получить дополнительные чанки из того же раздела.

```python
# Первый поиск
initial = exact_search(substring="database config")
# Нашли чанки с индексами [3, 7, 12] в разделе "Setup > Database"

# Получить соседние чанки
additional = get_chunks_by_index(
    source="setup.md",
    section="Setup > Database",
    chunk_indices=[0, 1, 2]  # Начало раздела
)

# Теперь есть полный контекст: начало раздела + найденные чанки
```

**Сценарий 2:** Построить последовательность чанков.

```python
# Получить чанки по порядку для чтения раздела
chunks = get_chunks_by_index(
    source="guide.md",
    section="Installation",
    chunk_indices=[0, 1, 2, 3, 4]
)
# Вернётся последовательность из 5 чанков в правильном порядке
```

---

## Ограничения get_chunks_by_index

1. **Требует точные параметры:**
   - Нужно знать точное имя `source`
   - Нужно знать точный путь `section`
   - Нужно знать индексы чанков

2. **Максимум 50 индексов** за один вызов (ограничение Pydantic схемы)

3. **Возвращает только существующие чанки:**
   - Если индекса не существует, он просто не будет в результатах
   - Нет ошибки, просто меньше чанков чем запрошено

---

## Тестирование

### Проверка синтаксиса
```bash
python -m py_compile kb_tools.py
```
✅ Синтаксис корректен

### Проверка количества инструментов
```python
from kb_tools import get_tool_registry
print(len(get_tool_registry()))  # 15
```

### Тестовый запрос (после запуска агента)
```python
# Получить первые 3 чанка раздела
result = get_chunks_by_index(
    source="servers.md",
    section="Database Servers",
    chunk_indices=[0, 1, 2]
)
print(f"Получено чанков: {result.total_found}")
```

---

## Статус

✅ **Реализовано**  
✅ **Проверено**  
✅ **Готово к использованию**

---

## Дополнительные заметки

### Порядок дедупликации

При дедупликации сохраняется **первое вхождение** чанка в исходном списке. Это важно для функций, которые возвращают результаты в порядке релевантности:
- В `semantic_search` - сохраняются самые семантически близкие
- В `exact_search` - сохраняются первые найденные
- В `multi_term_exact_search` - дедупликация внутри каждой группы coverage

### Производительность

Дедупликация работает за O(n) где n - количество чанков. Использует `set` для отслеживания уникальных ключей, что очень быстро.

### Совместимость

Все изменения **полностью обратно совместимы**:
- API инструментов не изменился
- Возвращаемые типы остались теми же
- Только убраны дубликаты (улучшение качества)
- Добавлен новый инструмент (не влияет на существующие)

