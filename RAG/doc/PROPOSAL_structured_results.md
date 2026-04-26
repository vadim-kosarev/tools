# 🔄 ПРЕДЛОЖЕНИЕ: Переход на структурированные результаты

## Дата: 2026-04-26

---

## 📋 ПРОБЛЕМА

**Текущее состояние:** Все инструменты возвращают `str` - отформатированный текст

```python
@tool
def semantic_search(...) -> str:
    return "[1] [file.md] — Section\nContent text...\n[2]..."
```

**Проблемы:**
- ❌ LLM должен парсить строку для извлечения данных
- ❌ Невозможно программно обработать результат
- ❌ Нет type safety
- ❌ Сложно добавлять новые поля

---

## ✅ РЕШЕНИЕ

**Предложение:** Возвращать **структурированные Pydantic объекты**

```python
@tool
def semantic_search(...) -> SearchChunksResult:
    return SearchChunksResult(
        query="PostgreSQL",
        chunks=[
            ChunkResult(
                content="Content text...",
                metadata=ChunkMetadata(
                    source="file.md",
                    section="Section Name",
                    line_start=123,
                    ...
                )
            )
        ],
        total_found=10
    )
```

**Преимущества:**
- ✅ Type safety - статическая проверка типов
- ✅ Автоматическая сериализация в JSON
- ✅ LLM получает структуру, не нужен парсинг
- ✅ Легко расширять (добавить поля)
- ✅ Программная обработка результатов

---

## 🎯 СОЗДАННЫЕ PYDANTIC МОДЕЛИ

### Базовые модели:

#### 1. `ChunkMetadata`
```python
class ChunkMetadata(BaseModel):
    source: str
    section: str
    chunk_type: str
    line_start: int
    line_end: int
    chunk_index: int
    table_headers: Optional[str] = None
```

#### 2. `ChunkResult`
```python
class ChunkResult(BaseModel):
    content: str
    metadata: ChunkMetadata
```

#### 3. `ScoredChunkResult`
```python
class ScoredChunkResult(BaseModel):
    content: str
    metadata: ChunkMetadata
    score: float  # distance или match_count
```

#### 4. `SectionInfo`
```python
class SectionInfo(BaseModel):
    source: str
    section: str
    match_count: int
    match_type: Optional[str] = None  # "NAME" или "CONTENT"
```

### Результаты поиска:

#### 5. `SearchChunksResult`
```python
class SearchChunksResult(BaseModel):
    query: str
    chunks: list[ChunkResult]
    total_found: int
```

#### 6. `SearchSectionsResult`
```python
class SearchSectionsResult(BaseModel):
    query: str
    sections: list[SectionInfo]
    total_found: int
    returned_count: int  # с учётом limit
```

#### 7. `MultiTermSearchResult`
```python
class MultiTermSearchResult(BaseModel):
    terms: list[str]
    chunks_by_coverage: dict[int, list[ChunkResult]]
    total_chunks: int
    max_coverage: int
```

### Специализированные:

#### 8. `RegexMatch` + `RegexSearchResult`
```python
class RegexMatch(BaseModel):
    file: str
    line_number: int
    matched_text: str
    context_before: list[str]
    matched_line: str
    context_after: list[str]

class RegexSearchResult(BaseModel):
    pattern: str
    matches: list[RegexMatch]
    total_matches: int
```

#### 9. `TableRow` + `TableResult`
```python
class TableRow(BaseModel):
    source: str
    section: str
    line_start: int
    columns: dict[str, str]

class TableResult(BaseModel):
    section_query: str
    rows: list[TableRow]
    total_rows: int
```

#### 10. `SectionContent`
```python
class SectionContent(BaseModel):
    source: str
    section: str
    line_start: int
    line_end: int
    content: str
```

### Навигационные:

#### 11. `SectionTreeNode` + `SectionsTree`
```python
class SectionsTree(BaseModel):
    source: Optional[str]
    sections: list[SectionTreeNode]
    total_sections: int
```

#### 12. `NeighborChunksResult`
```python
class NeighborChunksResult(BaseModel):
    anchor_line: int
    chunks_before: list[ChunkResult]
    chunks_after: list[ChunkResult]
```

#### 13. `SourceInfo` + `SourcesList`
```python
class SourcesList(BaseModel):
    sources: list[SourceInfo]
    total_sources: int
    total_chunks: int
```

---

## 📝 ПРИМЕР ПЕРЕДЕЛКИ

### ДО (текущий код):

```python
@tool(args_schema=SemanticSearchInput)
def semantic_search(query: str, top_k: int = 10, ...) -> str:
    """Semantic search..."""
    docs = vectorstore.clone().similarity_search(query, k=top_k)
    
    # Форматирование в строку
    result = []
    for i, doc in enumerate(docs, 1):
        result.append(f"[{i}] [{doc.metadata['source']}] — {doc.metadata['section']} (line {doc.metadata['line_start']})")
        result.append(doc.page_content)
        result.append("")
    
    return "\n".join(result)
```

### ПОСЛЕ (предложение):

```python
@tool(args_schema=SemanticSearchInput)
def semantic_search(query: str, top_k: int = 10, ...) -> SearchChunksResult:
    """Semantic search..."""
    docs = vectorstore.clone().similarity_search(query, k=top_k)
    
    # Преобразование в структуру
    chunks = [
        ChunkResult(
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
        for doc in docs
    ]
    
    return SearchChunksResult(
        query=query,
        chunks=chunks,
        total_found=len(chunks)
    )
```

---

## 🔄 ПЛАН МИГРАЦИИ

### Этап 1: Вспомогательные функции (helper)
```python
def doc_to_chunk_result(doc: Document) -> ChunkResult:
    """Конвертер Document → ChunkResult"""
    return ChunkResult(
        content=doc.page_content,
        metadata=ChunkMetadata(**doc.metadata)
    )

def docs_to_chunks(docs: list[Document]) -> list[ChunkResult]:
    """Конвертер списка документов"""
    return [doc_to_chunk_result(doc) for doc in docs]
```

### Этап 2: Переделка инструментов (приоритет)

**Высокий приоритет** (основные поисковые):
1. ✅ `semantic_search` → `SearchChunksResult`
2. ✅ `exact_search` → `SearchChunksResult`
3. ✅ `multi_term_exact_search` → `MultiTermSearchResult`
4. ✅ `find_sections_by_term` → `SearchSectionsResult`
5. ✅ `find_relevant_sections` → `SearchSectionsResult`

**Средний приоритет** (специализированные):
6. ✅ `exact_search_in_file` → `SearchChunksResult`
7. ✅ `exact_search_in_file_section` → `SearchChunksResult`
8. ✅ `regex_search` → `RegexSearchResult`
9. ✅ `read_table` → `TableResult`
10. ✅ `get_section_content` → `SectionContent`

**Низкий приоритет** (навигация):
11. ✅ `list_sections` → `SectionsTree`
12. ✅ `get_neighbor_chunks` → `NeighborChunksResult`
13. ✅ `list_sources` → `SourcesList`
14. ✅ `list_all_sections` → `SearchSectionsResult`

### Этап 3: Тестирование
- Обновить тесты для проверки структурированных результатов
- Проверить JSON сериализацию
- Протестировать с реальным агентом

### Этап 4: Документация
- Обновить README с новыми типами возврата
- Добавить примеры JSON-выводов
- Обновить docstrings инструментов

---

## 🎯 ПРЕИМУЩЕСТВА ДЛЯ LLM

### Текущий формат (строка):
```
[1] [file.md] — Section Name (line 123)
Content text here...

[2] [file.md] — Another Section (line 456)
More content...
```

**LLM получает:** неструктурированный текст, нужно парсить

### Новый формат (JSON):
```json
{
  "query": "PostgreSQL",
  "chunks": [
    {
      "content": "Content text here...",
      "metadata": {
        "source": "file.md",
        "section": "Section Name",
        "line_start": 123,
        "line_end": 125,
        "chunk_type": "",
        "chunk_index": 42
      }
    },
    {
      "content": "More content...",
      "metadata": {
        "source": "file.md",
        "section": "Another Section",
        "line_start": 456,
        "line_end": 458,
        "chunk_type": "table_row",
        "chunk_index": 89
      }
    }
  ],
  "total_found": 2
}
```

**LLM получает:** чистую структуру, готовую к обработке

---

## 💡 ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ

### 1. Композиция результатов
```python
# Можно комбинировать результаты разных инструментов
all_chunks = semantic_result.chunks + exact_result.chunks
```

### 2. Фильтрация
```python
# Программная фильтрация по метаданным
table_chunks = [c for c in result.chunks if c.metadata.chunk_type == "table_row"]
```

### 3. Расширение
```python
# Легко добавить новые поля без breaking changes
class ChunkMetadata(BaseModel):
    # ...existing fields...
    relevance_score: Optional[float] = None  # новое поле
```

### 4. Валидация
```python
# Pydantic автоматически валидирует данные
chunk = ChunkResult(content="...", metadata={...})  # ошибка если некорректно
```

---

## ⚠️ BREAKING CHANGES

**Внимание:** Это BREAKING CHANGE!

Существующий код, который парсит строковые результаты, перестанет работать.

### Что сломается:
- Код который ожидает `str` от инструментов
- Логирование текстовых результатов
- Тесты проверяющие строковый формат

### Как смягчить:
1. Добавить `.model_dump_json()` для обратной совместимости
2. Создать wrapper который конвертирует в строку при необходимости
3. Поэтапная миграция (сначала новые инструменты, потом старые)

---

## ❓ ВОПРОСЫ ДЛЯ ОБСУЖДЕНИЯ

1. **Начинать миграцию или нет?**
   - За: type safety, структурированность, расширяемость
   - Против: breaking changes, нужно время на переделку

2. **Поэтапная миграция или единовременно?**
   - Поэтапно: меньше риск, постепенная адаптация
   - Единовременно: всё сразу, консистентность

3. **Обратная совместимость?**
   - Добавить опцию `return_format: "json" | "string"`?
   - Создать wrapper `to_string()` для старого кода?

4. **Тестирование?**
   - Нужны новые тесты для структурированных результатов
   - Как тестировать JSON-сериализацию?

---

## ✅ СТАТУС

**Готово:**
- ✅ Pydantic модели созданы (14 классов)
- ✅ Документация предложения написана
- ✅ Примеры миграции подготовлены

**Ожидает решения:**
- ⏳ Начинать миграцию?
- ⏳ Какой формат выбрать (поэтапный/единовременный)?
- ⏳ Нужна ли обратная совместимость?

---

*Дата: 2026-04-26*  
*Статус: 🔄 ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ*

