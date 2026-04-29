# Быстрый старт: Промпты V2

## TL;DR

**5 нод, 5 промптов, JSON only**

```
plan → action → observation → refine → final
```

## Шпаргалка по нодам

### 1. PLAN
**Задача:** Составить план поиска

**Вход:** `user_query`

**Выход:**
```json
{
  "thought": "...",
  "plan": ["шаг 1", "шаг 2", "шаг 3"]
}
```

**Ключевые вопросы:**
- Что спрашивается?
- Какие инструменты нужны?
- В какой последовательности?

---

### 2. ACTION
**Задача:** Вызвать инструменты

**Вход:** `user_query`, `plan`, `all_tool_results`

**Выход:**
```json
{
  "thought": "...",
  "actions": [
    {"tool": "...", "input": {...}}
  ]
}
```

**Правила:**
- Можно вызывать параллельно
- НЕ повторять прошлые вызовы
- Проверять имена параметров

---

### 3. OBSERVATION
**Задача:** Проанализировать результаты

**Вход:** `user_query`, `tools_results`

**Выход:**
```json
{
  "thought": "...",
  "observation": "подробный анализ результатов..."
}
```

**Что анализировать:**
- Релевантность найденного
- Полнота данных
- Что еще нужно

---

### 4. REFINE
**Задача:** Решить — продолжать или финализировать

**Вход:** `user_query`, `observation`, `iteration`, `MAX_ITERATIONS`

**Выход (достаточно данных):**
```json
{
  "thought": "...",
  "needs_more_data": false
}
```

**Выход (нужно больше):**
```json
{
  "thought": "...",
  "needs_more_data": true,
  "refinement_plan": ["что искать дальше", "..."]
}
```

**Решение:**
- ✅ Данных достаточно → `false` → final
- ⚠️ Нужно еще → `true` → action

---

### 5. FINAL
**Задача:** Сформировать ответ

**Вход:** `user_query`, `plan`, `observation`, `all_results_json`

**Выход:**
```json
{
  "thought": "...",
  "final_answer": {
    "summary": "краткий ответ",
    "details": "подробное объяснение",
    "sources": ["файл.md > раздел"],
    "confidence": 0.95
  }
}
```

**Правила:**
- Summary: 1-2 предложения
- Details: факты из документации
- Sources: обязательно
- Confidence: честная оценка

---

## Инструменты TOP-5

1. **semantic_search** — по смыслу (концепции, определения)
2. **exact_search** — точное совпадение (термины, названия)
3. **multi_term_exact_search** — несколько терминов (сложные запросы)
4. **find_sections_by_term** — где упоминается (навигация)
5. **get_section_content** — полный текст (детали)

## Типичные сценарии

### "Что такое X?"
```
plan: semantic_search + exact_search
action: вызвать оба
observation: проанализировать определения
refine: достаточно → final
final: определение + контекст
```

### "Дай список X"
```
plan: find_sections_by_term + get_section_content
action: найти разделы с X
observation: проверить полноту
refine: если нужно — уточнить
final: структурированный список
```

### "Какое X на Y?"
```
plan: multi_term_exact_search (X, Y, синонимы)
action: поиск по терминам
observation: фильтровать релевантные
refine: если мало — exact_search_in_file
final: точный ответ с источниками
```

## Отладка

```bash
# План
--steps 1

# План + действие
--steps 2

# План + действие + анализ
--steps 3

# План + действие + анализ + решение
--steps 4

# Полностью
без --steps
```

Логи: `logs/00N_*.log`

## JSON Schema быстро

```typescript
// Plan
{
  thought: string,
  plan: string[]
}

// Action
{
  thought: string,
  actions: Array<{tool: string, input: object}>
}

// Observation
{
  thought: string,
  observation: string
}

// Refine
{
  thought: string,
  needs_more_data: boolean,
  refinement_plan?: string[]  // если true
}

// Final
{
  thought: string,
  final_answer: {
    summary: string,
    details: string,
    sources: string[],
    confidence: number  // 0.0-1.0
  }
}
```

## Ошибки частые

❌ **Повторные вызовы**
```json
// Было в iteration 1
{"tool": "exact_search", "input": {"substring": "СОИБ"}}
// НЕ повторять в iteration 2!
```

❌ **Неправильные параметры**
```json
// Неправильно
{"tool": "find_sections_by_term", "input": {"term": "..."}}
// Правильно
{"tool": "find_sections_by_term", "input": {"substring": "..."}}
```

❌ **Выдумывание данных**
```json
// Если данных НЕТ в документации
"summary": "В документации информация отсутствует"
// А не выдумывать факты!
```

❌ **Текст вне JSON**
```
Вот ответ:
{"thought": "..."}
// Неправильно! Только JSON!
```

## Советы

✅ **Краткость** — thought должен быть кратким (1 предложение)

✅ **Параллельность** — вызывай несколько инструментов сразу

✅ **Источники** — ВСЕГДА указывай файл и раздел

✅ **Честность** — нет данных? Так и пиши

✅ **Итеративность** — не нашел с первого раза? Попробуй другой инструмент

## Контакты

Документация: `prompts_v2/README.md`
Примеры: внутри каждого промпта

