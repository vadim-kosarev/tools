# 📝 Пример миграции: semantic_search

## Текущая реализация (строка)

```python
@tool(args_schema=SemanticSearchInput)
def semantic_search(
    query: str,
    top_k: int = semantic_top_k,
    source: Optional[str] = None,
    section: Optional[str] = None
) -> str:
    """
    Semantic similarity search in the knowledge base using vector embeddings (bge-m3).
    Best for: conceptual questions, 'what is X', 'how does Y work', broad topic search.
    Returns top-K most semantically similar text chunks from ClickHouse.
    Metadata includes: source file, section breadcrumb, line_start for context expansion.
    
    Optional filters:
    - source: limit search to specific file (e.g. 'servers.md')
    - section: limit search to specific section substring
    """
    filter_info = []
    if source:
        filter_info.append(f"file={source}")
    if section:
        filter_info.append(f"section~{section}")
    filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""
    
    logger.debug(f"Tool semantic_search: query='{query[:80]}'{filter_str}, top_k={top_k}")
    rec = _db_request("DB:semantic_search", f"query={query!r}\ntop_k={top_k}{filter_str}")
    docs = vectorstore.clone().similarity_search(query, k=top_k, source=source, section=section)
    result = _fmt_docs(docs)
    if rec:
        rec.set_response(f"Найдено {len(docs)} чанков\n\n{result}")
    logger.info(f"semantic_search '{query[:60]}'{filter_str}: {len(docs)} чанков")
    return result


def _fmt_docs(docs: list[Document]) -> str:
    """Форматирование документов в строку"""
    if not docs:
        return "Ничего не найдено."
    parts = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        parts.append(
            f"[{i}] [{meta['source']}] — {meta['section']} (line {meta['line_start']})"
        )
        if meta.get("table_headers"):
            parts.append(f"Table: {meta['table_headers']}")
        parts.append(doc.page_content)
        parts.append("")
    return "\n".join(parts)
```

**Возвращает:**
```
[1] [file.md] — Section Name (line 123)
Content text here...

[2] [file.md] — Another Section (line 456)
More content...
```

---

## Новая реализация (структурированная)

```python
# Вспомогательная функция
def _doc_to_chunk_result(doc: Document) -> ChunkResult:
    """Конвертер Document → ChunkResult"""
    return ChunkResult(
        content=doc.page_content,
        metadata=ChunkMetadata(
            source=doc.metadata['source'],
            section=doc.metadata['section'],
            chunk_type=doc.metadata['chunk_type'],
            line_start=doc.metadata['line_start'],
            line_end=doc.metadata['line_end'],
            chunk_index=doc.metadata['chunk_index'],
            table_headers=doc.metadata.get('table_headers')
        )
    )


@tool(args_schema=SemanticSearchInput)
def semantic_search(
    query: str,
    top_k: int = semantic_top_k,
    source: Optional[str] = None,
    section: Optional[str] = None
) -> SearchChunksResult:
    """
    Semantic similarity search in the knowledge base using vector embeddings (bge-m3).
    Best for: conceptual questions, 'what is X', 'how does Y work', broad topic search.
    Returns top-K most semantically similar text chunks from ClickHouse.
    
    Optional filters:
    - source: limit search to specific file (e.g. 'servers.md')
    - section: limit search to specific section substring
    
    Returns:
        SearchChunksResult with query, chunks list, and total_found count
    """
    filter_info = []
    if source:
        filter_info.append(f"file={source}")
    if section:
        filter_info.append(f"section~{section}")
    filter_str = f" [{', '.join(filter_info)}]" if filter_info else ""
    
    logger.debug(f"Tool semantic_search: query='{query[:80]}'{filter_str}, top_k={top_k}")
    rec = _db_request("DB:semantic_search", f"query={query!r}\ntop_k={top_k}{filter_str}")
    
    # Поиск
    docs = vectorstore.clone().similarity_search(query, k=top_k, source=source, section=section)
    
    # Конвертация в структуру
    chunks = [_doc_to_chunk_result(doc) for doc in docs]
    
    result = SearchChunksResult(
        query=query,
        chunks=chunks,
        total_found=len(chunks)
    )
    
    # Логирование
    if rec:
        rec.set_response(f"Найдено {len(chunks)} чанков\n\n{result.model_dump_json(indent=2)}")
    logger.info(f"semantic_search '{query[:60]}'{filter_str}: {len(chunks)} чанков")
    
    return result
```

**Возвращает JSON:**
```json
{
  "query": "PostgreSQL configuration",
  "chunks": [
    {
      "content": "PostgreSQL can be configured via postgresql.conf file...",
      "metadata": {
        "source": "database.md",
        "section": "Database Configuration > PostgreSQL",
        "chunk_type": "",
        "line_start": 123,
        "line_end": 125,
        "chunk_index": 42,
        "table_headers": null
      }
    },
    {
      "content": "The main settings include max_connections, shared_buffers...",
      "metadata": {
        "source": "database.md",
        "section": "Performance Tuning",
        "chunk_type": "",
        "line_start": 456,
        "line_end": 460,
        "chunk_index": 89,
        "table_headers": null
      }
    }
  ],
  "total_found": 2
}
```

---

## Что изменилось

### 1. Возвращаемый тип
```python
# Было
def semantic_search(...) -> str:

# Стало
def semantic_search(...) -> SearchChunksResult:
```

### 2. Форматирование
```python
# Было: ручное форматирование в строку
result = _fmt_docs(docs)
return result

# Стало: структурированный объект
chunks = [_doc_to_chunk_result(doc) for doc in docs]
return SearchChunksResult(query=query, chunks=chunks, total_found=len(chunks))
```

### 3. Логирование
```python
# Было
rec.set_response(f"Найдено {len(docs)} чанков\n\n{result}")

# Стало: JSON для логов
rec.set_response(f"Найдено {len(chunks)} чанков\n\n{result.model_dump_json(indent=2)}")
```

---

## Преимущества нового подхода

### 1. Type Safety
```python
# Статическая проверка типов
result: SearchChunksResult = semantic_search("query")
chunks: list[ChunkResult] = result.chunks
first_chunk: ChunkResult = chunks[0]
source: str = first_chunk.metadata.source  # IDE знает тип!
```

### 2. Программная обработка
```python
# Легко фильтровать и обрабатывать
result = semantic_search("PostgreSQL")

# Фильтр по типу чанка
table_chunks = [c for c in result.chunks if c.metadata.chunk_type == "table_row"]

# Группировка по файлам
from collections import defaultdict
by_file = defaultdict(list)
for chunk in result.chunks:
    by_file[chunk.metadata.source].append(chunk)

# Сортировка
sorted_chunks = sorted(result.chunks, key=lambda c: c.metadata.line_start)
```

### 3. Автоматическая сериализация
```python
# В JSON
json_str = result.model_dump_json(indent=2)

# В dict
data_dict = result.model_dump()

# Из JSON обратно
result = SearchChunksResult.model_validate_json(json_str)
```

### 4. Валидация
```python
# Pydantic автоматически проверяет данные
try:
    chunk = ChunkResult(
        content="text",
        metadata={"source": "file.md", ...}  # ошибка! нужен ChunkMetadata
    )
except ValidationError as e:
    print(e)  # чёткое описание ошибки
```

---

## Обратная совместимость

Если нужна строка (для старого кода):

```python
def format_chunks_result(result: SearchChunksResult) -> str:
    """Конвертер SearchChunksResult → str для обратной совместимости"""
    if not result.chunks:
        return "Ничего не найдено."
    
    parts = []
    for i, chunk in enumerate(result.chunks, 1):
        meta = chunk.metadata
        parts.append(
            f"[{i}] [{meta.source}] — {meta.section} (line {meta.line_start})"
        )
        if meta.table_headers:
            parts.append(f"Table: {meta.table_headers}")
        parts.append(chunk.content)
        parts.append("")
    
    return "\n".join(parts)


# Использование
result = semantic_search("query")
text = format_chunks_result(result)  # старый формат
```

---

## Тестирование

### Старые тесты (строка)
```python
def test_semantic_search_old():
    result = semantic_search("PostgreSQL")
    assert isinstance(result, str)
    assert "[1]" in result
    assert "PostgreSQL" in result
```

### Новые тесты (структура)
```python
def test_semantic_search_new():
    result = semantic_search("PostgreSQL")
    
    # Проверка типа
    assert isinstance(result, SearchChunksResult)
    assert result.query == "PostgreSQL"
    
    # Проверка структуры
    assert len(result.chunks) > 0
    assert result.total_found == len(result.chunks)
    
    # Проверка первого чанка
    first = result.chunks[0]
    assert isinstance(first, ChunkResult)
    assert isinstance(first.metadata, ChunkMetadata)
    assert first.metadata.source.endswith(".md")
    
    # Проверка JSON сериализации
    json_str = result.model_dump_json()
    assert "query" in json_str
    assert "chunks" in json_str
    
    # Проверка десериализации
    restored = SearchChunksResult.model_validate_json(json_str)
    assert restored.query == result.query
    assert len(restored.chunks) == len(result.chunks)
```

---

## Масштаб работы

### Для одного инструмента:
- ~30 минут времени
- 20-30 строк кода изменений
- 1 новая helper функция

### Для всех 14 инструментов:
- ~7 часов работы
- ~400 строк кода изменений
- 14 helper функций
- Обновление всех тестов
- Обновление документации

---

## Рекомендация

**Начать с миграции основных инструментов:**
1. `semantic_search` ✅
2. `exact_search` ✅
3. `find_relevant_sections` ✅
4. `multi_term_exact_search` ✅
5. `find_sections_by_term` ✅

Затем оценить результаты и продолжить с остальными.

---

*Пример готов к применению*  
*Дата: 2026-04-26*

