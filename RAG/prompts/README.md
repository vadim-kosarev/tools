# Система промптов на основе Jinja2

## Обзор

Промпты RAG-агента теперь хранятся в отдельных `.md` файлах с использованием **Jinja2** шаблонов.

### Преимущества

✅ **Markdown разметка** - красиво отображается в редакторе  
✅ **Видимые placeholders** - `{{ variable }}` сразу видны в файле  
✅ **Включение файлов** - `{% include 'other.md' %}` для переиспользования  
✅ **Условия и циклы** - `{% if %}`, `{% for %}` для динамических промптов  
✅ **Централизованное хранилище** - все в папке `prompts/`  
✅ **Version control friendly** - изменения промптов видны в git diff  

---

## Структура файлов

```
RAG/
├── prompts/                         # Папка с промптами
│   ├── system_prompt_base.md        # Базовый системный промпт
│   ├── plan/
│   │   ├── system.md                # System message для plan node
│   │   ├── user.md                  # User message для plan node
│   │   └── retry.md                 # Retry message при ошибках
│   ├── action/
│   │   ├── system.md
│   │   ├── user.md
│   │   └── retry.md
│   ├── observation/
│   │   ├── system.md
│   │   ├── user.md
│   │   └── retry.md
│   ├── refine/
│   │   ├── system.md
│   │   ├── user.md
│   │   └── retry.md
│   └── final/
│       ├── system.md
│       └── user.md
├── prompt_loader.py                 # Утилита для загрузки промптов
└── prompts.py                       # API для обратной совместимости
```

---

## Использование

### Базовый пример

```python
from prompt_loader import get_loader

# Получить загрузчик
loader = get_loader()

# Подготовить state с переменными
state = {
    'user_query': 'Что такое ППРК?',
    'iteration': 1,
    'MAX_ITERATIONS': 3,
    'step': 1,
    'plan': ['шаг 1', 'шаг 2', 'шаг 3']
}

# Загрузить и отрендерить промпт
system_prompt = loader.render('plan/system.md', state)
user_prompt = loader.render('plan/user.md', state)

# Или использовать удобные методы
system_prompt = loader.render_plan_system(state)
user_prompt = loader.render_plan_user(state)
```

### Через prompts.py (совместимость с rag_lg_agent.py)

```python
import prompts

state = {
    'user_query': 'Что такое ППРК?',
    'iteration': 1,
    'MAX_ITERATIONS': 3,
    ...
}

# Все функции принимают state как параметр
system_prompt = prompts.get_plan_system_prompt(state)
user_prompt = prompts.get_plan_user_prompt(state)
```

---

## Jinja2 Синтаксис

### Переменные

```jinja2
Вопрос пользователя: {{ user_query }}

Текущая итерация: {{ iteration }}/{{ MAX_ITERATIONS }}
```

### Включение файлов

```jinja2
{% include 'system_prompt_base.md' %}

ТЕКУЩИЙ ЭТАП: plan
...
```

### Условия

```jinja2
{% if iteration > 1 %}
Это уточняющий поиск.
{% else %}
Это первичный поиск.
{% endif %}
```

### Циклы

```jinja2
План поиска:
{% for step in plan %}
{{ loop.index }}. {{ step }}
{% endfor %}
```

### Фильтры

```jinja2
Найдено результатов: {{ all_tool_results|length }}
```

---

## Редактирование промптов

### 1. Найти нужный файл

- **Системный промпт**: `prompts/system_prompt_base.md`
- **Plan node**: `prompts/plan/system.md`, `prompts/plan/user.md`
- **Action node**: `prompts/action/system.md`, `prompts/action/user.md`
- И т.д.

### 2. Редактировать в IDE

Открыть файл в любом редакторе с поддержкой Markdown. Placeholders `{{ variable }}` будут подсвечены.

### 3. Изменения применяются автоматически

При следующем запуске RAG-агента промпт будет загружен из обновленного файла. Кеширование отключено для упрощения разработки.

---

## Доступные переменные (state)

### Общие для всех промптов

- `user_query`: str - вопрос пользователя
- `iteration`: int - текущая итерация (1, 2, 3...)
- `MAX_ITERATIONS`: int - максимальное количество итераций
- `step`: int - текущий шаг
- `plan`: list - список шагов плана поиска

### Специфичные для action

- `refinement_plan`: list - план уточнения (если есть)
- `all_tool_results`: list - все результаты инструментов

### Специфичные для observation

- `tools_json`: str - JSON с результатами выполнения инструментов

### Специфичные для refine

- `observation`: str - результат наблюдения

### Специфичные для final

- `total_tools`: int - общее количество выполненных инструментов
- `all_results_json`: str - JSON со всеми результатами
- `observation`: str - финальная observation

### Специфичные для system_prompt_base

- `available_tools`: str - JSON со списком доступных инструментов

---

## Установка зависимостей

```bash
pip install jinja2
```

Или добавьте в `requirements.txt`:

```
jinja2>=3.1.0
```

---

## Отладка

### Проверка рендеринга

```python
from prompt_loader import get_loader

loader = get_loader()
state = {'user_query': 'test'}

try:
    result = loader.render('plan/user.md', state)
    print(result)
except Exception as e:
    print(f"Ошибка рендеринга: {e}")
```

### Проверка доступных переменных

```python
# В промпте можно добавить временно для отладки:
# DEBUG: {{ state.keys() }}

# Или в Python:
print(f"Доступные ключи: {state.keys()}")
```

---

## FAQ

### Q: Как добавить новый промпт?

1. Создать `.md` файл в нужной папке (например, `prompts/action/new_prompt.md`)
2. Использовать Jinja2 синтаксис с переменными из state
3. Добавить метод в `prompt_loader.py` (опционально):
   ```python
   def render_action_new(self, state: Dict[str, Any]) -> str:
       return self.render('action/new_prompt.md', state)
   ```
4. Добавить функцию в `prompts.py` для совместимости:
   ```python
   def get_action_new_prompt(state: dict) -> str:
       return _loader.render_action_new(state)
   ```

### Q: Как переиспользовать части промпта?

Создать общий файл (например, `prompts/common/rules.md`) и включить его:

```jinja2
{% include 'common/rules.md' %}

Дополнительные инструкции для этого этапа...
```

### Q: Как добавить условную логику?

Использовать Jinja2 условия:

```jinja2
{% if iteration > 1 %}
Используй targeted tools.
{% else %}
Используй broad search.
{% endif %}
```

### Q: Будут ли работать старые вызовы prompts.py?

Да, `prompts.py` - это обертка, которая гарантирует обратную совместимость. Все функции имеют единую сигнатуру `(state: dict)`.

---

## Миграция старого кода

### Было (старая система)

```python
# Промпты были встроены в Python код
def get_plan_user_prompt(user_query: str) -> str:
    return f"Вопрос: {user_query}"
```

### Стало (новая система)

**prompts/plan/user.md:**
```jinja2
Вопрос: {{ user_query }}
```

**prompts.py:**
```python
def get_plan_user_prompt(state: dict) -> str:
    return _loader.render_plan_user(state)
```

**rag_lg_agent.py:**
```python
# Раньше:
user_message = prompts.get_plan_user_prompt(state['user_query'])

# Теперь:
user_message = prompts.get_plan_user_prompt(state)
```

---

## Производительность

- **Загрузка**: Быстрая (файлы небольшие, ~1-5 КБ каждый)
- **Рендеринг**: Мгновенный (Jinja2 очень быстрый)
- **Кеширование**: Не используется для упрощения разработки
- **Влияние на RAG**: Минимальное (~0.001 сек на промпт)

---

## Лицензия

Следует лицензии проекта tools.0.

