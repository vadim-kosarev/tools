# ✅ Изменение: Фильтрация chunk_type в semantic_search и exact_search

**Дата:** 2026-04-26 19:45  
**Файл:** `kb_tools.py`

---

## Что изменилось

### Инструменты поиска теперь возвращают только prose chunks

**До:**
```python
semantic_search(query="PostgreSQL")
exact_search(substring="PostgreSQL")
# Возвращали: prose + table_full + table_row
```

**После:**
```python
semantic_search(query="PostgreSQL")
exact_search(substring="PostgreSQL")
# Возвращают: только prose chunks (chunk_type="")
```

---

## Изменения в коде

### 1. SemanticSearchInput
```python
# Добавлено новое поле
chunk_type: str = ""  # По умолчанию только prose
```

### 2. ExactSearchInput
```python
# Изменён default
chunk_type: str = ""  # Было: Optional[str] = None
```

### 3. Функции
```python
def semantic_search(query: str, chunk_type: str = "", ...):
    # Добавлен параметр chunk_type

def exact_search(substring: str, chunk_type: str = "", ...):
    # Изменён default с None на ""
```

---

## Типы чанков

| Тип | Описание | Использование |
|-----|----------|---------------|
| `""` | Prose chunks | **Default** для текстового поиска |
| `"table_row"` | Строки таблиц | Явно указать для поиска в таблицах |
| `"table_full"` | Полные таблицы | Редко, есть `read_table` |

---

## Для работы с таблицами

```python
# Специальный инструмент
read_table(section="Список серверов", limit=50)

# Или явный поиск в таблицах
exact_search(substring="10.0.0.1", chunk_type="table_row")
```

---

## Преимущества

✅ Более релевантные результаты  
✅ Таблицы не засоряют поиск  
✅ Меньше токенов  
✅ Явный контроль

---

## Проверка

```bash
python -m py_compile kb_tools.py
```
✅ Синтаксис корректен

---

## Документация

- 📖 [doc/CHANGE_CHUNK_TYPE_FILTER.md](doc/CHANGE_CHUNK_TYPE_FILTER.md) - полное описание
- 📝 [READY.md](READY.md) - обновлён

---

✅ **Готово!**

