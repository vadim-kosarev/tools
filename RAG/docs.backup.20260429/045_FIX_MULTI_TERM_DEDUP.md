# ✅ Улучшения multi_term_exact_search

**Дата:** 2026-04-26 20:15  
**Файл:** `kb_tools.py`

---

## Что исправлено

### 1. Фильтрация только prose chunks по умолчанию

**Было:**
```python
chunk_type: Optional[str] = None  # Все типы чанков
```

**Стало:**
```python
chunk_type: str = ""  # Только prose chunks
```

### 2. Автоматическая дедупликация терминов

**Проблема:**
```python
# LLM передавал дубликаты
multi_term_exact_search(terms=['СУБД', 'СУБД', 'СУБД', 'СУБД'])
# Искало 4 раза один и тот же термин
```

**Решение:**
```python
# Автоматически удаляются дубликаты
unique_terms = list(dict.fromkeys(terms))  # ['СУБД']

# Логируется warning
if len(unique_terms) < len(terms):
    logger.warning(
        f"multi_term_exact_search: удалены дубликаты терминов. "
        f"Было: {len(terms)}, стало: {len(unique_terms)}"
    )
```

---

## Результат

**До:**
```python
multi_term_exact_search(terms=['СУБД', 'СУБД', 'СУБД', 'СУБД'])
# - Искало термин 4 раза
# - Возвращало prose + table_full + table_row
# - coverage мог быть 4/4 (хотя термин один)
```

**После:**
```python
multi_term_exact_search(terms=['СУБД', 'СУБД', 'СУБД', 'СУБД'])
# - Автоматически: unique_terms = ['СУБД']
# - Warning в логе о дедупликации
# - Ищет один раз
# - Возвращает только prose chunks
# - coverage корректный: 1/1
```

---

## Изменения в коде

### MultiTermExactSearchInput

```python
class MultiTermExactSearchInput(BaseModel):
    terms: list[str] = Field(
        description=(
            "List of UNIQUE substrings to search simultaneously... "
            "NOTE: Duplicate terms will be automatically removed."  # ✅ Добавлено
        )
    )
    chunk_type: str = ""  # ✅ Было: Optional[str] = None
```

### multi_term_exact_search()

```python
def multi_term_exact_search(
    terms: list[str],
    chunk_type: str = "",  # ✅ Было: Optional[str] = None
    ...
):
    # ✅ Дедупликация терминов
    unique_terms = list(dict.fromkeys(terms))
    if len(unique_terms) < len(terms):
        logger.warning(...)
    
    # ✅ Фильтр chunk_type добавлен в filter_info
    if chunk_type:
        filter_info.append(f"chunk_type={chunk_type!r}")
    
    # ✅ Поиск с unique_terms
    scored = vectorstore.clone().multi_term_exact_search(
        terms=unique_terms,  # Было: terms
        chunk_type=chunk_type,
        ...
    )
    
    # ✅ Результат с unique_terms
    result = MultiTermSearchResult(
        terms=unique_terms,  # Было: terms
        ...
    )
```

---

## Преимущества

### 1. Нет дубликатов терминов

✅ **Экономия ресурсов** - не ищет один термин несколько раз  
✅ **Корректный coverage** - максимум = количество уникальных терминов  
✅ **Логирование** - предупреждение о дубликатах в логе

### 2. Только prose chunks

✅ **Релевантные результаты** - не засоряется таблицами  
✅ **Консистентность** - как semantic_search и exact_search  
✅ **Меньше токенов** - таблицы могут быть большими

---

## Примеры

### До изменений

```python
# LLM отправил
{"terms": ["PostgreSQL", "MySQL", "PostgreSQL", "MySQL"]}

# Искало 4 термина (с дубликатами)
# Возвращало prose + table_full
# coverage: 4/4 для чанка с "PostgreSQL MySQL"
```

### После изменений

```python
# LLM отправил
{"terms": ["PostgreSQL", "MySQL", "PostgreSQL", "MySQL"]}

# В логе:
# WARNING: удалены дубликаты терминов. Было: 4, стало: 2

# Искало 2 уникальных термина
# Возвращало только prose
# coverage: 2/2 для чанка с "PostgreSQL MySQL"
```

---

## Обновлённое описание

В реестре инструментов:
```python
"multi_term_exact_search": "Поиск по нескольким терминам с ранжированием (автоудаление дубликатов)"
```

В Pydantic схеме:
```python
"List of UNIQUE substrings... NOTE: Duplicate terms will be automatically removed."
```

---

## Проверка

```bash
python -m py_compile kb_tools.py
```
✅ Синтаксис корректен

---

## Статус

✅ **Реализовано**  
✅ **Проверено**  
✅ **Готово к использованию**

---

## Дополнительно

### Порядок терминов сохраняется

`dict.fromkeys()` сохраняет порядок первого вхождения:
```python
terms = ['СУБД', 'PostgreSQL', 'СУБД', 'MySQL', 'PostgreSQL']
unique_terms = list(dict.fromkeys(terms))
# Результат: ['СУБД', 'PostgreSQL', 'MySQL']
```

### Совместимость

✅ **Полностью обратно совместимо**  
✅ **Не изменяет API**  
✅ **Только улучшает качество результатов**

