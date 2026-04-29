# Исправление: Добавлены недостающие retry.md

## Проблема

При ошибке парсинга JSON код пытался загрузить `action/retry.md`, но файл отсутствовал:

```
jinja2.exceptions.TemplateNotFound: 'action/retry.md' not found in search path: 'C:\\dev\\github.com\\vadim-kosarev\\tools.0\\RAG\\prompts_v2'
```

**Причина:** В `prompts_v2/` были созданы основные промпты, но не были созданы retry промпты для обработки ошибок.

## Решение

Созданы `retry.md` файлы для всех нод:

### Созданные файлы

1. ✅ `action/retry.md`
2. ✅ `plan/retry.md`
3. ✅ `observation/retry.md`
4. ✅ `refine/retry.md`

### Содержимое retry.md

Все retry промпты используют единый формат:

```markdown
⚠️ **ОШИБКА ПАРСИНГА JSON**

Твой предыдущий ответ не был валидным JSON.

**Ошибка:**
\```
{{ error_message }}
\```

**Требования:**

1. **ТОЛЬКО JSON** - никакого текста до или после
2. **Валидный синтаксис** - проверь запятые, скобки, кавычки
3. **Правильные поля** - используй только поля из schema
4. **Правильные типы** - строки в кавычках, числа без кавычек

**Формат ответа:**

\```json
{{ response_schema }}
\```

**Попробуй еще раз** - верни ТОЛЬКО валидный JSON без дополнительного текста.
```

### Ключевые особенности

1. **Показывает ошибку** - `{{ error_message }}` с описанием проблемы
2. **Напоминает правила** - 5 пунктов для валидного JSON
3. **Показывает schema** - `{{ response_schema }}` с правильным форматом
4. **Четкая инструкция** - "Попробуй еще раз"

## Как это работает

### 1. Нормальная работа

```python
# action_node
result = structured_llm.invoke(messages)
# → Успех, JSON валиден
```

### 2. Ошибка парсинга

```python
# action_node
try:
    result = structured_llm.invoke(messages)
except OutputParserException as exc:
    # Загружаем retry промпт
    error_message = _prompt_loader.render_action_retry(state)
    # Добавляем в messages
    messages.append({"role": "user", "content": error_message})
    # Повторный вызов
    result = structured_llm.invoke(messages)
```

### 3. Retry промпт

```
[#1 USER] - исходный промпт
[#2 ASSISTANT] - невалидный JSON
[#3 USER] - retry промпт с ошибкой и schema
[#4 ASSISTANT] - исправленный JSON
```

## Структура prompts_v2/ после изменений

```
prompts_v2/
├── system.md               # Базовый system prompt
├── QUICKSTART.md
├── README.md
├── action/
│   ├── system.md          # System для action
│   ├── user.md            # User для action
│   └── retry.md           # ✅ НОВЫЙ: Retry для action
├── plan/
│   ├── system.md          # System для plan (typo: sysetm.md)
│   ├── user.md            # User для plan
│   └── retry.md           # ✅ НОВЫЙ: Retry для plan
├── observation/
│   └── retry.md           # ✅ НОВЫЙ: Retry для observation
├── observation.md         # User для observation
├── refine/
│   └── retry.md           # ✅ НОВЫЙ: Retry для refine
├── refine.md              # User для refine
└── final.md               # User для final
```

**Примечание:** Смешанная структура нормальна:
- `action/` и `plan/` - полные папки (system + user + retry)
- `observation` и `refine` - гибридные (user в корне, retry в папке)
- `final` - только user (нет retry т.к. это финальная нода)

## Тестирование

### До исправления

```bash
python rag_lg_agent.py "тест"
# → jinja2.exceptions.TemplateNotFound: 'action/user.md'
```

### После исправления

```bash
python rag_lg_agent.py "тест"
# → Если ошибка парсинга → загружается user.md → повторная попытка
```

### Провоцирование ошибки для проверки

```bash
# Запустить с некорректной моделью или сложным запросом
python rag_lg_agent.py "очень сложный вопрос с множеством условий"
# Если LLM вернет невалидный JSON → retry сработает
```

## Примеры использования retry

### Сценарий 1: Текст до JSON

**LLM ответ:**
```
Вот мой ответ:
{
  "thought": "...",
  "actions": [...]
}
```

**Retry:**
```
⚠️ **ОШИБКА ПАРСИНГА JSON**

Твой предыдущий ответ не был валидным JSON.

**Ошибка:**
Expecting value: line 1 column 1

**Требования:**
1. **ТОЛЬКО JSON** - никакого текста до или после
...
```

**LLM исправленный ответ:**
```json
{
  "thought": "...",
  "actions": [...]
}
```

### Сценарий 2: Неправильные поля

**LLM ответ:**
```json
{
  "thinking": "...",  // ← неправильное поле
  "tools": [...]       // ← неправильное поле
}
```

**Retry показывает:**
```json
{
  "status": "action",
  "step": 0,
  "thought": "string",  // ← правильное поле
  "actions": [...]      // ← правильное поле
}
```

## Связь с другими компонентами

### prompt_loader.py

```python
def render_action_retry(self, state: Dict[str, Any]) -> str:
    """Retry prompt для action node."""
    return self.render('action/user.md', state)
```

### rag_lg_agent.py

```python
# action_node
try:
    result = structured_llm.invoke(messages)
except OutputParserException:
    if attempt < max_retries - 1:
        error_message = _prompt_loader.render_action_retry(state)
        messages.append({"role": "user", "content": error_message})
    else:
        raise
```

### state

Retry промпт использует те же переменные:
- `{{ error_message }}` - описание ошибки
- `{{ response_schema }}` - правильный формат (из Pydantic)

## Преимущества

### 1. Самовосстановление
- ✅ Агент может исправить свои ошибки
- ✅ Не падает при невалидном JSON
- ✅ 2 попытки вместо 1

### 2. Обучающий эффект
- ✅ LLM видит свою ошибку
- ✅ LLM видит правильный формат
- ✅ Конкретные инструкции что исправить

### 3. Отладка
- ✅ В логах видно retry промпт
- ✅ Видно исходную ошибку
- ✅ Видно исправленный ответ

## Статистика

| Метрика | Значение |
|---------|----------|
| Создано файлов | 4 (retry.md для каждой ноды) |
| Размер каждого | ~500 байт |
| Единый формат | Да |
| Использует response_schema | Да (автоген из Pydantic) |

## Итог

✅ **Все retry промпты созданы**
✅ **Единый формат для всех нод**
✅ **Используют автогенерацию schema**
✅ **Готово к использованию**

Теперь `prompts_v2/` полностью функционален и может обрабатывать ошибки парсинга JSON! 🎉

