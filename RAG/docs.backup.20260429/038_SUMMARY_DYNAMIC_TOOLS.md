# ✅ Исправление: Динамический список инструментов

**Дата:** 2026-04-26 19:15  
**Задача:** Убрать захардкоженный список инструментов, использовать реестр

---

## Что было

```python
# plan_node - захардкожен список
system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: plan

Доступные инструменты:
- semantic_search: семантический поиск по документации
- exact_search: точный поиск подстроки
- multi_term_exact_search: поиск по нескольким терминам
- regex_search: поиск по regex-паттерну
- find_sections_by_term: поиск разделов содержащих термин
- find_relevant_sections: семантический поиск разделов
- get_section_content: чтение полного содержимого раздела
- read_table: чтение табличных данных из раздела
- list_sections: список всех разделов в файле
"""
```

❌ При добавлении нового инструмента нужно обновлять код  
❌ Риск рассинхронизации с реальными инструментами  
❌ Нарушение DRY принципа

---

## Что стало

```python
# Импорт функции из реестра
from kb_tools import create_kb_tools, get_tool_registry

# Новая функция форматирования
def _format_tools_list() -> str:
    """Форматирует список доступных инструментов из реестра для промпта."""
    tool_registry = get_tool_registry()
    lines = ["Доступные инструменты:"]
    for tool_name, description in tool_registry.items():
        lines.append(f"- {tool_name}: {description}")
    return "\n".join(lines)

# plan_node - динамический список
system_message = f"""{_SYSTEM_PROMPT}

ТЕКУЩИЙ ЭТАП: plan

{_format_tools_list()}

Сформируй план поиска для ответа на вопрос пользователя."""
```

✅ Список всегда актуален  
✅ Один источник истины (`kb_tools.py`)  
✅ Автоматическое обновление

---

## Изменённые файлы

1. **`rag_lg_agent.py`**
   - Добавлен импорт: `get_tool_registry`
   - Создана функция: `_format_tools_list()`
   - Обновлён `plan_node`: динамический список
   - Обновлён `action_node`: динамический список

2. **Документация**
   - `doc/FIX_DYNAMIC_TOOLS_LIST.md` - полное описание
   - `READY.md` - обновлён раздел исправлений

---

## Реестр инструментов

Находится в `kb_tools.py`, функция `get_tool_registry()`:

```python
{
    "semantic_search": "Семантический поиск по эмбеддингам",
    "exact_search": "Точный поиск по подстроке",
    "exact_search_in_file": "Точный поиск в конкретном файле",
    "exact_search_in_file_section": "Точный поиск в разделе файла",
    "multi_term_exact_search": "Поиск по нескольким терминам",
    "find_sections_by_term": "Поиск разделов содержащих термин",
    "find_relevant_sections": "Двухэтапный поиск разделов",
    "regex_search": "Поиск по regex-паттернам",
    "read_table": "Чтение строк таблицы",
    "get_section_content": "Полный текст раздела",
    "list_sections": "Список разделов",
    "get_neighbor_chunks": "Соседние чанки",
    "list_sources": "Список файлов",
    "list_all_sections": "Все пары (source, section)",
}
```

**Всего: 14 инструментов**

---

## Проверка

```bash
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python -m py_compile rag_lg_agent.py
```

✅ **Результат:** Синтаксис корректен

---

## Результат

**До:**
- 9 инструментов в списке (захардкожено)
- Риск устаревания
- 3 места для обновления при изменениях

**После:**
- 14 инструментов (все доступные)
- Всегда актуально
- 1 место для обновления (реестр в `kb_tools.py`)

---

✅ **Готово к использованию**

