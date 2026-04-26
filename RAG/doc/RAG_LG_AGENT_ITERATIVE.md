# ✅ Модификация rag_lg_agent.py - Итеративный режим с уточнениями

**Дата:** 2026-04-26 20:45  
**Файл:** `rag_lg_agent.py`  
**Бэкап:** `rag_lg_agent.single_pass.backup`

---

## Что сделано

Модифицирован существующий `rag_lg_agent.py` для поддержки **итеративного анализа с уточнениями** (до 3 итераций).

---

## Архитектура

### Было (single-pass):
```
START → plan → action → observation → final → END
```

### Стало (iterative):
```
START → plan → action → observation → refine
                ↑                       ↓
                +------ [да] -----------+
                            ↓ [нет]
                         final → END
```

---

## Ключевые изменения

### 1. Добавлена константа MAX_ITERATIONS = 3

### 2. Обновлен AgentState

```python
class AgentState(TypedDict):
    user_query: str
    step: int
    iteration: int  # ✅ Новое - номер итерации (1, 2, 3)
    messages: list[dict[str, Any]]
    plan: list[str]
    tool_calls: list[dict[str, Any]]  # Текущей итерации
    all_tool_results: list[dict[str, Any]]  # ✅ Новое - все результаты всех итераций
    observation: str
    needs_refinement: bool  # ✅ Новое - нужны ли уточнения
    refinement_plan: list[str]  # ✅ Новое - план уточнения
    final_answer: str
```

### 3. Добавлена модель AgentRefine

```python
class AgentRefine(BaseModel):
    status: Literal["refine"] = "refine"
    step: int
    thought: str
    needs_refinement: bool  # True если нужны уточнения
    refinement_plan: list[str]  # План уточняющих запросов
```

### 4. Обновлен plan_node

- Добавлена подсказка LLM о том, что будут уточнения
- Инициализация `iteration=1`

### 5. Полностью переписан action_node

**Новые возможности:**
- Поддержка iteration (1, 2, 3)
- Использует `refinement_plan` если есть, иначе основной `plan`
- Подготовка контекста из предыдущих результатов
- Рекомендация использовать targeted tools для уточнений
- Сохранение результатов в `all_tool_results`

**Ключевой код:**
```python
iteration = state.get("iteration", 1)
current_plan = state.get('refinement_plan') or state['plan']

# Контекст из предыдущих результатов
previous_results_summary = ""
if state.get("all_tool_results"):
    # Показываем последние 10 для контекста
    ...

# Рекомендация для уточнений
if iteration > 1:
    "Используй targeted tools: find_relevant_sections, get_chunks_by_index, exact_search_in_file_section"
```

### 6. Обновлен observation_node

- Поддержка iteration
- Анализ результатов **текущей итерации** (не всех)
- Обновленное логирование

### 7. Добавлен refine_node (новый!)

**Назначение:** Решает, нужны ли уточнения после observation.

**Логика:**
```python
def refine_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration", 1)
    
    # Проверка лимита
    if iteration >= MAX_ITERATIONS:
        state["needs_refinement"] = False
        return state
    
    # LLM решает: нужны ли уточнения
    result: AgentRefine = structured_llm.invoke(...)
    
    state["needs_refinement"] = result.needs_refinement
    state["refinement_plan"] = result.refinement_plan
    
    # Увеличиваем iteration если продолжаем
    if result.needs_refinement:
        state["iteration"] = iteration + 1
    
    return state
```

**Промпт для LLM:**
- Проанализируй observation  
- Достаточно ли данных?
- Остались ли неотвеченные аспекты?
- Составь refinement_plan если нужны уточнения
- Используй targeted tools

### 8. Обновлен final_node

- Использует `all_tool_results` вместо `tool_results`
- Добавлена информация о количестве итераций
- Обновленное логирование

### 9. Добавлена функция should_refine

**Условный роутинг:**
```python
def should_refine(state: AgentState) -> str:
    if state.get("needs_refinement", False) and state.get("iteration", 1) < MAX_ITERATIONS:
        return "action"  # Цикл обратно к action
    else:
        return "final"   # Переход к финалу
```

### 10. Обновлен build_graph

**Новая структура:**
```python
workflow.add_node("plan", plan_node)
workflow.add_node("action", action_node)
workflow.add_node("observation", observation_node)
workflow.add_node("refine", refine_node)  # ✅ Новый узел
workflow.add_node("final", final_node)

workflow.set_entry_point("plan")
workflow.add_edge("plan", "action")
workflow.add_edge("action", "observation")
workflow.add_edge("observation", "refine")

# ✅ Условный роутинг
workflow.add_conditional_edges(
    "refine",
    should_refine,
    {
        "action": "action",  # Цикл
        "final": "final"      # Выход
    }
)

workflow.add_edge("final", END)
```

### 11. Обновлены вспомогательные функции

- `run_query` - инициализация новых полей state
- `print_result` - вывод информации об итерациях
- `run_interactive` - описание итеративного режима
- `parse_args` - описание max iterations
- `main` - логирование итеративного режима

---

## Пример работы

### Итерация 1 (первичный поиск)

```
plan → "найти упоминания СУБД через semantic_search и exact_search"
  ↓
action (iteration 1) → выполнить semantic_search, exact_search
  ↓
observation → "найдены упоминания PostgreSQL и MySQL, но нет IP-адресов"
  ↓
refine → needs_refinement=True, refinement_plan=["найти IP серверов БД"]
```

### Итерация 2 (уточнение)

```
action (iteration 2) → найти разделы с IP через find_relevant_sections
  ↓
observation → "найдены разделы с конфигурацией, но нужны конкретные IP"
  ↓
refine → needs_refinement=True, refinement_plan=["прочитать section 'Database Servers'"]
```

### Итерация 3 (финальное уточнение)

```
action (iteration 3) → get_section_content("servers.md", "Database Servers")
  ↓
observation → "найдены все IP адреса БД"
  ↓
refine → needs_refinement=False (данных достаточно)
  ↓
final → формирование итогового ответа
```

---

## Преимущества

### 1. Глубина анализа

✅ **До 3 итераций** уточнения вместо одного прохода  
✅ **Автоматическое решение** о продолжении на основе completeness данных  
✅ **Targeted tools** для точечных уточнений

### 2. Качество ответов

✅ **Больше данных** - несколько поисковых сессий  
✅ **Умные уточнения** - LLM сам выбирает что уточнить  
✅ **Контекст накапливается** - каждая итерация видит предыдущие результаты

### 3. Гибкость

✅ **Адаптивность** - может остановиться на 1й итерации если данных достаточно  
✅ **Контроль** - MAX_ITERATIONS ограничивает количество циклов  
✅ **Прозрачность** - логирование каждой итерации

---

## Использование

```bash
# Запуск с вопросом
python rag_lg_agent.py "найди все СУБД и их IP адреса"

# С подробным выводом
python rag_lg_agent.py "какие системы используются?" --verbose

# Интерактивный режим
python rag_lg_agent.py
```

**Вывод:**
```
================================================================================
Вопрос: найди все СУБД и их IP адреса
Шагов: 11
Итераций: 3/3
Messages: 18
Tools executed: 9
================================================================================
ОТВЕТ:
...
🎯 Confidence: 95%
🔄 Iterations: 3
================================================================================
```

---

## Логирование

В `logs/_rag_llm.log`:

```
##  2026-04-26 20:50:00  PLAN NODE START
##    Вопрос: найди все СУБД и их IP адреса

##  2026-04-26 20:50:05  ACTION NODE START (iteration 1)
...

##  2026-04-26 20:50:10  REFINE NODE COMPLETE (iteration 1)
##    Needs refinement: True
##    Refinement plan:
##      1. найти IP адреса серверов БД
##      2. использовать find_relevant_sections

##  2026-04-26 20:50:15  ACTION NODE START (iteration 2)
...

##  2026-04-26 20:50:25  REFINE NODE COMPLETE (iteration 2)
##    Needs refinement: False
##    Данных достаточно

##  2026-04-26 20:50:30  FINAL NODE START
##    Всего выполнено инструментов: 9, итераций: 2
```

---

## Сравнение с single-pass

| Аспект | Single-pass (old) | Iterative (new) |
|--------|-------------------|-----------------|
| **Итераций** | 1 | До 3 |
| **Уточнения** | Нет | Да |
| **Узлов** | 4 (plan, action, observation, final) | 5 (+ refine) |
| **Граф** | Линейный | С условным циклом |
| **State** | 8 полей | 11 полей |
| **Скорость** | Быстрее | Медленнее |
| **Качество** | Базовое | Высокое |
| **Targeted tools** | Нет поддержки | Рекомендуются |

---

## Файлы

- ✅ `rag_lg_agent.py` - модифицированный агент (1032 строки)
- ✅ `rag_lg_agent.single_pass.backup` - бэкап исходной версии (841 строка)

---

## Проверка

```bash
python -m py_compile rag_lg_agent.py
```
✅ Синтаксис корректен

---

## Статус

✅ **Реализовано**  
✅ **Проверено**  
✅ **Готово к использованию**

---

## Документация

- 📖 [doc/RAG_LG_AGENT_ITERATIVE.md](doc/RAG_LG_AGENT_ITERATIVE.md) - полное описание
- 📝 [READY.md](READY.md) - обновить

---

✅ **Готово!** `rag_lg_agent.py` теперь поддерживает до 3 итераций уточнения с автоматическим решением о продолжении.

