# Промпты V2 для RAG-агента

Новая версия промптов — **с нуля**, фокус на простоте и четкости.

## Структура

### Файлы

```
prompts_v2/
├── system.md         — базовый system prompt (общий для всех)
├── plan.md           — нода планирования
├── action.md         — нода выполнения инструментов
├── observation.md    — нода анализа результатов
├── refine.md         — нода принятия решения
└── final.md          — нода формирования ответа
```

### Поток

```
plan → action → observation → refine → (action) → final
```

## Особенности

### 1. Краткость
- Системный промпт: ~50 строк (vs 600+ в старой версии)
- Каждая нода: ~80-120 строк
- Никакой воды, только суть

### 2. Четкая структура
Каждая нода:
- Задача (что делать)
- Контекст (данные)
- Формат ответа
- Примеры
- Чек-лист/правила

### 3. Примеры
Каждая нода содержит примеры JSON-ответов:
- Типичные кейсы
- Граничные случаи
- Что делать, если данных нет

### 4. JSON-first
- Всегда возвращается JSON
- Четкая schema для каждой ноды
- Без текста вне JSON

## Переменные

Используются в промптах через Jinja2:

```jinja2
{{ user_query }}           # Вопрос пользователя
{{ plan }}                 # Список шагов плана
{{ all_tool_results }}     # История вызовов инструментов
{{ tools_results }}        # Результаты текущей итерации
{{ observation }}          # Текущая observation
{{ iteration }}            # Номер итерации
{{ MAX_ITERATIONS }}       # Максимум итераций
{{ all_results_json }}     # Все результаты в JSON
{{ available_tools }}      # JSON спецификация инструментов
```

## JSON Schema

### Plan
```json
{
  "thought": "string",
  "plan": ["string", ...]
}
```

### Action
```json
{
  "thought": "string",
  "actions": [
    {"tool": "string", "input": {}}
  ]
}
```

### Observation
```json
{
  "thought": "string",
  "observation": "string"
}
```

### Refine
```json
{
  "thought": "string",
  "needs_more_data": boolean,
  "refinement_plan": ["string", ...] // если needs_more_data = true
}
```

### Final
```json
{
  "thought": "string",
  "final_answer": {
    "summary": "string",
    "details": "string",
    "sources": ["string", ...],
    "confidence": float
  }
}
```

## Инструменты

15 инструментов для работы с документацией в ClickHouse:

**Поиск:**
- `semantic_search` — семантический поиск по эмбеддингам
- `exact_search` — точный поиск по подстроке
- `multi_term_exact_search` — поиск по нескольким терминам
- `regex_search` — regex-поиск (IP, порты, VLAN)

**Разделы:**
- `find_sections_by_term` — разделы содержащие термин
- `find_relevant_sections` — двухэтапный поиск (название + содержимое)
- `get_section_content` — полный текст раздела

**Контекст:**
- `get_neighbor_chunks` — соседние чанки
- `get_chunks_by_index` — чанки по индексам

**Таблицы:**
- `read_table` — чтение строк таблицы

**Файловый поиск:**
- `exact_search_in_file` — поиск в конкретном файле
- `exact_search_in_file_section` — поиск в разделе файла

**Навигация:**
- `list_sections` — список разделов
- `list_sources` — список файлов
- `list_all_sections` — все пары (source, section)

## Использование

### Интеграция в код

```python
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('prompts_v2'))

# Для plan ноды
template = env.get_template('user.md')
prompt = template.render(
    user_query=question,
    available_tools=tools_json
)
```

### Тестирование

```bash
# Только план (1 шаг)
python rag_lg_agent.py --steps 1 "ваш вопрос"

# План + action (2 шага)
python rag_lg_agent.py --steps 2 "ваш вопрос"

# Полное выполнение
python rag_lg_agent.py "ваш вопрос"
```

Логи смотреть в `logs/001_*.log`, `logs/002_*.log` и т.д.

## Отличия от V1

| Аспект | V1 (старая) | V2 (новая) |
|--------|-------------|------------|
| Размер system prompt | 600+ строк | ~50 строк |
| Стратегии поиска | 200+ строк подробностей | Кратко в примерах |
| Примеры | Немного | Для каждого кейса |
| Фокус | Описание всех нюансов | Действие и результат |
| Читаемость | Перегружено | Четко и ясно |

## Философия

**Меньше текста — больше действий**

- LLM не нужны длинные объяснения
- Нужны четкие инструкции и примеры
- JSON schema > текстовое описание
- Примеры > правила

**Итеративность вместо универсальности**

- Каждая нода делает ОДНО дело
- Refine проверяет достаточность данных
- Можно вернуться и доискать информацию

**Простота > сложность**

- Если можно проще — делай проще
- Никакой воды
- Прямые инструкции

## Миграция

Чтобы переключиться с V1 на V2:

1. В `rag_lg_agent.py` изменить путь к промптам:
   ```python
   PROMPTS_DIR = Path(__file__).parent / "prompts_v2"
   ```

2. Обновить функции рендеринга промптов (если нужно)

3. Протестировать с `--steps 1` для каждой ноды

4. Сравнить результаты с V1

## Дальнейшие улучшения

- [ ] Добавить промпты для обработки таблиц
- [ ] Специализированные промпты для списков
- [ ] Промпт для извлечения структурированных данных
- [ ] Few-shot примеры для сложных кейсов

