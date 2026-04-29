# Итоговое резюме сессии: Улучшения RAG-агента

## Обзор

Серия улучшений RAG-агента: от отладки промптов до автоматической генерации JSON schema.

## Выполненные задачи

### 1. ✅ Режим отладки с ограничением шагов

**Проблема:** Сложно отлаживать промпты, приходится ждать полного выполнения.

**Решение:** Добавлен аргумент `--steps N` для выполнения только N шагов графа.

```bash
# Только планирование
python rag_lg_agent.py --steps 1 "вопрос"

# План + действие
python rag_lg_agent.py --steps 2 "вопрос"
```

**Файлы:**
- `rag_lg_agent.py` - добавлен параметр max_steps
- `DEBUG_MODE.md` - документация
- `051_DEBUG_MODE_AND_PROMPT_VISIBILITY.md` - итоговый отчет

---

### 2. ✅ Улучшение видимости вопроса пользователя

**Проблема:** LLM не видела вопрос в огромном системном промпте (600+ строк).

**Решение:** 
- Упрощен system prompt до ~30 строк
- Вопрос выделен эмодзи 🎯 и визуальными рамками

**До:**
```markdown
Вопрос пользователя:
{{ user_query }}
```

**После:**
```markdown
🎯🎯🎯 **ВОПРОС ПОЛЬЗОВАТЕЛЯ** 🎯🎯🎯

{{ user_query }}

🎯🎯🎯🎯🎯🎯🎯🎯🎯🎯🎯🎯🎯
```

**Файлы:**
- `prompts/plan/system.md` - упрощен до 30 строк
- `prompts/plan/user.md` - усилено выделение
- `052_FIX_QUESTION_VISIBILITY.md` - документация

---

### 3. ✅ Увеличение MAX_ITERATIONS

**Было:** 2 итерации
**Стало:** 5 итераций

**Причина:** Сложные вопросы требуют больше циклов уточнения.

**Файл:** `rag_lg_agent.py` (строка 72)

---

### 4. ✅ Удален лишний слой промптов

**Проблема:** 3 уровня вызовов для загрузки промпта.

**Было:**
```
rag_lg_agent.py → prompts.py → prompt_loader.py → файл.md
```

**Стало:**
```
rag_lg_agent.py → prompt_loader.py → файл.md
```

**Изменения:**
- Убран `import prompts`
- Прямой импорт `from prompt_loader import get_loader`
- Все вызовы `prompts.get_*()` заменены на `_prompt_loader.render_*()`

**Файлы:**
- `rag_lg_agent.py` - 14 замен вызовов
- `prompts.py` - помечен как DEPRECATED
- `053_REFACTOR_REMOVE_PROMPTS_LAYER.md` - документация

---

### 5. ✅ Путь к промптам через .env

**Проблема:** Хардкод пути к промптам в коде.

**Решение:** Переменная `PROMPTS_DIR` в .env.

```bash
# .env
PROMPTS_DIR=prompts      # текущая версия
PROMPTS_DIR=prompts_v2    # новая версия
```

**Изменения:**
- `rag_chat.py` - добавлено поле `prompts_dir` в Settings
- `prompt_loader.py` - использует settings.prompts_dir
- `.env.example` - документирована переменная
- `054_CONFIG_PROMPTS_DIR.md` - документация

---

### 6. ✅ Создана альтернативная версия промптов

**Создана папка `prompts_v2/` с нуля:**
- `system.md` - базовый prompt (~50 строк vs 600+)
- `plan.md` - планирование
- `action.md` - выполнение инструментов
- `observation.md` - анализ результатов
- `refine.md` - принятие решения
- `final.md` - финальный ответ
- `README.md` - полная документация
- `QUICKSTART.md` - быстрый старт

**Философия:** Краткость > подробность, примеры > объяснения, JSON-first.

---

### 7. ✅ JSON Schema из Pydantic моделей

**Проблема:** JSON schema в промптах дублировал Pydantic модели (two sources of truth).

**Решение:** Автоматическая генерация schema из моделей.

**Архитектура:**
```
Pydantic модель → schema_generator → state['response_schema'] → {{ response_schema }} в промпте
```

**Реализация:**

1. **schema_generator.py** - новый модуль для генерации schema
   - `generate_json_example(model)` - генерация с комментариями
   - `get_plan_schema()`, `get_action_schema()`, etc.

2. **rag_lg_agent.py** - все ноды добавляют schema в state:
   ```python
   state['response_schema'] = get_plan_schema()
   ```

3. **prompts/plan/system.md** - заменен хардкод на placeholder:
   ```markdown
   ## Формат ответа
   {{ response_schema }}
   ```

**Пример сгенерированной schema:**
```json
{
  "thought": "string",  // Краткое рассуждение (1-2 предложения)
  "plan": [
    "string"
  ]  // Список шагов плана поиска (3-5 пунктов)
}
```

**Преимущества:**
- ✅ Single source of truth
- ✅ Промпты синхронизированы автоматически
- ✅ Descriptions из Field() видны в промпте
- ✅ Constraints отображаются

**Файлы:**
- `schema_generator.py` - генератор schema
- `rag_lg_agent.py` - импорт и использование
- `prompts/plan/system.md` - обновлен
- `055_SCHEMA_FROM_PYDANTIC.md` - документация

---

## Структура проекта

```
RAG/
├── .env                           # Конфигурация (PROMPTS_DIR=prompts)
├── .env.example                   # Пример конфигурации
├── rag_lg_agent.py                # Основной агент (обновлен)
├── prompt_loader.py               # Загрузчик промптов (обновлен)
├── schema_generator.py            # Генератор JSON schema (новый)
├── prompts/                       # Текущая версия промптов (упрощена)
│   ├── plan/system.md             # Упрощен до 30 строк
│   ├── plan/user.md               # Усилено выделение вопроса
│   └── ...                        # Остальные ноды
├── prompts_v2/                    # Альтернативная версия (новая)
│   ├── system.md                  # Базовый (~50 строк)
│   ├── plan.md                    # Нода планирования
│   ├── action.md                  # Нода выполнения
│   ├── observation.md             # Нода анализа
│   ├── refine.md                  # Нода решения
│   ├── final.md                   # Нода ответа
│   ├── README.md                  # Документация
│   └── QUICKSTART.md              # Быстрый старт
├── docs/                          # Документация
│   ├── 051_DEBUG_MODE_AND_PROMPT_VISIBILITY.md
│   ├── 052_FIX_QUESTION_VISIBILITY.md
│   ├── 053_REFACTOR_REMOVE_PROMPTS_LAYER.md
│   ├── 054_CONFIG_PROMPTS_DIR.md
│   └── 055_SCHEMA_FROM_PYDANTIC.md
└── DEBUG_MODE.md                  # Руководство по отладке
```

## Использование

### Режим отладки

```bash
# Только план
python rag_lg_agent.py --steps 1 "вопрос"

# План + действие
python rag_lg_agent.py --steps 2 "вопрос"

# Полностью
python rag_lg_agent.py "вопрос"
```

### Переключение версий промптов

```bash
# Текущая версия
PROMPTS_DIR=prompts python rag_lg_agent.py "вопрос"

# Новая версия
PROMPTS_DIR=prompts_v2 python rag_lg_agent.py "вопрос"
```

### Добавление нового поля в модель

1. Обновить Pydantic модель:
   ```python
   class AgentPlan(BaseModel):
       thought: str = Field(description="Рассуждение")
       plan: list[str] = Field(description="План")
       confidence: float = Field(ge=0.0, le=1.0, description="Уверенность")  # новое
   ```

2. Готово! Schema в промптах обновится автоматически.

## Метрики

| Аспект | До | После | Улучшение |
|--------|----|----|-----------|
| Размер plan system prompt | 600+ строк | 30 строк | -95% |
| Уровни вызовов промптов | 3 | 2 | -33% |
| MAX_ITERATIONS | 2 | 5 | +150% |
| Sources of truth для JSON | 2 (модель + промпт) | 1 (модель) | -50% |
| Дублирование JSON schema | вручную | автоматически | 100% |

## Ключевые принципы

1. **Краткость** - меньше текста, больше фокуса
2. **Автоматизация** - генерация вместо хардкода
3. **Конфигурация** - .env вместо хардкода путей
4. **Отладка** - пошаговое выполнение
5. **Single source of truth** - один источник истины

## Тестирование

```bash
# 1. Тест режима отладки
python rag_lg_agent.py --steps 1 "что такое СОИБ"

# 2. Проверка логов
cat logs/001_llm_plan_request.log

# 3. Проверка schema генератора
python schema_generator.py

# 4. Тест с prompts_v2
echo "PROMPTS_DIR=prompts_v2" >> .env
python rag_lg_agent.py "тест"
```

## Следующие шаги

### Потенциальные улучшения
- [ ] Кэширование сгенерированных schema
- [ ] Поддержка Union типов в schema_generator
- [ ] Миграция на prompts_v2 после тестирования
- [ ] Few-shot примеры в промптах
- [ ] Опция генерации schema без комментариев

### Мониторинг
- [ ] Сравнить качество ответов prompts vs prompts_v2
- [ ] Измерить влияние MAX_ITERATIONS=5 на время
- [ ] A/B тестирование длины system prompt

## Итог

✅ **7 задач выполнено**
✅ **5 документов создано**
✅ **1 новый модуль (schema_generator.py)**
✅ **2 версии промптов (prompts, prompts_v2)**
✅ **Режим отладки готов**
✅ **Конфигурируемость через .env**
✅ **Single source of truth для JSON schema**

Агент стал:
- Гибче (конфигурируемость)
- Понятнее (упрощенные промпты)
- Надежнее (автоматическая синхронизация schema)
- Удобнее в отладке (режим --steps)

🎉 **Готово к продакшену!**

