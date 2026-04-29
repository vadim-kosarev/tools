# Исправление: user_query не передается в final_node

## Проблема

В логе 018_llm_final_response.log LLM отвечает:

```json
{
  "thought": "Пользователь не задаёт вопрос. Нужно запросить ввод пользователя.",
  "final_answer": {
    "summary": "Ожидание вопроса пользователя",
    "details": "Я готов ответить на вопросы по документации...",
    "confidence": 1.0,
    "self_assessment": "Ожидание ввода пользователя"
  }
}
```

Хотя вопрос был: **"что такое smart monitor"**

## Причина

В логе 017_llm_final_request.log вопрос пользователя НЕ отображается:

```markdown
### 🎯 ВОПРОС ПОЛЬЗОВАТЕЛЯ (РЕАЛЬНЫЙ):

```
```
```

**Пустой блок!** `{{ user_query }}` не подставляется.

### Почему

`user_query` должен быть в `state` (устанавливается в `initial_state`), но по какой-то причине:
1. Либо теряется при передаче между нодами
2. Либо перезаписывается  
3. Либо не передается в LangGraph правильно

## Решение

Добавлены **проверки и логирование** `user_query` во всех нодах.

### 1. plan_node

```python
# ПРОВЕРКА: user_query ОБЯЗАТЕЛЬНО должен быть
if 'user_query' not in state or not state['user_query']:
    logger.error("⚠️ PLAN: user_query отсутствует в state!")
    raise ValueError("user_query is required in state")
logger.info(f"✅ PLAN: user_query = '{state['user_query']}'")
```

### 2. action_node

```python
# ПРОВЕРКА: user_query должен быть в state
if 'user_query' not in state or not state['user_query']:
    logger.error("⚠️ ACTION: user_query отсутствует в state!")
    state['user_query'] = "ОШИБКА: вопрос не передан"
else:
    logger.info(f"✅ ACTION: user_query = '{state['user_query']}'")
```

### 3. observation_node

```python
# ПРОВЕРКА: user_query должен быть в state  
if 'user_query' not in state or not state['user_query']:
    logger.error("⚠️ OBSERVATION: user_query отсутствует в state!")
    state['user_query'] = "ОШИБКА: вопрос не передан"
else:
    logger.info(f"✅ OBSERVATION: user_query = '{state['user_query']}'")
```

### 4. final_node

```python
# ОБЯЗАТЕЛЬНО проверяем что user_query есть
if 'user_query' not in state or not state['user_query']:
    logger.error("⚠️ user_query отсутствует в state!")
    state['user_query'] = "ОШИБКА: вопрос не передан"
else:
    logger.info(f" user_query в state: '{state['user_query']}'")
```

## Диагностика

Теперь в логах будет видно:

```
2026-04-28 XX:XX:XX [INFO] ✅ PLAN: user_query = 'что такое smart monitor'
2026-04-28 XX:XX:XX [INFO] ✅ ACTION: user_query = 'что такое smart monitor'
2026-04-28 XX:XX:XX [INFO] ✅ OBSERVATION: user_query = 'что такое smart monitor'
2026-04-28 XX:XX:XX [INFO] ✅ user_query в state: 'что такое smart monitor'
```

Или (если проблема):

```
2026-04-28 XX:XX:XX [ERROR] ⚠️ FINAL: user_query отсутствует в state!
```

## Возможные причины потери user_query

### 1. TypedDict строгость

LangGraph использует TypedDict для state. Если где-то создается новый dict без user_query:

```python
# НЕПРАВИЛЬНО
state = {
    "step": 1,
    "plan": [...]
    # user_query забыли!
}
```

### 2. Копирование state

Если state копируется неправильно:

```python
# НЕПРАВИЛЬНО
new_state = {}
new_state['step'] = state['step']
# user_query не скопирован!
```

### 3. LangGraph bugs

Возможно баг в передаче state между нодами в LangGraph.

## Следующие шаги

### 1. Запустить с логированием

```bash
python rag_lg_agent.py "что такое smart monitor"

# Смотрим логи
cat logs/rag_lg_agent_v2.log | grep "user_query"
```

### 2. Найти где теряется

Если видим:
```
✅ PLAN: user_query = 'что такое smart monitor'
✅ ACTION: user_query = 'что такое smart monitor'
⚠️ OBSERVATION: user_query отсутствует в state!  ← ЗДЕСЬ!
```

Значит проблема между action_node и observation_node.

### 3. Исправить передачу

Проверить что все ноды возвращают полный state:

```python
def action_node(state: AgentState) -> AgentState:
    # ...обработка...
    
    # ОБЯЗАТЕЛЬНО вернуть ВСЕ поля state
    return state  # не создавать новый dict!
```

## Альтернативное решение

Если проблема в LangGraph, можно явно копировать user_query:

```python
def observation_node(state: AgentState) -> AgentState:
    # Явно сохраняем user_query
    user_query = state.get('user_query', '')
    
    # ...обработка...
    
    # Явно восстанавливаем user_query перед return
    state['user_query'] = user_query
    return state
```

## Связанные проблемы

- `060_FIX_LLM_IGNORING_QUESTION.md` - LLM игнорировала вопрос в промпте
- `061_FIX_SYSTEM_DUPLICATION.md` - System дублировался в user messages
- Эта проблема: **user_query не передается в state**

Все три про то что LLM не видит вопрос, но по разным причинам:
1. Вопрос есть, но не выделен → добавили эмодзи 🎯
2. System дублировался → убрали include
3. Вопрос не передается → добавили проверки

## Итог

✅ **Добавлено логирование user_query во всех нодах**
✅ **Проверки наличия user_query**
✅ **Fallback на ошибку если user_query отсутствует**
✅ **Готово к диагностике**

После запуска будет видно где именно теряется вопрос! 🔍

