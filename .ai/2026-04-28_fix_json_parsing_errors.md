# Исправление ошибок парсинга JSON в RAG-агенте

**Дата:** 2026-04-28  
**Файл:** `RAG/rag_lg_agent.py`  
**Проблема:** LLM возвращал JSON с неправильным `status`, что приводило к ошибкам `OutputParserException`

---

## Проблема

При выполнении запроса AgentAction (этап `action`) LLM возвращал JSON с `status: "observation"` вместо `status: "action"`:

```json
{
  "step": 1,
  "thought": "...",
  "observation": "...",
  "status": "observation"
}
```

Это приводило к ошибке:
```
langchain_core.exceptions.OutputParserException: Invalid json output: 
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

**Причина:** 
- System prompt (`system_prompt.md`) описывает универсальный формат JSON, где в одном объекте могут быть разные поля
- Код использует строгие Pydantic модели для каждого этапа (`AgentPlan`, `AgentAction`, `AgentObservation`, etc.)
- LLM видел в истории сообщений предыдущие `observation` и копировал их формат вместо `action`

---

## Решение

### 1. Добавлены явные примеры JSON для каждого этапа

Для всех узлов (`plan_node`, `action_node`, `observation_node`, `refine_node`, `final_node`) добавлены секции с обязательным форматом ответа:

**Пример для `action_node`:**
```python
⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа action:
```json
{
  "status": "action",
  "step": 2,
  "thought": "краткое рассуждение о выборе инструментов",
  "action": [
    {
      "tool": "имя_инструмента",
      "input": {"параметр": "значение"}
    }
  ]
}
```

НЕ ИСПОЛЬЗУЙ поля "observation" или "final_answer" на этом этапе!
```

### 2. Добавлена retry логика для всех узлов

При ошибке парсинга JSON система теперь:
1. Ловит исключение
2. Добавляет сообщение с правильным форматом
3. Повторяет запрос к LLM (до 2 попыток)

**Пример retry логики:**
```python
max_retries = 2
for attempt in range(max_retries):
    try:
        result: AgentAction = structured_llm.invoke(messages, config=invoke_config)
        break  # Успешно распарсили
    except Exception as exc:
        if attempt < max_retries - 1:
            logger.warning(f"Ошибка парсинга JSON (попытка {attempt + 1}/{max_retries}): {exc}")
            
            error_message = """⚠️ ОШИБКА ПАРСИНГА JSON!
            
ОБЯЗАТЕЛЬНАЯ структура для этапа action:
{
  "status": "action",  ← ОБЯЗАТЕЛЬНО "action", НЕ "observation"!
  ...
}

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше."""
            
            messages.append({"role": "user", "content": error_message})
            continue
        else:
            raise  # Последняя попытка - выбрасываем исключение
```

---

## Изменённые узлы

1. **`plan_node`** - добавлен явный формат для `status: "plan"` + retry логика
2. **`action_node`** - добавлен явный формат для `status: "action"` + retry логика
3. **`observation_node`** - добавлен явный формат для `status: "observation"` + retry логика
4. **`refine_node`** - добавлен явный формат для `status: "refine"` + retry логика
5. **`final_node`** - добавлен явный формат для `status: "final"` + retry логика

---

## Результат

✅ LLM теперь получает явные инструкции о правильном формате JSON для каждого этапа  
✅ При ошибке парсинга система автоматически повторяет запрос с дополнительными инструкциями  
✅ Снижается вероятность ошибок `OutputParserException`  
✅ Улучшена отказоустойчивость агента  

---

## Дополнительные улучшения

Все изменения следуют принципам:
- Явные инструкции для LLM (explicit is better than implicit)
- Автоматическая коррекция ошибок (self-healing)
- Подробное логирование всех попыток и ошибок
- Сохранение контекста между попытками (LLM видит свою ошибку и исправляется)

---

## Файлы

- `RAG/rag_lg_agent.py` - основной файл с исправлениями
- `RAG/system_prompt.md` - базовый промпт (не изменялся, но используется во всех узлах)

