# Документация RAG проекта

## 📖 Основные гайды

| Файл | Описание |
|------|----------|
| [001_LLM_MESSAGES_GUIDE.md](./001_LLM_MESSAGES_GUIDE.md) | Руководство по работе с LLM сообщениями |
| [002_SYSTEM_PROMPT_GUIDE.md](./002_SYSTEM_PROMPT_GUIDE.md) | Системный промпт для аналитического агента (Quick Start) |
| [003_READY.md](./003_READY.md) | Статус готовности проекта |
| [004_message_history_fix.md](./004_message_history_fix.md) | ✨ **НОВОЕ** Исправление истории сообщений в LLM логах |
| [004_system_prompt_react_agent.md](./004_system_prompt_react_agent.md) | System prompt для ReAct агента |
| [005_README.md](./005_README.md) | Основная документация проекта |
| [007_LLM_REQUEST_STRUCTURE.md](./007_LLM_REQUEST_STRUCTURE.md) | Структура LLM запросов |
| [008_LOGGING_SYSTEM.md](./008_LOGGING_SYSTEM.md) | Система логирования |
| [009_AUTO_CLEAR_LOGS.md](./009_AUTO_CLEAR_LOGS.md) | Автоматическая очистка логов |
| [010_QUICKSTART_LOGGING.md](./010_QUICKSTART_LOGGING.md) | Быстрый старт с логированием |

## 🔧 Рабочие заметки

В этой папке также находятся рабочие заметки о развитии проекта:
- `COMPLETE_*.md` - завершенные задачи
- `FIX_*.md` - исправления багов
- `SUMMARY_*.md` - сводки изменений
- `MIGRATION_*.md` - миграции и рефакторинг
- и другие...

## 📁 Структура проекта

```
RAG/
├── docs/              # Документация (вы здесь)
├── system_prompt.md   # Системный промпт (используется в коде)
├── *.py              # Исходный код
└── ...
```

## 🚀 Быстрый старт

1. Начните с [005_README.md](./005_README.md) - основная документация
2. Изучите [002_SYSTEM_PROMPT_GUIDE.md](./002_SYSTEM_PROMPT_GUIDE.md) для понимания work flow агента
3. Посмотрите [001_LLM_MESSAGES_GUIDE.md](./001_LLM_MESSAGES_GUIDE.md) для работы с сообщениями
4. **НОВОЕ:** [004_message_history_fix.md](./004_message_history_fix.md) - исправление истории сообщений в LLM логах

