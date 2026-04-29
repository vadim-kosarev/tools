# Система Логирования RAG Агента

## Отдельные Файлы для Каждого Запроса/Ответа

Каждый запрос к LLM или инструменту логируется в **отдельный файл** со **сквозной нумерацией**.

### Структура Файлов

```
logs/
  001_llm_plan_request.log          🤖 → Запрос к LLM на этапе PLAN
  002_llm_plan_response.log         🤖 ← Ответ LLM (план поиска)
  003_tool_exact_search_request.log 🔧 → Вызов инструмента exact_search
  004_tool_exact_search_response.log🔧 ← Результат exact_search
  005_llm_action_request.log        🤖 → Запрос к LLM на этапе ACTION
  006_llm_action_response.log       🤖 ← Ответ LLM (tool calls)
  007_tool_semantic_search_request.log  🔧 → Вызов semantic_search
  008_tool_semantic_search_response.log 🔧 ← Результат semantic_search
  ...
```

### Формат Имени Файла

```
{номер:03d}_{тип}_{этап}_{направление}.log
```

**Примеры:**
- `001_llm_plan_request.log` - LLM запрос на этапе PLAN
- `002_llm_plan_response.log` - LLM ответ с этапа PLAN
- `003_tool_exact_search_request.log` - вызов инструмента exact_search
- `004_tool_exact_search_response.log` - результат exact_search

### Содержимое Файлов

Каждый файл содержит **одно событие** с полной структурой:

#### LLM Request (например, `001_llm_plan_request.log`)
```
................................................................................
  #001  2026-04-27 23:00:00  [PLAN]  REQUEST
................................................................................
Model: ChatOllama
----------------------------------------

[SYSTEM]
# System Prompt для Аналитического AI-Агента
...

[AVAILABLE_TOOLS]
[
  {"name":"semantic_search",...},
  {"name":"exact_search",...}
]

[MESSAGES]

[USER]
найди все СУБД
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  end #001 REQUEST
```

#### LLM Response (например, `002_llm_plan_response.log`)
```
................................................................................
  #002  2026-04-27 23:00:01  [PLAN]  RESPONSE
................................................................................
{
  "status": "plan",
  "step": 1,
  "thought": "нужно найти упоминания СУБД в документации",
  "plan": [
    "точный поиск по термину 'СУБД'",
    "семантический поиск по 'системы управления базами данных'"
  ]
}
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  end #002 RESPONSE
```

#### Tool Request (например, `003_tool_exact_search_request.log`)
```
--------------------------------------------------------------------------------
  #003  2026-04-27 23:00:02  [TOOL:exact_search]  REQUEST
--------------------------------------------------------------------------------
{"substring": "СУБД", "limit": 30}
..............  end #003 REQUEST  ..............
```

#### Tool Response (например, `004_tool_exact_search_response.log`)
```
--------------------------------------------------------------------------------
  #004  2026-04-27 23:00:03  [TOOL:exact_search]  RESPONSE
--------------------------------------------------------------------------------
[args]
{"substring": "СУБД", "limit": 30}
----------------------------------------
**SearchChunksResult**

- **query:** СУБД
- **chunks:** (5 элементов)
  1. ChunkResult(source=database.md, section=Архитектура > СУБД, line=150, content='PostgreSQL СУБД...')
  2. ChunkResult(source=overview.md, section=Компоненты, line=220, content='Используемые СУБД...')
  ...
- **total_found:** 5
..............  end #004 RESPONSE  ..............
```

## Навигация по Логам

### Скрипт `show_logs.py`

```bash
# Показать последние 20 файлов
python show_logs.py

# Показать все файлы
python show_logs.py --all

# Показать последние 50 файлов
python show_logs.py --last 50

# Показать с первыми строками содержимого
python show_logs.py --content

# Показать содержимое конкретного файла
python show_logs.py --show 5

# Следить за новыми файлами (live)
python show_logs.py --tail
```

### Пример Вывода

```
================================================================================
Лог-файлы в logs
Показано: 10 файлов
================================================================================

001 🤖 → LLM  plan_request                        23:00:00   1.5KB
002 🤖 ← LLM  plan_response                       23:00:01   0.3KB
003 🔧 → TOOL exact_search_request                23:00:02   0.1KB
004 🔧 ← TOOL exact_search_response               23:00:03   2.8KB
005 🔧 → TOOL semantic_search_request             23:00:04   0.1KB
006 🔧 ← TOOL semantic_search_response            23:00:05   3.2KB
007 🤖 → LLM  observation_request                 23:00:06   4.5KB
008 🤖 ← LLM  observation_response                23:00:07   0.5KB
009 🤖 → LLM  final_request                       23:00:08   5.1KB
010 🤖 ← LLM  final_response                      23:00:09   1.2KB
```

## Преимущества

### ✅ Читаемость
- Один файл = одно событие
- Легко найти нужный запрос/ответ
- Не нужно пролистывать тысячи строк

### ✅ Навигация
- Сквозная нумерация (001, 002, 003...)
- Понятные имена файлов
- Быстрый переход к нужному моменту

### ✅ Анализ
- Видно последовательность вызовов
- Легко сравнить запрос и ответ
- Можно открыть несколько файлов одновременно

### ✅ Отладка
- Быстрый поиск проблемного вызова
- Изолированное изучение каждого события
- Легко поделиться конкретным файлом

## Настройка

### Включение Режима Отдельных Файлов

В коде агента (уже включено по умолчанию):

```python
llm_logger = LlmCallLogger(
    enabled=True,
    separate_files=True  # ← режим отдельных файлов
)
```

### Режим Одного Файла (старый)

Если нужен единый файл `_rag_llm.log`:

```python
llm_logger = LlmCallLogger(
    enabled=True,
    separate_files=False  # ← единый файл
)
```

## Автоматическая Очистка

Файлы НЕ удаляются автоматически. Для очистки:

```bash
# Удалить все лог-файлы
rm logs/[0-9][0-9][0-9]_*.log

# Оставить последние 100
cd logs
ls [0-9][0-9][0-9]_*.log | head -n -100 | xargs rm

# PowerShell: удалить старше 7 дней
Get-ChildItem logs\[0-9][0-9][0-9]_*.log | Where-Object {$_.LastWriteTime -lt (Get-Date).AddDays(-7)} | Remove-Item
```

## Интеграция с Другими Инструментами

### Visual Studio Code

Откройте папку `logs` в VS Code:
- Удобная навигация по файлам
- Поиск по содержимому
- Diff между запросами

### Shell

```bash
# Найти все запросы к exact_search
ls logs/*exact_search_request.log

# Посмотреть все ответы LLM
cat logs/*llm*response.log

# Grep по всем tool результатам
grep -h "total_found" logs/*tool*response.log
```

## Структура Секций (Сохранена)

Все секции из `LLM_REQUEST_STRUCTURE.md` сохранены:
- `[SYSTEM]` - системный промпт
- `[AVAILABLE_TOOLS]` - инструменты (компактный JSON)
- `[MESSAGES]` - история
  - `[USER]` - запросы
  - `[ASSISTANT]` - ответы
  - `[TOOL_CALLS]` - вызовы (компактный JSON)
  - `[TOOL_RESULT: name]` - результаты

## Совместимость

- ✅ Работает с существующим `LangChainFileLogger`
- ✅ Streaming режим поддерживается
- ✅ Thread-safe (параллельные вызовы)
- ✅ Обратная совместимость (режим одного файла)

---

**Обновлено:** 2026-04-27  
**Версия:** 2.0  
**Файлы:** `llm_call_logger.py`, `show_logs.py`

