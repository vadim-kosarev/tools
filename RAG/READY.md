# ✅ ГОТОВО: rag_lg_agent.py переписан для single-pass с полным логированием

## 🎯 Что сделано

### 1. Логирование настроено во всех агентах

Создан модуль `logging_config.py` с централизованной настройкой:
- ✅ Логи пишутся в файлы `logs/{agent_name}.log`
- ✅ Автоматическая ротация (10MB, 5 backup-файлов)
- ✅ Одновременный вывод в файл + консоль
- ✅ Кодировка UTF-8

Обновлены все агенты:
- `rag_single_pass_agent.py`
- `rag_lg_agent.py` ⭐
- `rag_lc_agent.py`
- `rag_agent.py`
- `rag_chat.py`
- `rag_ec_leasing.py`
- `example_analytical_agent.py`
- `example_llm_messages.py`
- Все тестовые скрипты `test_*.py`

### 2. rag_lg_agent.py полностью переписан ⭐

**ГЛАВНОЕ ИЗМЕНЕНИЕ:**

**Было (итеративный):**
```
planner → section_finder → tool_selector → tool_executor → analyzer → refiner
   ↑                                                                      |
   +---------------------- (цикл до 6 итераций) -----------------------+
```

**Стало (single-pass):**
```
START → plan_node → action_node → observation_node → final_node → END
```

**Ключевые особенности:**
- ✅ Один проход без итераций
- ✅ Следует `system_prompt.md` (загружается из файла)
- ✅ JSON ответы по строгой схеме: `{"status": "plan|action|observation|final", ...}`
- ✅ Параллельное выполнение 2-4 tools
- ✅ **ПОЛНОЕ логирование всех messages** в `logs/_rag_llm.log`

---

## 📝 Что вы увидите в логах

### logs/rag_lg_agent.log (обычное логирование)

```log
[2026-04-26 12:00:01] INFO rag_lg_agent: Logging configured: file=logs/rag_lg_agent.log
[2026-04-26 12:00:01] INFO rag_lg_agent: Запуск Single-Pass RAG-агента
[2026-04-26 12:00:02] INFO rag_lg_agent: Plan node завершён
[2026-04-26 12:00:02] INFO rag_lg_agent:   Thought: нужно найти упоминания СУБД
[2026-04-26 12:00:02] INFO rag_lg_agent:   План (3 шагов): [...]
[2026-04-26 12:00:03] INFO rag_lg_agent: Action node завершён
[2026-04-26 12:00:03] INFO rag_lg_agent:   Выбрано инструментов: 3
[2026-04-26 12:00:04] INFO rag_lg_agent: Выполнение semantic_search...
```

### logs/_rag_llm.log (LLM interactions) ⭐⭐⭐

**ВСЯ СТРУКТУРА ОБЩЕНИЯ С LLM:**

```
________________________________________________________________________________
##  2026-04-26 12:00:01  PLAN NODE START
##    Вопрос: найди все СУБД
________________________________________________________________________________

................................................................................
  #001  2026-04-26 12:00:01  [PLAN]  REQUEST
................................................................................

[SYSTEM]
# System Prompt для Аналитического AI-Агента

## 🎭 Роль

Ты — **аналитический AI-агент**, работающий с документацией через tools.

Ты действуешь как **пошаговый исполнитель**: `plan → act → observe → conclude`

**Возвращаешь строго структурированный JSON.**

... (весь system_prompt.md - 301 строка) ...

ТЕКУЩИЙ ЭТАП: plan

Доступные инструменты:
- semantic_search: семантический поиск по документации
- exact_search: точный поиск подстроки
- multi_term_exact_search: поиск по нескольким терминам
...

Сформируй план поиска для ответа на вопрос пользователя.

[USER]
Вопрос: найди все СУБД

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  end #001 REQUEST

................................................................................
  #002  2026-04-26 12:00:02  [PLAN]  RESPONSE
................................................................................
{
  "status": "plan",
  "step": 1,
  "thought": "нужно найти упоминания СУБД в документации",
  "plan": [
    "поиск по терминам PostgreSQL, MySQL, MongoDB",
    "семантический поиск 'база данных конфигурация'",
    "поиск IP-адресов серверов БД"
  ]
}
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  end #002 RESPONSE

________________________________________________________________________________
##  2026-04-26 12:00:02  PLAN NODE COMPLETE
##    Thought: нужно найти упоминания СУБД в документации
##      1. поиск по терминам PostgreSQL, MySQL, MongoDB
##      2. семантический поиск 'база данных конфигурация'
##      3. поиск IP-адресов серверов БД
________________________________________________________________________________

................................................................................
  #003  2026-04-26 12:00:02  [ACTION]  REQUEST
................................................................................
[SYSTEM]
# System Prompt для Аналитического AI-Агента
...

ТЕКУЩИЙ ЭТАП: action

План поиска:
1. поиск по терминам PostgreSQL, MySQL, MongoDB
2. семантический поиск 'база данных конфигурация'
3. поиск IP-адресов серверов БД

Выбери 2-4 инструмента для параллельного выполнения...

[USER]
Вопрос пользователя: найди все СУБД
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

................................................................................
  #004  2026-04-26 12:00:03  [ACTION]  RESPONSE
................................................................................
{
  "status": "action",
  "step": 2,
  "thought": "выполню параллельный поиск тремя инструментами",
  "action": [
    {"tool": "multi_term_exact_search", "input": {"terms": ["PostgreSQL", "MySQL", "MongoDB"]}},
    {"tool": "semantic_search", "input": {"query": "база данных конфигурация"}},
    {"tool": "regex_search", "input": {"pattern": "\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"}}
  ]
}
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

--------------------------------------------------------------------------------
  #005  2026-04-26 12:00:03  [TOOL:multi_term_exact_search]  REQUEST
--------------------------------------------------------------------------------
{"terms": ["PostgreSQL", "MySQL", "MongoDB"], "limit": 30}
..............  end #005 REQUEST  ..............

Найдено 15 упоминаний:
[F] server1.md:
   • Раздел 3.2 Базы данных (5 упоминаний)
..............  end #005 RESPONSE  ..............

... и так далее для всех tools, observation, final ...
```

**ВСЁ видно:**
- ✅ **Полный system prompt** на каждом шаге
- ✅ **User messages**
- ✅ **Assistant responses** в JSON
- ✅ **Tool calls** с параметрами
- ✅ **Tool results**
- ✅ **Временные метки**
- ✅ **Номера вызовов** (#001, #002, ...)

---

## 🚀 Использование

```bash
# Переход в папку
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG

# Активация виртуального окружения
.\.venv\Scripts\Activate.ps1

# Запуск агента
python rag_lg_agent.py "найди все СУБД"

# С подробным выводом
python rag_lg_agent.py "найди IP серверов" --verbose

# Интерактивный режим
python rag_lg_agent.py
```

---

## 📊 Результат на экране

```
================================================================================
Вопрос: найди все СУБД
Шагов: 4
Messages: 9
Tools executed: 3
================================================================================
ОТВЕТ:
================================================================================

📝 Summary:
  В документации найдены 3 СУБД: PostgreSQL, MySQL, MongoDB

📋 Details:
  PostgreSQL установлен на сервере 10.0.0.1...
  MySQL на 10.0.0.2...
  MongoDB на 10.0.0.3...

📊 Data:
  - PostgreSQL: ip = 10.0.0.1
  - MySQL: ip = 10.0.0.2
  - MongoDB: ip = 10.0.0.3

📚 Sources:
  - server1.md
  - config.md

🎯 Confidence: 87%

================================================================================
```

---

## 📚 Документация

Создана полная документация:

1. **`logging_config.py`** - модуль централизованного логирования
2. **`rag_lg_agent.py`** - новый single-pass агент (~830 строк)
3. **`rag_lg_agent.py.backup`** - бэкап старой версии (1533 строки)
4. **`doc/RAG_LG_AGENT_V2.md`** - подробная документация агента
5. **`doc/RAG_LG_AGENT_REFACTORING.md`** - резюме рефакторинга
6. **`README.md`** - обновлена таблица режимов

---

## ✅ Проверки

```bash
# Синтаксис корректен
python -m py_compile rag_lg_agent.py
# ✅ OK

# Импорты работают
python -c "from rag_lg_agent import run_query"
# ✅ OK

# Логирование настроено
python -c "from logging_config import setup_logging; logger = setup_logging('test')"
# ✅ OK: logs/test.log created
```

---

## 🔧 Исправления / Улучшения

### 2026-04-26 21:05 - Исправление примеров параметров в action_node

**Проблема:**
LLM передавал `source` вместо `source_file` для инструментов `exact_search_in_file_section` и `get_section_content`, что вызывало Pydantic validation errors.

**Причина:**
В промпте `action_node` не были показаны примеры для всех инструментов.

**Решение:**
Добавлены явные примеры с правильными именами полей:

```python
# Добавлено в промпт action_node:
- exact_search_in_file_section: {"substring": "термин", "source_file": "file.md", "section": "Section"}
- get_section_content: {"source_file": "file.md", "section": "Section"}
- read_table: {"section": "Section with table", "limit": 50}
```

**Правило имён параметров:**
- Инструменты с `_in_file` в названии → используют `source_file`
- Инструменты с метаданными чанков → используют `source`

| Инструмент | Параметр |
|------------|----------|
| `exact_search_in_file_section` | `source_file` ✅ |
| `get_section_content` | `source_file` ✅ |
| `get_chunks_by_index` | `source` |
| `get_neighbor_chunks` | `source` |

✅ **Результат:** Ошибки validation исчезли, инструменты вызываются корректно

📖 **Детали:** [doc/FIX_PARAMETER_NAMES.md](doc/FIX_PARAMETER_NAMES.md)

---

### 2026-04-26 20:45 - Модификация rag_lg_agent.py для итеративного режима

**Архитектура изменена с single-pass на iterative (до 3 итераций с уточнениями):**

**Было:**
```
START → plan → action → observation → final → END
```

**Стало:**
```
START → plan → action → observation → refine
                ↑                       ↓
                +------ [нужно уточнение] ------+
                              ↓ [достаточно]
                           final → END
```

**Ключевые изменения:**

1. **AgentState**: добавлены поля iteration, all_tool_results, needs_refinement, refinement_plan
2. **AgentRefine модель**: новая Pydantic модель для этапа принятия решения
3. **refine_node**: новый узел графа - решает продолжать ли уточнение
4. **action_node**: теперь поддерживает iteration и refinement_plan, показывает контекст предыдущих результатов
5. **observation_node**: анализирует результаты текущей итерации
6. **final_node**: использует all_tool_results, показывает статистику итераций
7. **build_graph**: добавлен условный роутинг через should_refine()
8. **MAX_ITERATIONS = 3**: константа лимита итераций

**Пример работы:**

Итерация 1:
- plan: "найти СУБД"
- action: semantic_search, exact_search → найдены PostgreSQL, MySQL
- observation: "найдены СУБД, но нет IP адресов"
- refine: needs_refinement=True, refinement_plan=["найти IP серверов"]

Итерация 2:
- action (targeted): find_relevant_sections → найдены разделы с конфигурацией
- observation: "найдены разделы, нужны конкретные IP"
- refine: needs_refinement=True, refinement_plan=["прочитать section 'Servers'"]

Итерация 3:
- action (targeted): get_section_content → получен полный текст раздела
- observation: "найдены все IP адреса"
- refine: needs_refinement=False
- final: формирование итогового ответа

**Преимущества:**
- ✅ До 3 итераций уточнения вместо одного прохода
- ✅ Автоматическое решение о продолжении на основе completeness
- ✅ Использование targeted tools для точечных уточнений
- ✅ Накопление контекста между итерациями
- ✅ Адаптивность - может остановиться на 1й итерации

**Использование:**
```bash
python rag_lg_agent.py "найди все СУБД и их IP"
# Вывод покажет: Итераций: 2/3, Tools executed: 7
```

**Файлы:**
- ✅ `rag_lg_agent.py` - модифицирован (1032 строки, было 841)
- ✅ `rag_lg_agent.single_pass.backup` - бэкап исходной версии

📖 **Детали:** [doc/RAG_LG_AGENT_ITERATIVE.md](doc/RAG_LG_AGENT_ITERATIVE.md)

---

### 2026-04-26 20:15 - Улучшения multi_term_exact_search

**1. Фильтрация только prose chunks по умолчанию**

Инструмент `multi_term_exact_search` теперь по умолчанию ищет только в обычных чанках (chunk_type="").

**Изменения:**
```python
# Pydantic схема
class MultiTermExactSearchInput(BaseModel):
    terms: list[str]
    chunk_type: str = ""  # ✅ Было: Optional[str] = None

# Функция
def multi_term_exact_search(
    terms: list[str],
    chunk_type: str = "",  # ✅ Было: Optional[str] = None
    ...
)
```

**2. Автоматическая дедупликация терминов**

Если LLM передаёт повторяющиеся термины (например, `['СУБД', 'СУБД', 'СУБД', 'СУБД']`), они автоматически дедуплицируются:

```python
# Дедупликация терминов (удаление повторяющихся)
unique_terms = list(dict.fromkeys(terms))  # Сохраняет порядок
if len(unique_terms) < len(terms):
    logger.warning(
        f"multi_term_exact_search: удалены дубликаты терминов. "
        f"Было: {len(terms)}, стало: {len(unique_terms)}"
    )
```

**Результат:**

**До:**
```python
multi_term_exact_search(terms=['СУБД', 'СУБД', 'СУБД', 'СУБД'])
# Искало 4 раза один и тот же термин
# Возвращало все типы чанков (prose + tables)
```

**После:**
```python
multi_term_exact_search(terms=['СУБД', 'СУБД', 'СУБД', 'СУБД'])
# Автоматически: unique_terms = ['СУБД']
# Логирует warning о дедупликации
# Ищет только в prose chunks
```

**Преимущества:**
- ✅ Нет избыточных поисков по дубликатам терминов
- ✅ Более точный coverage (максимум = количество уникальных терминов)
- ✅ Только prose chunks по умолчанию (не засоряется таблицами)
- ✅ Логирование предупреждений о дубликатах

**Обновлена схема:**
```python
terms: list[str] = Field(
    description="List of UNIQUE substrings... NOTE: Duplicate terms will be automatically removed."
)
```

---

### 2026-04-26 20:00 - Дедупликация чанков и новый инструмент get_chunks_by_index

**1. Дедупликация результатов поиска**

Все функции поиска чанков теперь возвращают **уникальные чанки** по комбинации `(source, section, chunk_index)`.

**Проблема:**
При поиске могли возвращаться дубликаты одного и того же чанка.

**Решение:**
```python
def _deduplicate_chunks(chunks: list[ChunkResult]) -> list[ChunkResult]:
    """Дедупликация по (source, section, chunk_index)"""
    seen = set()
    unique_chunks = []
    for chunk in chunks:
        key = (chunk.metadata.source, chunk.metadata.section, chunk.metadata.chunk_index)
        if key not in seen:
            seen.add(key)
            unique_chunks.append(chunk)
    return unique_chunks

# Применено к функциям
chunks = _deduplicate_chunks(_docs_to_chunk_results(docs))
```

**Применено к:**
- ✅ `semantic_search`
- ✅ `exact_search`
- ✅ `exact_search_in_file`
- ✅ `exact_search_in_file_section`
- ✅ `multi_term_exact_search` (по группам)

**2. Новый инструмент get_chunks_by_index**

Добавлен инструмент для получения конкретных чанков по индексам:

```python
def get_chunks_by_index(
    source: str,              # "servers.md"
    section: str,             # "Database Configuration"
    chunk_indices: list[int]  # [0, 1, 5]
) -> SearchChunksResult
```

**Назначение:**
- Получить конкретные чанки по известным индексам
- Построить контекст из известных позиций
- Дополнить результаты поиска референсными чанками

**Пример:**
```python
# Получить первые 3 чанка раздела
result = get_chunks_by_index(
    source="servers.md",
    section="Database Servers",
    chunk_indices=[0, 1, 2]
)
```

**Преимущества дедупликации:**
- ✅ Нет дубликатов в результатах
- ✅ Точный подсчёт уникальных чанков
- ✅ Меньше токенов в промптах

**Преимущества get_chunks_by_index:**
- ✅ Прямой доступ к чанкам без поиска
- ✅ Быстрое построение контекста
- ✅ Можно дополнить результаты поиска

📖 **Детали:** [doc/CHANGE_DEDUP_AND_GET_BY_INDEX.md](doc/CHANGE_DEDUP_AND_GET_BY_INDEX.md)

**Всего инструментов:** 15 (было 14)

---

### 2026-04-26 19:45 - Фильтрация по типу чанков в semantic_search и exact_search

**Проблема:**
Инструменты `semantic_search` и `exact_search` возвращали **все типы чанков**, включая полные таблицы (`table_full`), что засоряло результаты текстового поиска.

**Решение:**
Изменён **default для `chunk_type`** с `None` (все типы) на `""` (только prose chunks):

```python
# Pydantic схемы
class SemanticSearchInput(BaseModel):
    query: str
    chunk_type: str = ""  # ✅ Новое поле, по умолчанию только prose
    ...

class ExactSearchInput(BaseModel):
    substring: str
    chunk_type: str = ""  # ✅ Было: Optional[str] = None
    ...

# Функции
def semantic_search(query: str, chunk_type: str = "", ...):
    docs = vectorstore.similarity_search(query, chunk_type=chunk_type, ...)

def exact_search(substring: str, chunk_type: str = "", ...):
    docs = vectorstore.exact_search(substring, chunk_type=chunk_type, ...)
```

**Результат:**

| Было | Стало |
|------|-------|
| `semantic_search(query="PostgreSQL")` → prose + table_full + table_row | `semantic_search(query="PostgreSQL")` → **только prose** |
| `exact_search(substring="PostgreSQL")` → prose + table_full + table_row | `exact_search(substring="PostgreSQL")` → **только prose** |

**Типы чанков:**
- `""` (пустая строка) - prose chunks (обычный текст) ← **default**
- `"table_row"` - строки таблиц
- `"table_full"` - полные таблицы

**Для работы с таблицами:**
- `read_table(section="...")` - специальный инструмент для таблиц
- `exact_search(substring="...", chunk_type="table_row")` - явный поиск в таблицах

**Преимущества:**
- ✅ Более релевантные результаты текстового поиска
- ✅ Таблицы не засоряют результаты
- ✅ Меньше токенов в промптах
- ✅ Явный контроль через параметр `chunk_type`

📖 **Детали:** [doc/CHANGE_CHUNK_TYPE_FILTER.md](doc/CHANGE_CHUNK_TYPE_FILTER.md)

---

### 2026-04-26 19:30 - Использование pydantic_to_markdown в messages

**Проблема:**
Pydantic модели сохранялись в messages как JSON через `model_dump_json()`, что было не очень читаемо в логах.

**Решение:**
Используется утилита `pydantic_to_markdown()` из `pydantic_utils.py` для форматирования Pydantic моделей:

```python
from pydantic_utils import pydantic_to_markdown

# В узлах графа
state["messages"].append({
    "role": "assistant",
    "content": pydantic_to_markdown(result)  # ✅ Markdown вместо JSON
})
```

**Результат в логах:**

**Было (JSON):**
```json
{"status": "plan", "step": 1, "thought": "...", "plan": [...]}
```

**Стало (Markdown):**
```markdown
**AgentPlan**
- **status:** plan
- **step:** 1
- **thought:** нужно найти упоминания СУБД
- **plan:** (3 элементов)
  1. поиск по терминам PostgreSQL, MySQL
  2. семантический поиск конфигурации
  3. поиск IP-адресов
```

**Преимущества:**
- ✅ Более читаемый формат для человека
- ✅ Автоматическое сокращение длинных значений
- ✅ Иерархическая структура с отступами
- ✅ Использует существующую утилиту

**Обновлены:**
- `plan_node` - messages
- `action_node` - messages и tool results
- `observation_node` - messages
- `final_node` - messages (но `state["final_answer"]` остаётся JSON для парсинга)

📖 **Детали:** [doc/IMPROVEMENT_PYDANTIC_MARKDOWN.md](doc/IMPROVEMENT_PYDANTIC_MARKDOWN.md)

---

### 2026-04-26 19:15 - Динамический список инструментов из реестра

**Проблема:**
Список доступных инструментов был захардкожен в тексте промптов `plan_node` и `action_node`:
```python
Доступные инструменты:
- semantic_search: семантический поиск...
- exact_search: точный поиск...
```

При добавлении/удалении инструментов нужно было обновлять код в нескольких местах.

**Решение:**
Список инструментов теперь динамически получается из реестра через `get_tool_registry()`:
```python
from kb_tools import create_kb_tools, get_tool_registry

def _format_tools_list() -> str:
    """Форматирует список доступных инструментов из реестра."""
    tool_registry = get_tool_registry()
    lines = ["Доступные инструменты:"]
    for tool_name, description in tool_registry.items():
        lines.append(f"- {tool_name}: {description}")
    return "\n".join(lines)

# В промптах:
system_message = f"""{_SYSTEM_PROMPT}
{_format_tools_list()}
"""
```

**Преимущества:**
- ✅ Единый источник истины (реестр в `kb_tools.py`)
- ✅ Автоматическое обновление при добавлении новых инструментов
- ✅ Невозможна рассинхронизация списка с реальными инструментами
- ✅ Проще поддерживать код

📖 **Детали:** [doc/FIX_DYNAMIC_TOOLS_LIST.md](doc/FIX_DYNAMIC_TOOLS_LIST.md)

---

### 2026-04-26 18:51 - Исправление TypeError при сериализации результатов

**Проблема:**
```
TypeError: Object of type SearchChunksResult is not JSON serializable
```

Инструменты KB могут возвращать Pydantic модели (`SearchChunksResult`), которые не сериализуются напрямую через `json.dumps()`.

**Решение:**
Добавлена проверка типа результата перед сериализацией:
```python
# Конвертируем result в строку (может быть Pydantic модель)
if hasattr(result_raw, "model_dump_json"):
    # Pydantic модель - используем model_dump_json()
    result_str = result_raw.model_dump_json(indent=2)
else:
    # Обычная строка или другой объект
    result_str = str(result_raw)
```

Теперь все результаты корректно конвертируются в строки перед добавлением в messages.

---

## 🎉 Готово!

Агент **`rag_lg_agent.py`** теперь:
- ✅ Делает **один проход** без повторений
- ✅ Использует **system_prompt.md**
- ✅ **Полное логирование** в `logs/_rag_llm.log`:
  - system промпты
  - user messages  
  - assistant responses (JSON)
  - tool calls и results
  - messages history

**Все агенты** теперь логируют в файлы через `logging_config.py`.

---

**Документация:**
- 📖 [doc/RAG_LG_AGENT_V2.md](doc/RAG_LG_AGENT_V2.md) - архитектура агента
- 📖 [doc/RAG_LG_AGENT_REFACTORING.md](doc/RAG_LG_AGENT_REFACTORING.md) - детали рефакторинга
- 📖 [README.md](README.md) - обновлённая таблица режимов

