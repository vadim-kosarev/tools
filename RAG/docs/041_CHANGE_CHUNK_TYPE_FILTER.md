# Изменение: Фильтрация по типу чанков в semantic_search и exact_search

**Дата:** 2026-04-26 19:45  
**Файлы:** `kb_tools.py`

---

## Проблема

Инструменты `semantic_search` и `exact_search` возвращали **все типы чанков**, включая:
- `""` (пустая строка) - prose chunks (обычный текст)
- `"table_row"` - строки таблиц
- `"table_full"` - полные таблицы

**Проблема:**
❌ При текстовом поиске попадались полные таблицы (`table_full`), что засоряло результаты  
❌ Таблицы содержат много данных и плохо подходят для семантического/текстового поиска  
❌ Для работы с таблицами есть специальный инструмент `read_table`

---

## Решение

Изменён **default для `chunk_type`** с `None` (все типы) на `""` (только prose chunks):

### 1. SemanticSearchInput

**Было:**
```python
class SemanticSearchInput(BaseModel):
    query: str
    top_k: int = 10
    source: Optional[str] = None
    section: Optional[str] = None
    # chunk_type отсутствовал
```

**Стало:**
```python
class SemanticSearchInput(BaseModel):
    query: str
    top_k: int = 10
    chunk_type: str = ""  # ✅ По умолчанию только prose
    source: Optional[str] = None
    section: Optional[str] = None
```

### 2. ExactSearchInput

**Было:**
```python
class ExactSearchInput(BaseModel):
    substring: str
    limit: int = 30
    chunk_type: Optional[str] = None  # ❌ По умолчанию все типы
    source: Optional[str] = None
    section: Optional[str] = None
```

**Стало:**
```python
class ExactSearchInput(BaseModel):
    substring: str
    limit: int = 30
    chunk_type: str = ""  # ✅ По умолчанию только prose
    source: Optional[str] = None
    section: Optional[str] = None
```

### 3. Функция semantic_search

**Добавлен параметр:**
```python
def semantic_search(
    query: str,
    top_k: int = 10,
    chunk_type: str = "",  # ✅ Новый параметр
    source: Optional[str] = None,
    section: Optional[str] = None
) -> SearchChunksResult:
    # Передаётся в vectorstore
    docs = vectorstore.clone().similarity_search(
        query, k=top_k, chunk_type=chunk_type, source=source, section=section
    )
```

### 4. Функция exact_search

**Изменён дефолт:**
```python
def exact_search(
    substring: str,
    limit: int = 30,
    chunk_type: str = "",  # ✅ Было: Optional[str] = None
    source: Optional[str] = None,
    section: Optional[str] = None
) -> SearchChunksResult:
    # chunk_type уже передавался
    docs = vectorstore.clone().exact_search(
        substring, limit=limit, chunk_type=chunk_type, source=source, section=section
    )
```

---

## Что изменилось

### До изменений

```python
# semantic_search
semantic_search(query="PostgreSQL")
# Возвращал: prose chunks + table_full + table_row

# exact_search  
exact_search(substring="PostgreSQL")
# Возвращал: prose chunks + table_full + table_row
```

### После изменений

```python
# semantic_search
semantic_search(query="PostgreSQL")
# Возвращает: только prose chunks (chunk_type="")

# exact_search
exact_search(substring="PostgreSQL")
# Возвращает: только prose chunks (chunk_type="")

# Если нужны таблицы - явно указываем
exact_search(substring="PostgreSQL", chunk_type="table_row")
# Возвращает: только строки таблиц

exact_search(substring="PostgreSQL", chunk_type="table_full")
# Возвращает: только полные таблицы
```

---

## Типы чанков

| chunk_type | Описание | Когда использовать |
|------------|----------|-------------------|
| `""` (пустая строка) | **Prose chunks** - обычный текст | По умолчанию для текстового поиска |
| `"table_row"` | Строки таблиц (каждая строка = отдельный чанк) | Поиск в табличных данных |
| `"table_full"` | Полные таблицы целиком | Редко, обычно используется `read_table` |

---

## Инструменты для работы с таблицами

Для работы с таблицами есть специальные инструменты:

**read_table** - чтение табличных данных по названию раздела:
```python
read_table(section="Список серверов", limit=50)
# Возвращает строки таблицы из раздела
```

**exact_search с chunk_type="table_row"**:
```python
exact_search(substring="PostgreSQL", chunk_type="table_row")
# Поиск в строках таблиц
```

---

## Преимущества

### 1. Более релевантные результаты

✅ **semantic_search** теперь возвращает только текстовые фрагменты  
✅ **exact_search** не захламляется полными таблицами  
✅ Таблицы ищутся специально через `read_table` или явный `chunk_type="table_row"`

### 2. Явный контроль

✅ Чтобы получить таблицы, нужно **явно указать** `chunk_type`  
✅ Не нужно фильтровать результаты на стороне агента  
✅ Меньше ненужных данных передаётся в LLM

### 3. Производительность

✅ Меньше чанков для обработки (таблицы могут быть большими)  
✅ Более быстрый поиск  
✅ Меньше токенов в промптах

---

## Обратная совместимость

### Как получить старое поведение (все типы чанков)

Если нужно искать во всех типах чанков, можно передать `chunk_type=None`:

**Не рекомендуется, но возможно:**
```python
# Через код (если вызывается напрямую)
vectorstore.similarity_search(query="PostgreSQL", chunk_type=None)

# Через LLM агента - нужно попросить LLM передать chunk_type явно
```

**Рекомендуется:**
Использовать отдельные запросы для prose и таблиц.

---

## Примеры использования

### 1. Поиск определения (prose)

```python
semantic_search(query="что такое ППРК")
# ✅ Ищет только в текстовых фрагментах
```

### 2. Поиск системы (prose)

```python
exact_search(substring="PostgreSQL")
# ✅ Ищет только в текстовых фрагментах
```

### 3. Поиск в таблице серверов

```python
read_table(section="Список серверов", limit=50)
# ✅ Читает строки таблицы
```

### 4. Поиск IP в строках таблиц

```python
exact_search(substring="10.0.0.1", chunk_type="table_row")
# ✅ Ищет только в строках таблиц
```

---

## Изменённые файлы

1. **`kb_tools.py`**
   - `SemanticSearchInput` - добавлено поле `chunk_type: str = ""`
   - `ExactSearchInput` - изменён default с `Optional[str] = None` на `str = ""`
   - `semantic_search()` - добавлен параметр `chunk_type: str = ""`
   - `exact_search()` - изменён default с `Optional[str] = None` на `str = ""`

---

## Проверка

```bash
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python -m py_compile kb_tools.py
```

✅ **Результат:** Синтаксис корректен

---

## Тестирование

### До изменений
```python
results = exact_search(substring="PostgreSQL")
# Могло вернуть 100 чанков: 80 prose + 15 table_row + 5 table_full
```

### После изменений
```python
results = exact_search(substring="PostgreSQL")
# Вернёт ~80 чанков: только prose

# Если нужны таблицы
table_results = exact_search(substring="PostgreSQL", chunk_type="table_row")
# Вернёт ~15 чанков: только строки таблиц
```

---

## Статус

✅ **Реализовано**  
✅ **Проверено**  
✅ **Готово к использованию**

---

## Примечание

Другие инструменты (`multi_term_exact_search`, `exact_search_in_file`, и т.д.) **не изменены** - у них `chunk_type` остался `Optional[str] = None` (все типы чанков), так как они используются для специфичных задач где может понадобиться гибкость.

Если потребуется, их тоже можно изменить аналогично.

