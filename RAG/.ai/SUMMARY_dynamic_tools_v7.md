# Резюме: Динамическая генерация списка инструментов из реестра

**Дата:** 2026-04-27  
**Задача:** ✅ Выполнена  
**Версия:** v7 (08)

---

## 🎯 ЧТО СДЕЛАНО

### Проблема
- Список инструментов в `system_prompt.md` был статическим - при добавлении нового инструмента нужно было обновлять промпт
- Дублирование: список был в промпте + дополнительно вставлялся в код через `_format_tools_list()`
- Риск рассинхронизации между реестром инструментов и промптом

### Решение
- ✅ Заменили статическую таблицу на плейсхолдер `{available_tools}`
- ✅ Добавили динамическую генерацию таблицы из реестра `get_tool_registry()`
- ✅ Удалили дублирование - теперь один источник истины

---

## 📁 ИЗМЕНЕННЫЕ ФАЙЛЫ

### 1. `system_prompt.md`
**Было:**
```markdown
| Инструмент | Назначение | Ключевой параметр |
|------------|------------|-------------------|
| `semantic_search` | Поиск по смыслу (эмбеддинги) | `query` |
| `exact_search` | Точный поиск подстроки | `substring` |
...
```

**Стало:**
```markdown
{available_tools}
```

### 2. `rag_lg_agent.py`

**Добавлено:**
```python
def _build_tools_table() -> str:
    """Генерирует markdown-таблицу доступных инструментов из реестра."""
    tool_registry = get_tool_registry()
    
    lines = [
        "| Инструмент | Назначение |",
        "|------------|------------|"
    ]
    
    for tool_name, description in tool_registry.items():
        safe_description = description.replace("|", "\\|")
        lines.append(f"| `{tool_name}` | {safe_description} |")
    
    return "\n".join(lines)
```

**Обновлено:**
```python
def _load_system_prompt() -> str:
    """Загружает system_prompt.md и подставляет динамический список инструментов."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    if prompt_path.exists():
        prompt_template = prompt_path.read_text(encoding="utf-8")
        
        # Подставляем таблицу инструментов
        tools_table = _build_tools_table()
        prompt = prompt_template.replace("{available_tools}", tools_table)
        
        return prompt
    else:
        logger.warning(f"system_prompt.md не найден в {prompt_path}")
        return "Ты - аналитический AI-агент"
```

**Удалено:**
- ❌ Функция `_format_tools_list()` - больше не нужна
- ❌ Вызовы `{_format_tools_list()}` в plan_node и action_node - дублирование

### 3. `README.md`
- ✅ Добавлена запись в "История исправлений" (v7)

### 4. `.ai/version.txt`
- ✅ Обновлен номер версии: 07 → 08

### 5. Дополнительно созданы (для будущего)
- `system_prompt_react_agent.md` - отдельный промпт для ReAct агента (на случай если понадобится)
- Обновлен `system_prompts.py` - зарегистрирован react_agent промпт

---

## 🔄 КАК ЭТО РАБОТАЕТ

### Поток данных:

```
1. get_tool_registry() в kb_tools.py
   ↓
   Возвращает dict {tool_name: description}
   
2. _build_tools_table() в rag_lg_agent.py
   ↓
   Конвертирует в markdown-таблицу
   
3. _load_system_prompt() в rag_lg_agent.py
   ↓
   Загружает system_prompt.md
   Подставляет {available_tools} → таблица
   
4. _SYSTEM_PROMPT используется в узлах графа
   ↓
   LLM получает актуальный список инструментов
```

### Единый источник истины:

```python
# kb_tools.py
def get_tool_registry() -> dict[str, str]:
    return {
        "semantic_search": "Семантический поиск по эмбеддингам",
        "exact_search": "Точный поиск по подстроке",
        "multi_term_exact_search": "Поиск по нескольким терминам с ранжированием",
        ...
    }
```

**Добавляем новый инструмент:**
1. Добавляем в `get_tool_registry()`
2. Всё! Промпт автоматически обновится при следующем запуске

---

## ✅ РЕЗУЛЬТАТ

### Преимущества:
- ✅ **Нет дублирования** - список инструментов в одном месте
- ✅ **Автоматическая актуализация** - добавил в реестр → появилось в промпте
- ✅ **Единый источник истины** - `get_tool_registry()`
- ✅ **Меньше кода** - удалена функция `_format_tools_list()` и её вызовы
- ✅ **Проще поддержка** - не нужно синхронизировать разные места

### Что улучшилось:
- Добавление нового инструмента: 1 место (реестр) вместо 3 (реестр + промпт + код)
- Изменение описания: 1 место вместо 2-3
- Риск рассинхронизации: 0%

---

## 🧪 ТЕСТИРОВАНИЕ

### Проверка генерации таблицы:
```python
from rag_lg_agent import _build_tools_table

# Должна вывести markdown-таблицу со всеми 15 инструментами
print(_build_tools_table())
```

### Проверка загрузки промпта:
```python
from rag_lg_agent import _SYSTEM_PROMPT

# Должен содержать таблицу инструментов (не плейсхолдер)
assert "{available_tools}" not in _SYSTEM_PROMPT
assert "semantic_search" in _SYSTEM_PROMPT
assert "| Инструмент | Назначение |" in _SYSTEM_PROMPT
```

### Проверка работы агента:
```bash
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python rag_lg_agent.py "найди все СУБД"
```

**Ожидаемый результат:**
- ✅ Агент загружается без ошибок
- ✅ В логах видна таблица с 15 инструментами
- ✅ Агент корректно вызывает инструменты

---

## 📚 ДОКУМЕНТАЦИЯ

Обновлена документация:
- `RAG/README.md` - раздел "История исправлений" (v7)
- `RAG/system_prompt.md` - теперь с плейсхолдером {available_tools}
- `RAG/.ai/SUMMARY_dynamic_tools_v7.md` - этот файл

---

## 🔮 БУДУЩЕЕ

### Возможные улучшения:
1. **Детальное описание параметров** - добавить в таблицу колонку с ключевыми параметрами каждого инструмента
2. **Pydantic-схемы в промпте** - использовать `pydantic_to_markdown()` для автогенерации детального описания параметров
3. **Категории инструментов** - группировать по типам (search, read, list, etc.)
4. **Примеры использования** - генерировать примеры вызовов автоматически

---

**Статус:** ✅ Готово к использованию  
**Автор:** AI Agent (GitHub Copilot)  
**Версия:** 1.0

