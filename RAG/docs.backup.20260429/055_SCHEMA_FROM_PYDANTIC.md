# JSON Schema из Pydantic моделей для промптов

## Проблема

В промптах хардкод JSON schema дублировал Pydantic модели:

**В коде:**
```python
class AgentPlan(BaseModel):
    status: Literal["plan"] = "plan"
    thought: str = Field(description="Краткое рассуждение")
    plan: list[str] = Field(description="Список шагов")
```

**В промпте:**
```markdown
## Формат ответа
```json
{
  "thought": "краткий анализ",
  "plan": ["шаг 1", "шаг 2"]
}
\```
```

**Проблемы:**
- ❌ Дублирование (two sources of truth)
- ❌ При изменении модели нужно менять промпт вручную
- ❌ Легко забыть синхронизировать
- ❌ Не видно descriptions из Field()

## Решение

JSON schema генерируется автоматически из Pydantic моделей и передается через state.

### Архитектура

```
Pydantic модель (AgentPlan) 
    ↓
schema_generator.py (generate_json_example)
    ↓
state['response_schema'] = get_plan_schema()
    ↓
промпт: {{ response_schema }}
    ↓
LLM видит актуальную schema с descriptions
```

## Реализация

### 1. schema_generator.py

Новый модуль для генерации JSON schema из Pydantic моделей:

```python
def generate_json_example(model: Type[BaseModel]) -> str:
    """
    Генерирует пример JSON с описаниями из Pydantic модели.
    
    Returns:
        ```json
        {
          "field1": "value",  // description
          "field2": 0  // description with constraints
        }
        ```
    """
```

**Функции:**
- `generate_json_example(model)` - генерация примера с комментариями
- `get_plan_schema()` - schema для plan ноды
- `get_action_schema()` - schema для action ноды
- `get_observation_schema()` - schema для observation ноды
- `get_refine_schema()` - schema для refine ноды
- `get_final_schema()` - schema для final ноды

### 2. Обновлены ноды (rag_lg_agent.py)

Все ноды добавляют `response_schema` в state:

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

### 3. Обновлены промпты

**Было (prompts/plan/system.md):**
```markdown
## Формат ответа (ТОЛЬКО JSON!)

```json
{
  "thought": "краткий анализ вопроса",
  "plan": [
    "шаг 1: какой инструмент использовать",
    "шаг 2: что искать",
    "шаг 3: как обработать результат"
  ]
}
\```
```

**Стало:**
```markdown
## Формат ответа (ТОЛЬКО JSON!)

{{ response_schema }}
```

## Примеры сгенерированных schema

### Plan Schema

```json
{
  "status": "plan",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение (1-2 предложения)
  "plan": [
    "string"
  ]  // Список шагов плана поиска (3-5 пунктов)
}
```

### Action Schema

```json
{
  "status": "action",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение
  "actions": [
    {
      "tool": "string",  // Имя инструмента
      "input": {}  // Параметры инструмента
    }
  ]  // Список вызовов инструментов (2-4 штуки для параллельного выполнения)
}
```

### Final Schema

```json
{
  "status": "final",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение
  "final_answer": {
    "summary": "string",  // Краткий ответ
    "details": "string",  // Подробное объяснение
    "data": [],  // Структурированные данные
    "sources": [],  // Источники
    "confidence": 0.0,  // Уверенность (0-1) (>=0.0, <=1.0)
    "recommendations": []  // Рекомендованные разделы документации для дальнейшего изучения
  }  // Итоговый ответ
}
```

## Преимущества

### 1. Single Source of Truth
- ✅ Pydantic модель - единственный источник истины
- ✅ Промпты автоматически синхронизированы
- ✅ Изменил модель → промпт обновился автоматически

### 2. Полная информация
- ✅ Descriptions из `Field(description=...)`
- ✅ Constraints (`ge=`, `le=`, `min_length=`, etc.)
- ✅ Default значения
- ✅ Вложенные модели

### 3. Читаемость
- ✅ JSON с инлайн комментариями
- ✅ Понятные примеры значений
- ✅ Структурированный формат

### 4. Поддержка
- ✅ Легко добавить новое поле в модель
- ✅ Не нужно обновлять N промптов вручную
- ✅ Меньше ошибок синхронизации

## Использование

### Добавление нового поля в модель

**Было:**
1. Добавить поле в Pydantic модель
2. Обновить JSON в 5-10 промптах вручную
3. Проверить что нигде не забыли

**Стало:**
1. Добавить поле в Pydantic модель
2. Готово! Schema обновится автоматически

### Пример

Добавим новое поле `confidence` в AgentPlan:

```python
class AgentPlan(BaseModel):
    status: Literal["plan"] = "plan"
    thought: str = Field(description="Краткое рассуждение")
    plan: list[str] = Field(description="Список шагов")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Уверенность в плане"
    )  # <- Новое поле
```

**Результат в промпте:**
```json
{
  "status": "plan",
  "step": 0,
  "thought": "string",
  "plan": ["string"],
  "confidence": 0.0  // Уверенность в плане (>=0.0, <=1.0)
}
```

Автоматически появилось в промпте с описанием и constraints!

## Тестирование

```bash
# Генерация schema для всех нود
python schema_generator.py

# Вывод:
# === PLAN SCHEMA ===
# ...JSON с комментариями...
# === ACTION SCHEMA ===
# ...JSON с комментариями...
# и т.д.
```

### Проверка в промптах

```bash
# Запустить агента с отладкой
python rag_lg_agent.py --steps 1 "тест"

# Проверить логи
cat logs/001_llm_plan_request.log

# Должны увидеть сгенерированную schema в промпте
```

## Технические детали

### Генерация примеров значений

schema_generator умеет генерировать примеры для:

- **Простые типы:** `str` → `"string"`, `int` → `0`, `float` → `0.0`, `bool` → `False`
- **Literal:** `Literal["plan"]` → `"plan"`
- **list[]:** `list[str]` → `["string"]`
- **dict[]:** `dict[str, Any]` → `{}`
- **Вложенные модели:** рекурсивно генерирует структуру
- **Optional:** использует тип не-None значения
- **default/default_factory:** использует реальное значение

### Комментарии

Комментарии генерируются из:
- `Field(description="...")` - основное описание
- `ge=`, `le=`, `gt=`, `lt=` - constraints для чисел
- `min_length=`, `max_length=` - constraints для строк

Формат: `"field": value  // description (constraints)`

## Ограничения

### 1. Encoding в PowerShell
Кириллические комментарии могут отображаться неправильно в PowerShell (кракозябры).
**Решение:** Используйте `| Out-String` или смотрите в файлах логов.

### 2. default_factory
Для полей с `default_factory=list` нужна дополнительная обработка.
**Status:** Работает, но отображается как `[]` без примера элемента.

### 3. Union типы
Сложные Union типы отображаются упрощенно.
**Workaround:** Использовать Literal или отдельные модели.

## Связанные изменения

В той же сессии:
- Убран лишний слой `prompts.py` (053_REFACTOR_REMOVE_PROMPTS_LAYER.md)
- Путь к промптам вынесен в .env (054_CONFIG_PROMPTS_DIR.md)
- Увеличен MAX_ITERATIONS до 5
- Упрощены промпты

## Дальнейшие улучшения

- [ ] Улучшить генерацию примеров для `default_factory`
- [ ] Добавить поддержку Union типов
- [ ] Генерировать full JSON Schema (не только примеры)
- [ ] Опция для генерации без комментариев (чистый JSON)
- [ ] Кэширование сгенерированных schema

## Итог

**Хардкод → Генерация из моделей**

```
Было:
Pydantic модель ← → (нужно синхронизировать вручную) ← → JSON в промпте

Стало:
Pydantic модель → [schema_generator] → state['response_schema'] → {{ response_schema }} в промпте
```

**Single source of truth** - меняем модель, промпты обновляются автоматически! 🎉

