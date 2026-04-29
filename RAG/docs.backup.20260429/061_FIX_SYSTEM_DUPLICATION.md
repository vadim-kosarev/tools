# Исправление: System Prompt дублировался в User Message

## Проблема

В логах видно что system prompt дублировался в user message:

**Лог 005_llm_action_1_request.log:**
```
[#1 USER]
## ЭТАП: PLAN (Планирование)
...

[#2 ASSISTANT]
**AgentPlan**
...

[#3 USER]
# System Prompt для RAG-агента работы с документацией  ← ⚠️ System prompt!
...
## ЭТАП: ACTION (Выполнение)  ← И user prompt!
...
```

❌ **В [#3 USER] попал И system prompt И user prompt!**

### Почему это плохо

1. **Избыточность** - system дублируется в истории
2. **Токены** - лишние ~600 строк на каждое сообщение
3. **Контекст** - занимает context window
4. **Логика** - system должен быть один раз в начале

## Причина

User промпты содержали `{% include 'system.md' %}`:

```markdown
{% include 'system.md' %}  ← ⚠️ Дублирует system!

---

## ЭТАП: ACTION (Выполнение)
...
```

Код формировал messages:
```python
messages = [
    {"role": "system", "content": system_message},  # System отдельно
]
for msg in state["messages"]:
    messages.append(msg)  # История
messages.append({"role": "user", "content": user_message})  # + user который содержит system!
```

Результат:
- System отдельной ролью ✅
- User с включенным system внутри ❌

## Решение

Убраны `{% include 'system.md' %}` из всех user промптов.

### Изменённые файлы

#### 1. action/user.md

**Было:**
```markdown
{% include 'system.md' %}

---

## ЭТАП: ACTION (Выполнение)
```

**Стало:**
```markdown
## ЭТАП: ACTION (Выполнение)
```

#### 2. observation.md

**Было:**
```markdown
{% include 'system.md' %}

---

## ЭТАП: OBSERVATION (Анализ результатов)
```

**Стало:**
```markdown
## ЭТАП: OBSERVATION (Анализ результатов)
```

#### 3. refine.md

**Было:**
```markdown
{% include 'system.md' %}

---

## ЭТАП: REFINE (Решение)
```

**Стало:**
```markdown
## ЭТАП: REFINE (Решение)
```

#### 4. final.md

**Было:**
```markdown
{% include 'system.md' %}

---

## ЭТАП: FINAL (Финальный ответ)
```

**Стало:**
```markdown
## ЭТАП: FINAL (Финальный ответ)
```

## Правильная архитектура

### System промпты (отдельно)

- `plan/system.md` - может включать `{% include 'system.md' %}` ✅
- `action/system.md` - может включать `{% include 'system.md' %}` ✅  
- Эти файлы используются для `render_action_system()`

### User промпты (без system)

- `plan/user.md` - только user часть, БЕЗ include ✅
- `action/user.md` - только user часть, БЕЗ include ✅
- `observation.md` - только user часть, БЕЗ include ✅
- `refine.md` - только user часть, БЕЗ include ✅
- `final.md` - только user часть, БЕЗ include ✅

### Код (правильный)

```python
# Формируем отдельно
system_message = _prompt_loader.render_action_system(state)  # Из action/system.md
user_message = _prompt_loader.render_action_user(state)      # Из action/user.md

# Собираем messages
messages = [{"role": "system", "content": system_message}]  # System один раз
messages.append({"role": "user", "content": user_message})  # User без system
```

## Результат после исправления

**Ожидаемые логи:**

```
[SYSTEM]
# System Prompt для RAG-агента работы с документацией
...
(базовый system prompt)

[MESSAGES]

[#1 USER]
## ЭТАП: PLAN (Планирование)
...
(только user часть)

[#2 ASSISTANT]
**AgentPlan**
...

[#3 USER]
## ЭТАП: ACTION (Выполнение)  ← Только user!
...
(БЕЗ system prompt)
```

## Преимущества

### 1. Экономия токенов

**Было:**
- [SYSTEM]: ~600 строк
- [#1 USER]: ~50 строк (plan user)
- [#2 ASSISTANT]: ~10 строк
- [#3 USER]: ~600 (system) + ~100 (action user) = ~700 строк ❌

**Итого на action:** ~1360 строк

**Стало:**
- [SYSTEM]: ~600 строк
- [#1 USER]: ~50 строк (plan user)
- [#2 ASSISTANT]: ~10 строк
- [#3 USER]: ~100 строк (только action user) ✅

**Итого на action:** ~760 строк

**Экономия:** ~600 строк (44%)

### 2. Чистота архитектуры

- ✅ System один раз
- ✅ User только user content
- ✅ Нет дублирования
- ✅ Правильные роли

### 3. Context window

С каждой нодой в истории накапливается меньше токенов:
- Итерация 1: экономия 600 токенов
- Итерация 2: экономия 1200 токенов
- Итерация 3: экономия 1800 токенов
- ...

## Проверка

### До исправления

```bash
python rag_lg_agent.py "тест"
cat logs/005_llm_action_1_request.log

# В логе видим:
# [#3 USER]
# # System Prompt для RAG-агента...  ← дублирование!
# ## ЭТАП: ACTION
```

### После исправления

```bash
python rag_lg_agent.py "тест"
cat logs/005_llm_action_1_request.log

# В логе должно быть:
# [SYSTEM]
# # System Prompt для RAG-агента...  ← один раз!
#
# [#3 USER]
# ## ЭТАП: ACTION  ← только user!
```

## Связанные файлы

### Обновлены (убран include)

- `prompts_v2/action/user.md`
- `prompts_v2/observation.md`
- `prompts_v2/refine.md`
- `prompts_v2/final.md`

### Не изменены (правильно используют include)

- `prompts_v2/action/system.md` - ✅ правильно включает system.md
- `prompts_v2/plan/system.md` - ✅ правильно включает system.md
- `prompts_v2/system.md` - ✅ базовый system prompt

## Best Practices для Jinja2 промптов

### ✅ Правильно

```markdown
<!-- system.md (базовый) -->
# System Prompt
...

<!-- action/system.md -->
{% include 'system.md' %}  ← OK для system файла

<!-- action/user.md -->
## ЭТАП: ACTION  ← Без include!
...
```

### ❌ Неправильно

```markdown
<!-- action/user.md -->
{% include 'system.md' %}  ← НЕТ! Дублирует system!

## ЭТАП: ACTION
```

## Итог

✅ **Убран `{% include 'system.md' %}` из всех user промптов**
✅ **System передается отдельно через системные промпты**
✅ **Экономия ~600 строк на каждое сообщение**
✅ **Правильная архитектура roles**
✅ **Готово к тестированию**

Теперь system prompt не дублируется в истории сообщений! 🎉

