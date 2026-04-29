# Упрощение графа: Прямая линия без циклов

## Что изменено

### Было (итеративный подход)

```
plan → action → observation → refine → (условие) → action/final
                                   ↑                    |
                                   └────────────────────┘
```

- **Ноды:** plan, action, observation, refine, final
- **Циклы:** refine могла вернуть в action для доп. поиска
- **MAX_ITERATIONS:** ограничение на количество циклов
- **Условный роутинг:** `should_refine()` функция

### Стало (прямая линия)

```
plan → action → observation → final
```

- **Ноды:** plan, action, observation, final
- **Без циклов:** прямой flow
- **Без ограничений:** нет MAX_ITERATIONS
- **Без условий:** нет conditional edges

## Изменения в коде

### 1. Граф (build_graph)

**Было:**
```python
workflow.add_node("plan", plan_node)
workflow.add_node("action", action_node)
workflow.add_node("observation", observation_node)
workflow.add_node("refine", refine_node)
workflow.add_node("final", final_node)

workflow.add_edge("observation", "refine")

workflow.add_conditional_edges(
    "refine",
    should_refine,
    {
        "action": "action",
        "final": "final"
    }
)
```

**Стало:**
```python
workflow.add_node("plan", plan_node)
workflow.add_node("action", action_node)
workflow.add_node("observation", observation_node)
workflow.add_node("final", final_node)

workflow.add_edge("observation", "final")  # Прямо к финалу
```

### 2. Убрана функция should_refine

```python
# УДАЛЕНО
def should_refine(state: AgentState) -> str:
    if state.get("needs_refinement", False) and state.get("iteration", 1) < MAX_ITERATIONS:
        return "action"
    else:
        return "final"
```

### 3. Добавлена самооценка в FinalAnswer

```python
class FinalAnswer(BaseModel):
    summary: str
    details: str
    data: list[FinalAnswerData] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    recommendations: list[RecommendedSection] = Field(default_factory=list)
    self_assessment: str = Field(  # ← НОВОЕ
        description="Самооценка агента: насколько полно и точно удалось ответить на вопрос, что можно было бы улучшить"
    )
```

### 4. Обновлены логи

**Было:**
```python
logger.info(
    f"Запуск Iterative RAG-агента (max {MAX_ITERATIONS} iterations)\n"
    ...
)
```

**Стало:**
```python
logger.info(
    f"Запуск RAG-агента (прямой flow: plan → action → observation → final)\n"
    ...
)
```

## Преимущества

### 1. Простота

- ✅ Линейный flow легче понять
- ✅ Нет условной логики
- ✅ Предсказуемый результат

### 2. Скорость

- ✅ Всегда 4 ноды (было: 4-20+ нод)
- ✅ Нет повторных вызовов LLM
- ✅ Быстрее получение ответа

### 3. Токены

- ✅ Меньше токенов (1 проход вместо N)
- ✅ Меньше вызовов API
- ✅ Дешевле

### 4. Отладка

- ✅ Легче отлаживать (фиксированный flow)
- ✅ Понятные логи
- ✅ Нет циклов

## Недостатки (trade-offs)

### 1. Нет уточнений

- ❌ Если одного поиска мало → ответ неполный
- ❌ Не может попросить больше данных
- ❌ Одна попытка

### 2. Фиксированное качество

- ❌ Качество зависит от первого action
- ❌ Не адаптируется к сложности вопроса

## Когда использовать

### Прямая линия подходит

- ✅ Простые вопросы ("что такое X?")
- ✅ Поиск определений
- ✅ Факты из документации
- ✅ Скорость важнее полноты

### Итеративный подход лучше

- ❌ Сложные многошаговые запросы
- ❌ Нужно собирать данные из разных источников
- ❌ Требуется уточнение на лету
- ❌ Качество важнее скорости

## Самооценка агента

Добавлено новое поле `self_assessment` в финальный ответ:

```json
{
  "final_answer": {
    "summary": "...",
    "details": "...",
    "sources": [...],
    "confidence": 0.9,
    "self_assessment": "Смог ответить полностью, найдены все ключевые источники. Можно было бы добавить примеры использования."
  }
}
```

### Что оценивает агент

1. **Полнота** - удалось ли найти всю информацию
2. **Точность** - насколько точен ответ
3. **Источники** - достаточно ли источников
4. **Улучшения** - что можно было бы сделать лучше

### Польза самооценки

- ✅ Понимание ограничений ответа
- ✅ Идеи для улучшения промптов
- ✅ Метрика качества работы агента
- ✅ Обратная связь для разработчика

## Миграция

### Если нужен старый подход

1. Восстановить refine_node
2. Добавить back conditional edges
3. Вернуть should_refine функцию
4. Раскомментировать MAX_ITERATIONS логику

### Или использовать гибрид

```python
# Опция в конфиге
ENABLE_REFINEMENT = False  # True для итераций

if ENABLE_REFINEMENT:
    workflow.add_edge("observation", "refine")
    workflow.add_conditional_edges(...)
else:
    workflow.add_edge("observation", "final")
```

## Тестирование

### До изменений

```bash
python rag_lg_agent.py "вопрос"

# Flow:
# plan → action → observation → refine → action → observation → refine → final
# ~10-20 секунд, 2-3 итерации
```

### После изменений

```bash
python rag_lg_agent.py "вопрос"

# Flow:
# plan → action → observation → final
# ~5-10 секунд, 1 проход
```

### Проверка самооценки

```bash
python rag_lg_agent.py "что такое СОИБ КЦОИ"

# В финальном ответе должно быть:
# {
#   "final_answer": {
#     ...
#     "self_assessment": "Найдено точное определение в нескольких источниках..."
#   }
# }
```

## Связанные изменения

- `prompts_v2/final.md` - нужно обновить чтобы включить инструкции по самооценке
- `schema_generator.py` - автоматически сгенерирует новую schema с self_assessment
- Логи агента - упрощены, без упоминания iterations

## Обновление промптов

Нужно обновить final промпт чтобы явно запросить самооценку:

```markdown
## Финальный ответ

Сформируй ответ включая:
- summary - краткий ответ
- details - подробное объяснение
- sources - источники
- confidence - уверенность 0-1
- **self_assessment** - твоя оценка: насколько полно ответил, что можно было бы улучшить
```

## Итог

✅ **Упрощен граф до прямой линии**
✅ **Убраны циклы и условия**
✅ **Добавлена самооценка агента**
✅ **Быстрее и проще**
✅ **Готово к тестированию**

Прямая линия: **plan → action → observation → final** 🎉

