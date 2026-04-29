# 004. Исправление истории сообщений в LLM запросах

## Проблема

В секции `[MESSAGES]` логов LLM запросов не содержалась история сообщений - там был только текущий запрос пользователя. Необходимо было хранить полную историю:
- запросы к LLM
- ответы от LLM  
- запросы к TOOL
- ответы от TOOL

## Причина

В файле `rag_lg_agent.py` каждый узел (plan, action, observation, refine, final) вызывал LLM **только с двумя новыми сообщениями**:

```python
result = structured_llm.invoke(
    [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message}
    ],
    config=invoke_config,
)
```

История накапливалась в `state["messages"]`, но **НЕ передавалась** в последующие вызовы LLM.

## Решение

### 1. Передача истории в LLM вызовы

Изменены все узлы (`action_node`, `observation_node`, `refine_node`, `final_node`), чтобы они:

1. Формировали список сообщений из истории
2. Добавляли новый system prompt
3. Добавляли новый user message
4. Передавали полный список в LLM

**Пример (action_node):**

```python
# Формируем список сообщений: system message + история + новый user message
messages = [{"role": "system", "content": system_message}]

# Добавляем историю (пропускаем старые system messages)
for msg in state["messages"]:
    if msg["role"] != "system":
        messages.append(msg)

# Добавляем текущий user message
messages.append({"role": "user", "content": user_message})

# Сохраняем user message в историю
state["messages"].append({"role": "user", "content": user_message})

# Вызов LLM с полной историей
result: AgentAction = structured_llm.invoke(messages, config=invoke_config)
```

### 2. Сохранение user сообщений

Теперь каждый узел **сохраняет свой user message** в `state["messages"]` перед вызовом LLM.

### 3. Фильтрация system messages

Старые system messages не передаются в последующие вызовы - только текущий system prompt используется для каждого узла.

## Структура истории

Теперь `state["messages"]` содержит:

```
[
  {role: "system", content: "..."},      # из plan_node
  {role: "user", content: "вопрос"},     # из plan_node
  {role: "assistant", content: "..."},   # ответ plan_node
  {role: "user", content: "контекст"},   # из action_node
  {role: "assistant", content: "..."},   # ответ action_node
  {role: "tool", name: "exact_search", content: "..."}, # результаты tool 1
  {role: "tool", name: "semantic_search", content: "..."}, # результаты tool 2
  {role: "user", content: "анализ"},     # из observation_node
  {role: "assistant", content: "..."},   # ответ observation_node
  ...
]
```

## Результат

Теперь в логах `[MESSAGES]` видна **полная история** взаимодействия:
- Все запросы пользователя на каждом этапе
- Все ответы LLM
- Все вызовы инструментов и их результаты

Это позволяет:
- **Отлаживать** поведение агента
- **Понимать** контекст каждого решения
- **Улучшать** промпты на основе полной истории
- **Воспроизводить** проблемы

## Измененные файлы

- `rag_lg_agent.py` - добавлена передача истории во все узлы (action_node, observation_node, refine_node, final_node)

## Совместимость

Изменения **обратно совместимы** - все существующие функции работают как прежде, но теперь с полной историей сообщений.

## Дата изменений

2026-04-27

