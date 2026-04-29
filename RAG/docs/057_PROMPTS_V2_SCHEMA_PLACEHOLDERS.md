# Обновление prompts_v2: JSON Schema через Placeholder

## Проблема

В `prompts_v2/` хардкод JSON дублировал Pydantic модели, несмотря на то что мы уже реализовали генератор schema.

**Было в prompts_v2/system.md:**
```markdown
## Формат ответа

**ВСЕГДА возвращай JSON:**

```json
{
  "thought": "краткое рассуждение",
  "plan": ["шаг 1", "шаг 2"],
  "actions": [{"tool": "tool_name", "input": {...}}],
  "observation": "анализ результатов",
  "needs_more_data": true/false,
  "final_answer": {
    "summary": "краткий ответ",
    "details": "подробности",
    "sources": ["файл.md > раздел"]
  }
}
\```
```

❌ **Проблемы:**
- Общий формат для всех нод (не специфичный)
- Хардкод дублирует Pydantic модели
- Two sources of truth

## Решение

### 1. Удален общий формат из system.md

Раздел "Формат ответа" удален из `system.md`, т.к. каждая нода должна показывать свой специфичный формат.

**Обновленный system.md:**
```markdown
# System Prompt для RAG-агента работы с документацией

Ты — RAG-агент для работы с технической документацией.

## Данные
Документация проиндексирована в **ClickHouse** и доступна в виде инструментов...

## Задача
Отвечать на вопросы пользователя о содержимом документации...

## Принцип работы
plan → action → observation → refine → action → ... → final

## Инструменты
{{ available_tools }}

## Правила
1. Не выдумывай — только данные из документации
2. Указывай источники — файл и раздел
3. Используй инструменты — не отвечай без поиска
4. Будь точным — проверяй факты
5. JSON только — никакого текста вне JSON
```

### 2. Заменен хардкод на placeholder во всех нодах

#### plan/user.md

**Было:**
```markdown
### Формат ответа

```json
{
  "thought": "краткий анализ вопроса и выбор стратегии",
  "plan": [
    "шаг 1: [инструмент] — что искать",
    "шаг 2: [инструмент] — уточнение",
    "шаг 3: формирование ответа"
  ]
}
\```
```

**Стало:**
```markdown
### Формат ответа

{{ response_schema }}
```

#### action.md

**Было:**
```markdown
### Формат ответа

```json
{
  "thought": "какие инструменты вызываю и зачем",
  "actions": [
    {
      "tool": "semantic_search",
      "input": { "query": "...", "top_k": 10 }
    }
  ]
}
\```
```

**Стало:**
```markdown
### Формат ответа

{{ response_schema }}
```

#### observation.md, refine.md, final.md

Аналогично - хардкод JSON заменен на `{{ response_schema }}`.

## Обновленные файлы

### prompts_v2/

- ✅ `system.md` - удален раздел "Формат ответа"
- ✅ `plan/user.md` - заменен JSON на placeholder
- ✅ `action.md` - заменен JSON на placeholder
- ✅ `observation.md` - заменен JSON на placeholder
- ✅ `refine.md` - заменен JSON на placeholder
- ✅ `final.md` - заменен JSON на placeholder

## Как это работает

### 1. В коде (rag_lg_agent.py)

Каждая нода добавляет специфичную schema в state:

```python
# plan_node
state['response_schema'] = get_plan_schema()

# action_node
state['response_schema'] = get_action_schema()

# observation_node
state['response_schema'] = get_observation_schema()

# refine_node
state['response_schema'] = get_refine_schema()

# final_node
state['response_schema'] = get_final_schema()
```

### 2. Генератор (schema_generator.py)

Автоматически создает schema из Pydantic моделей:

```python
def get_plan_schema() -> str:
    """JSON schema для plan ноды."""
    from rag_lg_agent import AgentPlan
    return generate_schema_for_prompt(AgentPlan)

# Результат:
```json
{
  "status": "plan",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение (1-2 предложения)
  "plan": [
    "string"
  ]  // Список шагов плана поиска (3-5 пунктов)
}
\```
```

### 3. В промпте (plan/user.md)

Placeholder заменяется на сгенерированную schema:

```markdown
### Формат ответа

{{ response_schema }}  ← Заменится на JSON с комментариями
```

## Преимущества

### 1. Single Source of Truth
- ✅ Pydantic модель - единственный источник
- ✅ Изменил модель → все промпты обновились
- ✅ Нет дублирования

### 2. Специфичность
- ✅ Каждая нода показывает свой формат
- ✅ Нет путаницы с полями других нод
- ✅ Точные descriptions для каждого поля

### 3. Автоматизация
- ✅ Не нужно обновлять промпты вручную
- ✅ Constraints видны автоматически
- ✅ Меньше ошибок

### 4. Читаемость
- ✅ JSON с инлайн комментариями
- ✅ Descriptions из Field()
- ✅ Примеры остались в промптах

## Пример сгенерированного промпта

### Plan нода

**Промпт (plan/user.md):**
```markdown
### Формат ответа

{{ response_schema }}

**НЕ используй** поля `actions`, `observation`, `final_answer` на этом этапе.
```

**После рендеринга:**
```markdown
### Формат ответа

```json
{
  "status": "plan",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение (1-2 предложения)
  "plan": [
    "string"
  ]  // Список шагов плана поиска (3-5 пунктов)
}
\```

**НЕ используй** поля `actions`, `observation`, `final_answer` на этом этапе.
```

## Использование

### Переключение на prompts_v2

```bash
# .env
PROMPTS_DIR=prompts_v2

# Запуск
python rag_lg_agent.py "вопрос"
```

### Проверка schema

```bash
# Смотрим сгенерированные schema
python schema_generator.py

# Проверяем в логах
python rag_lg_agent.py --steps 1 "тест"
cat logs/001_llm_plan_request.log
```

## Сравнение prompts vs prompts_v2

| Аспект | prompts/ | prompts_v2/ |
|--------|----------|-------------|
| Структура | plan/system.md + plan/user.md | plan/user.md (включает system) |
| JSON формат | Частично placeholder | Полностью placeholder |
| Размер system | 30 строк | 30 строк (без общего JSON) |
| Специфичность | Высокая | Очень высокая |
| Примеры | В некоторых | Во всех |

## Следующие шаги

### Если prompts_v2 работает хорошо

1. A/B тестирование обеих версий
2. Сравнение качества ответов
3. Миграция на prompts_v2 как основную
4. Архивация prompts/ → prompts_legacy/

### Потенциальные улучшения

- [ ] Добавить больше примеров в prompts_v2
- [ ] Унифицировать структуру (всё через include)
- [ ] Создать prompts_v2/README.md с описанием
- [ ] Few-shot примеры для сложных кейсов

## Итог

✅ **prompts_v2/ полностью использует schema_generator**
✅ **Нет дублирования JSON**
✅ **Single source of truth - Pydantic модели**
✅ **Специфичные schema для каждой ноды**

Теперь обе версии промптов (prompts/ и prompts_v2/) используют автоматическую генерацию JSON schema из Pydantic моделей! 🎉

