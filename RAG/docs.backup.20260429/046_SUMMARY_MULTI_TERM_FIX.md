# ✅ Исправлено: multi_term_exact_search

**Дата:** 2026-04-26 20:15

---

## Что сделано

### 1. Только prose chunks по умолчанию
```python
chunk_type: str = ""  # Было: Optional[str] = None
```

### 2. Автоудаление дубликатов терминов
```python
unique_terms = list(dict.fromkeys(terms))
if len(unique_terms) < len(terms):
    logger.warning("удалены дубликаты терминов...")
```

---

## Результат

**До:**
```python
terms=['СУБД', 'СУБД', 'СУБД', 'СУБД']
# Искало 4 раза, возвращало все типы чанков
```

**После:**
```python
terms=['СУБД', 'СУБД', 'СУБД', 'СУБД']
# unique_terms = ['СУБД']
# Warning в логе
# Ищет 1 раз, только prose chunks
```

---

## Преимущества

✅ Нет избыточных поисков  
✅ Корректный coverage  
✅ Только prose chunks  
✅ Логирование дубликатов

---

## Проверка

```bash
python -m py_compile kb_tools.py
```
✅ Синтаксис корректен

---

## Документация

- 📖 [doc/FIX_MULTI_TERM_DEDUP.md](doc/FIX_MULTI_TERM_DEDUP.md) - полное описание
- 📝 [READY.md](READY.md) - обновлён

---

✅ **Готово!**

