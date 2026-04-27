# 015. Исправление структуры файлов документации

## Проблема

При создании документации для задачи "История сообщений в LLM" файлы были созданы с неправильными именами, не соответствующими правилам проекта:
- ❌ Не было нумерации (`SUMMARY.md` вместо `011_SUMMARY.md`)
- ❌ `CHANGELOG.md` находился в корне `RAG/` вместо `docs/`
- ❌ Внутренние ссылки указывали на старые имена

## Правила из CLAUDE.md

Согласно `CLAUDE.md`, все создаваемые агентом `.md` файлы должны:
1. Иметь префикс нумерации: `001.`, `002.`, `003.`, ...
2. Находиться в папке `docs/` (не засорять корневой каталог)
3. Функциональные файлы (используемые в коде) остаются там, где их создал пользователь

## Выполненные исправления

### 1. Переименование файлов

| Было | Стало |
|------|-------|
| `SUMMARY.md` | `011_SUMMARY.md` |
| `README_MESSAGE_HISTORY.md` | `012_README_MESSAGE_HISTORY.md` |
| `TASK_COMPLETE.md` | `013_TASK_COMPLETE.md` |
| `../CHANGELOG.md` | `014_CHANGELOG.md` (+ перемещение) |

### 2. Обновление внутренних ссылок

Во всех файлах обновлены ссылки на новые имена:
- ✅ `011_SUMMARY.md` - ссылки на другие файлы
- ✅ `012_README_MESSAGE_HISTORY.md` - структура проекта + документация
- ✅ `013_TASK_COMPLETE.md` - список файлов + документация
- ✅ `014_CHANGELOG.md` - список файлов
- ✅ `README.md` - быстрый старт (добавлена ссылка на новый гайд)

### 3. Результирующая структура

```
RAG/
├── rag_lg_agent.py              # код (не перемещается)
├── test_message_history.py      # тест (не перемещается)
└── docs/
    ├── 001_LLM_MESSAGES_GUIDE.md
    ├── 002_SYSTEM_PROMPT_GUIDE.md
    ├── 003_READY.md
    ├── 004_message_history_fix.md      ✅ правильно с самого начала
    ├── 004_system_prompt_react_agent.md
    ├── 005_README.md
    ├── 007_LLM_REQUEST_STRUCTURE.md
    ├── 008_LOGGING_SYSTEM.md
    ├── 009_AUTO_CLEAR_LOGS.md
    ├── 010_QUICKSTART_LOGGING.md
    ├── 011_SUMMARY.md                   🆕 переименован
    ├── 012_README_MESSAGE_HISTORY.md    🆕 переименован
    ├── 013_TASK_COMPLETE.md             🆕 переименован
    ├── 014_CHANGELOG.md                 🆕 перемещен + переименован
    ├── 015_FILE_STRUCTURE_FIX.md        🆕 этот файл
    └── ...другие файлы...
```

## Команды PowerShell

```powershell
# Переименование и перемещение
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG\docs
Rename-Item -Path "SUMMARY.md" -NewName "011_SUMMARY.md"
Rename-Item -Path "README_MESSAGE_HISTORY.md" -NewName "012_README_MESSAGE_HISTORY.md"
Rename-Item -Path "TASK_COMPLETE.md" -NewName "013_TASK_COMPLETE.md"
Move-Item -Path "..\CHANGELOG.md" -Destination ".\014_CHANGELOG.md"
```

## Проверка

```powershell
# Проверить правильную структуру
Get-ChildItem -Path .\docs -Filter "01*.md" | Select-Object Name | Sort-Object Name

# Проверить что CHANGELOG.md перемещен
Test-Path "C:\dev\github.com\vadim-kosarev\tools.0\RAG\CHANGELOG.md"  # должен вернуть False
```

## Результат

✅ **Все правила соблюдены:**
- Префикс нумерации `001.`, `002.`, ... ✓
- Все агентские файлы в `docs/` ✓
- Корневой каталог не засорен ✓
- Внутренние ссылки обновлены ✓

## Дата

2026-04-27

---

**Примечание:** Этот файл сам создан по всем правилам с номером `015_` и находится в `docs/`.

