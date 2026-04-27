# 🎯 Быстрый Старт: Новая Система Логирования

## ✅ Что Изменилось

**БЫЛО:** Один огромный файл `_rag_llm.log` с тысячами строк  
**СТАЛО:** Отдельные файлы для каждого запроса/ответа с нумерацией

## 📁 Структура Логов

```
logs/
  001_llm_plan_request.log          🤖 → Запрос к LLM (PLAN)
  002_llm_plan_response.log         🤖 ← Ответ LLM (план)
  003_tool_exact_search_request.log 🔧 → Вызов exact_search
  004_tool_exact_search_response.log🔧 ← Результат exact_search
  005_llm_observation_request.log   🤖 → Запрос к LLM (OBSERVATION)
  006_llm_observation_response.log  🤖 ← Ответ LLM (анализ)
  ...
```

## 🚀 Как Использовать

### 1. Запустить Агента

```bash
python rag_lg_agent.py "найди все СУБД"
```

Логи автоматически создаются в `logs/` с нумерацией 001, 002, 003...

**⚠️ Важно:** При запуске агента **директория logs автоматически очищается** от старых файлов логов. Это обеспечивает чистую нумерацию с 001 для каждого нового запуска.

### 2. Посмотреть Список Логов

```bash
# Последние 20 файлов
python show_logs.py

# Все файлы
python show_logs.py --all

# Последние 50
python show_logs.py --last 50
```

**Вывод:**
```
================================================================================
Лог-файлы в logs
Показано: 10 файлов
================================================================================

001 🤖 → LLM  plan_request                        23:00:00   1.5KB
002 🤖 ← LLM  plan_response                       23:00:01   0.3KB
003 🔧 → TOOL exact_search_request                23:00:02   0.1KB
004 🔧 ← TOOL exact_search_response               23:00:03   2.8KB
...
```

### 3. Открыть Конкретный Файл

```bash
# Показать содержимое файла #5
python show_logs.py --show 5

# Или просто открыть в редакторе
code logs/005_llm_observation_response.log
```

### 4. Следить За Новыми Логами (Live)

```bash
python show_logs.py --tail
```

**Вывод:**
```
🔍 Отслеживание новых логов (Ctrl+C для выхода)
================================================================================

23:15:30 001 🤖 → plan_request
23:15:31 002 🤖 ← plan_response
23:15:32 003 🔧 → exact_search_request
23:15:33 004 🔧 ← exact_search_response
...
```

## 📖 Содержимое Файлов

Каждый файл содержит **полную структуру** с секциями:

### LLM Request (`001_llm_plan_request.log`)
```
[SYSTEM]
# System Prompt для Аналитического AI-Агента
...

[AVAILABLE_TOOLS]
[{"name":"semantic_search",...},...]

[MESSAGES]

[USER]
найди все СУБД
```

### LLM Response (`002_llm_plan_response.log`)
```
{
  "status": "plan",
  "step": 1,
  "thought": "нужно найти СУБД",
  "plan": [...]
}
```

### Tool Request (`003_tool_exact_search_request.log`)
```
{"substring": "СУБД", "limit": 30}
```

### Tool Response (`004_tool_exact_search_response.log`)
```
**SearchChunksResult**
- query: СУБД
- chunks: (5 элементов)
  1. ChunkResult(source=database.md, section=..., line=150, content='...')
- total_found: 5
```

## 💡 Преимущества

### ✅ Читаемость
- **1 файл = 1 событие** - не нужно пролистывать тысячи строк
- Понятные имена файлов
- Сохранена структура с секциями

### ✅ Навигация
- Сквозная нумерация (001, 002, 003...)
- Легко найти нужный момент
- Быстрый переход между файлами

### ✅ Анализ
- Видно последовательность вызовов
- Легко сравнить запрос и ответ
- Можно открыть несколько файлов сразу

### ✅ Отладка
- Быстрый поиск проблемного вызова
- Изолированное изучение
- Легко поделиться конкретным файлом

## 🔧 Настройка

### По Умолчанию (Отдельные Файлы)

Уже включено! Ничего делать не нужно.

### Вернуть Старый Режим (Один Файл)

В `rag_lg_agent.py`:

```python
llm_logger = LlmCallLogger(
    enabled=True,
    separate_files=False  # ← единый файл _rag_llm.log
)
```

## 🧹 Очистка Логов

### Автоматическая Очистка (По Умолчанию)

**Логи очищаются автоматически** при каждом запуске `rag_lg_agent.py`:
- Удаляются файлы `001_*.log`, `002_*.log`, и т.д.
- Удаляется `_rag_llm.log` (старый формат)
- Сохраняются другие файлы в `logs/`

Это обеспечивает чистую нумерацию с 001 для каждого нового запуска.

### Ручная Очистка (Опционально)

Если нужно очистить логи вручную без запуска агента:

```bash
# PowerShell: удалить все файлы логов
Remove-Item logs\[0-9][0-9][0-9]_*.log
Remove-Item logs\_rag_llm.log -ErrorAction SilentlyContinue

# Bash: удалить все
rm logs/[0-9][0-9][0-9]_*.log
rm logs/_rag_llm.log 2>/dev/null

# Удалить старше 7 дней (если автоочистка отключена)
Get-ChildItem logs\[0-9][0-9][0-9]_*.log | Where-Object {$_.LastWriteTime -lt (Get-Date).AddDays(-7)} | Remove-Item
```

## 📚 Документация

- `008_LOGGING_SYSTEM.md` - Полное описание системы
- `007_LLM_REQUEST_STRUCTURE.md` - Структура секций запросов
- `009_AUTO_CLEAR_LOGS.md` - Автоматическая очистка логов

## 🎓 Примеры

### Найти все запросы к exact_search
```bash
ls logs/*exact_search_request.log
```

### Посмотреть все ответы LLM
```bash
cat logs/*llm*response.log | less
```

### Grep по результатам поиска
```bash
grep -h "total_found" logs/*tool*response.log
```

### VS Code
```bash
code logs/
```
Откроется папка с удобной навигацией по файлам.

---

**Версия:** 2.0  
**Дата:** 2026-04-27  
**Файлы:** `llm_call_logger.py`, `show_logs.py`, `test_separate_logs.py`

