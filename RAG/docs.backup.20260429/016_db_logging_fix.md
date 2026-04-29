# 016. Исправление пустых файлов xxx_llm_db в логах

## Проблема

В папке `logs/` создавались пустые файлы с именами типа `007_llm_db`, `008_llm_db` без расширения `.log` и без содержимого.

### Симптомы
```
logs/
├── 007_llm_db      (0 байт, пустой)
├── 008_llm_db      (0 байт, пустой)
├── 009_llm_db      (0 байт, пустой)
├── 020_llm_db      (0 байт, пустой)
└── ...
```

Эти файлы создавались вместо логов для запросов к базе данных (DB queries) и накапливались при каждом запуске.

## Причина

### 1. Использование недопустимого символа в Windows

В `kb_tools.py` запросы к базе данных логировались с префиксом `"DB:"`:
```python
rec = _db_request("DB:semantic_search", ...)  # step = "DB:semantic_search"
```

В `llm_call_logger.py` формировалось имя файла:
```python
filename = f"{number:03d}_llm_{step.lower()}_{kind_lower}.log"
# step = "DB:semantic_search" → filename = "007_llm_db:semantic_search_request.log"
```

**НО!** Символ `:` (двоеточие) недопустим в именах файлов Windows!

При попытке создать файл `007_llm_db:semantic_search_request.log`, Windows создавал файл только до двоеточия: `007_llm_db` (без расширения и остальной части имени), а затем выдавал ошибку при записи → файл оставался пустым.

### 2. Неправильная категоризация DB запросов

DB запросы обрабатывались как LLM calls (префикс `llm_`), хотя по сути являются tool calls (запросы к базе данных через инструменты).

## Решение

### 1. Замена недопустимых символов

В `llm_call_logger.py`, метод `_write()`:
```python
# БЫЛО:
tool_name = step.replace("TOOL:", "") if is_tool else step.lower()
filename = f"{number:03d}_llm_{step.lower()}_{kind_lower}.log"

# СТАЛО:
tool_name = step.replace("TOOL:", "").replace("DB:", "") if is_tool else step.lower()
# Заменяем недопустимые символы в именах файлов (для Windows)
tool_name = tool_name.replace(":", "_")
step_clean = step.lower().replace(":", "_")
filename = f"{number:03d}_llm_{step_clean}_{kind_lower}.log"
```

И аналогично в методе `_write_streaming_header()`:
```python
# БЫЛО:
filename = f"{number:03d}_llm_{step.lower()}_response.log"

# СТАЛО:
step_clean = step.lower().replace(":", "_")
filename = f"{number:03d}_llm_{step_clean}_response.log"
```

### 2. Категоризация DB запросов как tool calls

```python
# БЫЛО:
is_tool  = step.startswith("TOOL:")

# СТАЛО:
is_tool  = step.startswith("TOOL:") or step.startswith("DB:")
```

Теперь DB запросы логируются как tool calls с префиксом `tool_` в именах файлов.

## Результат

### До исправления
```
logs/
├── 007_llm_db         ❌ пустой файл (0 байт)
├── 008_llm_db         ❌ пустой файл (0 байт)
└── ...
```

### После исправления
```
logs/
├── 007_tool_semantic_search_request.log   ✅ полный лог запроса
├── 008_tool_semantic_search_response.log  ✅ полный лог ответа
├── 009_tool_exact_search_request.log      ✅ полный лог запроса
├── 010_tool_exact_search_response.log     ✅ полный лог ответа
└── ...
```

## Тестирование

Создан тест `test_db_logging.py` который проверяет:
- ✅ Нет пустых файлов
- ✅ Нет недопустимых символов в именах
- ✅ Все файлы создаются с правильными именами
- ✅ DB запросы логируются как tool calls

```bash
python test_db_logging.py
```

Результат:
```
✅ PASS: Все файлы созданы правильно!
✅ Создано файлов: 6
✅ Нет пустых файлов
✅ Нет недопустимых символов в именах
```

## Измененные файлы

- `llm_call_logger.py` - исправлены методы `_write()` и `_write_streaming_header()`
- `test_db_logging.py` - новый тест для проверки

## Очистка старых файлов

Удалить пустые файлы из логов:
```powershell
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG\logs
Remove-Item -Path "*_llm_db" -Force
```

## Совместимость

✅ **Обратно совместимо** - существующие логи не затронуты, новые создаются правильно.

## Дата

2026-04-28

---

**Примечание:** Эта проблема проявлялась только в Windows из-за ограничений на символы в именах файлов. В Linux/Mac файлы с `:` создавались бы нормально.

