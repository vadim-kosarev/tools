# Рефакторинг: Удален лишний слой промптов

## Проблема

Была избыточная цепочка вызовов:

```python
# rag_lg_agent.py
prompts.get_plan_system_prompt(state)
    ↓
# prompts.py
def get_plan_system_prompt(state: dict) -> str:
    return _loader.render_plan_system(state)
    ↓
# prompt_loader.py
def render_plan_system(self, state: Dict[str, Any]) -> str:
    return self.render('plan/system.md', state)
```

**3 слоя** для одного действия!

## Решение

Убран промежуточный слой `prompts.py`. Прямой вызов loader:

```python
# rag_lg_agent.py
_prompt_loader.render_plan_system(state)
    ↓
# prompt_loader.py
def render_plan_system(self, state: Dict[str, Any]) -> str:
    return self.render('plan/system.md', state)
```

**2 слоя** — проще и понятнее.

## Изменения

### rag_lg_agent.py

**Было:**
```python
import prompts

# Где-то в коде
system_message = prompts.get_plan_system_prompt(state)
user_message = prompts.get_plan_user_prompt(state)
```

**Стало:**
```python
from prompt_loader import get_loader

_prompt_loader = get_loader()

# Где-то в коде
system_message = _prompt_loader.render_plan_system(state)
user_message = _prompt_loader.render_plan_user(state)
```

### Полный список замен

| Было | Стало |
|------|-------|
| `prompts.get_plan_system_prompt(state)` | `_prompt_loader.render_plan_system(state)` |
| `prompts.get_plan_user_prompt(state)` | `_prompt_loader.render_plan_user(state)` |
| `prompts.get_plan_retry_prompt(state)` | `_prompt_loader.render_plan_retry(state)` |
| `prompts.get_action_system_prompt(state)` | `_prompt_loader.render_action_system(state)` |
| `prompts.get_action_user_prompt(state)` | `_prompt_loader.render_action_user(state)` |
| `prompts.get_action_retry_prompt(state)` | `_prompt_loader.render_action_retry(state)` |
| `prompts.get_observation_system_prompt(state)` | `_prompt_loader.render_observation_system(state)` |
| `prompts.get_observation_user_prompt(state)` | `_prompt_loader.render_observation_user(state)` |
| `prompts.get_refine_system_prompt(state)` | `_prompt_loader.render_refine_system(state)` |
| `prompts.get_refine_user_prompt(state)` | `_prompt_loader.render_refine_user(state)` |
| `prompts.get_refine_retry_prompt(state)` | `_prompt_loader.render_refine_retry(state)` |
| `prompts.get_final_system_prompt(state)` | `_prompt_loader.render_final_system(state)` |
| `prompts.get_final_user_prompt(state)` | `_prompt_loader.render_final_user(state)` |
| `prompts.get_system_prompt_base(state)` | `_prompt_loader.render('system_prompt_base.md', state)` |

## Преимущества

### 1. Меньше кода
- Удален файл-обёртка `prompts.py` (193 строки) из цепочки вызовов
- Нет промежуточных функций-прокси

### 2. Понятнее
- Явно видно что используется `PromptLoader`
- Меньше слоёв абстракции
- Очевиднее путь к промптам

### 3. Гибче
- Можно использовать `_prompt_loader.render('путь/к/файлу.md', state)` для любых промптов
- Не нужно создавать обёртки для новых промптов
- Единая точка входа

### 4. Быстрее
- Один уровень вызовов вместо двух
- Меньше overhead

## Обратная совместимость

Файл `prompts.py` можно оставить для старого кода:

```python
"""
DEPRECATED: Используйте prompt_loader напрямую.

Старый код:
    prompts.get_plan_system_prompt(state)

Новый код:
    from prompt_loader import get_loader
    _loader = get_loader()
    _loader.render_plan_system(state)
"""

from prompt_loader import get_loader

_loader = get_loader()

# Обратная совместимость
def get_plan_system_prompt(state):
    return _loader.render_plan_system(state)
# ... и т.д.
```

Но в текущем коде он больше не нужен.

## Дальнейшие улучшения

Можно ещё упростить, используя прямой вызов render:

```python
# Вместо
_prompt_loader.render_plan_system(state)

# Можно
_prompt_loader.render('plan/system.md', state)
```

Методы `render_plan_system`, `render_action_user` и т.д. — тоже обёртки, но:
- ✅ Они в одном месте (`prompt_loader.py`)
- ✅ Дают автодополнение и type hints
- ✅ Не добавляют много слоёв
- ✅ Удобны для частых вызовов

Поэтому оставляем их, а промежуточный слой `prompts.py` убираем.

## Тестирование

```bash
# Код должен работать как раньше
python rag_lg_agent.py "тестовый вопрос"

# Логи должны генерироваться так же
ls logs/
```

Функциональность не меняется — только архитектура.

## Связанные изменения

В той же сессии:
- Увеличен `MAX_ITERATIONS` с 2 до 5
- Упрощены промпты в `prompts/plan/`
- Создана новая версия промптов `prompts_v2/` (альтернативный подход)

## Итог

**3 уровня → 2 уровня**

```
Было:
rag_lg_agent.py → prompts.py → prompt_loader.py → файл.md

Стало:
rag_lg_agent.py → prompt_loader.py → файл.md
```

Проще, понятнее, чище! 🎉

