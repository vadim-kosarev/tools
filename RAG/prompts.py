"""
[DEPRECATED] Централизованное хранилище промптов для RAG-агента.

⚠️ ВНИМАНИЕ: Этот модуль больше не используется в основном коде!

Новый код должен использовать prompt_loader напрямую:
    
    from prompt_loader import get_loader
    _loader = get_loader()
    
    # Вместо prompts.get_plan_system_prompt(state)
    _loader.render_plan_system(state)
    
    # Или напрямую
    _loader.render('plan/system.md', state)

Этот файл оставлен только для обратной совместимости со старым кодом.

---

⚠️ ВАЖНО: Промпты теперь хранятся в .md файлах (папка prompts/)
Этот модуль - обертка для обратной совместимости.

Промпты используют Jinja2 шаблоны:
    - {{ variable }} - подстановка переменной из state
    - {% include 'file.md' %} - включение другого промпта
    - {% if condition %}...{% endif %} - условия
    - {% for item in list %}...{% endfor %} - циклы

Структура промптов:
    prompts/
        system_prompt_base.md     - Базовый системный промпт
        plan/
            prompt.md             - Полный промпт (SYSTEM + TOOLS + MESSAGES + USER)
            system.md             - System message для plan_node
            user.md               - User message для plan_node
            user.md              - Retry message
        action/prompt.md, system.md, user.md, user.md
        observation/prompt.md, system.md, user.md, user.md
        refine/prompt.md, system.md, user.md, user.md
        final/prompt.md, system.md, user.md

Использование IDE Find Usages:
    - Чтобы найти где используется промпт - нажмите на функцию и выберите Find Usages
    - Чтобы изменить промпт - редактируйте соответствующий .md файл в папке prompts/

Структура API:
    ПОЛНЫЕ ПРОМПТЫ (рекомендуется использовать):
    - get_plan_prompt(state) - полный промпт для plan_node
    - get_action_prompt(state) - полный промпт для action_node
    - get_observation_prompt(state) - полный промпт для observation_node
    - get_refine_prompt(state) - полный промпт для refine_node
    - get_final_prompt(state) - полный промпт для final_node

    ЧАСТИЧНЫЕ ПРОМПТЫ (для обратной совместимости):
    - get_system_prompt_base(state) - базовый системный промпт
    - get_plan_system_prompt(state), get_plan_user_prompt(state), get_plan_retry_prompt(state)
    - get_action_system_prompt(state), get_action_user_prompt(state), get_action_retry_prompt(state)
    - get_observation_system_prompt(state), get_observation_user_prompt(state), get_observation_retry_prompt(state)
    - get_refine_system_prompt(state), get_refine_user_prompt(state), get_refine_retry_prompt(state)
    - get_final_system_prompt(state), get_final_user_prompt(state)

Ожидаемые ключи в state:
    - user_query: str - вопрос пользователя
    - iteration: int - текущая итерация
    - step: int - текущий шаг
    - MAX_ITERATIONS: int - максимальное количество итераций
    - messages: list[dict] - история сообщений
    - available_tools: str - JSON со списком доступных инструментов
    - tool_calls: list[dict] - вызовы инструментов (для observation)
    - plan: list[str] - план действий
    - refinement_plan: list[str] - план уточнения (для refine)
    - observation: str - результат observation
    - all_tool_results: list[dict] - все результаты инструментов
    - tools_json: str - JSON с результатами инструментов (для observation)
    - total_tools: int - количество выполненных инструментов (для final)
    - all_results_json: str - JSON со всеми результатами (для final)
"""

from prompt_loader import get_loader


# Получаем глобальный экземпляр загрузчика
_loader = get_loader()


# ---------------------------------------------------------------------------
# BASE SYSTEM PROMPT
# ---------------------------------------------------------------------------

def get_system_prompt_base(state: dict) -> str:
    """
    Загружает базовый системный промпт из system_prompt_base.md.

    Ожидаемые ключи в state:
        - available_tools: str - JSON со списком доступных инструментов
    """
    return _loader.render('system_prompt_base.md', state)


# ---------------------------------------------------------------------------
# PLAN NODE PROMPTS
# ---------------------------------------------------------------------------

def get_plan_system_prompt(state: dict) -> str:
    """System message для plan node. Загружается из plan/system.md."""
    return _loader.render_plan_system(state)


def get_plan_user_prompt(state: dict) -> str:
    """User message для plan node. Загружается из plan/user.md."""
    return _loader.render_plan_user(state)


def get_plan_retry_prompt(state: dict) -> str:
    """Retry message для plan node. Загружается из plan/user.md."""
    return _loader.render_plan_retry(state)


# ---------------------------------------------------------------------------
# ACTION NODE PROMPTS
# ---------------------------------------------------------------------------

def get_action_prompt(state: dict) -> str:
    """
    Полный промпт для action node (включает SYSTEM, TOOLS, MESSAGES, USER).
    Загружается из action/prompt.md.

    Использует: action/system.md, action/user.md, available_tools, messages
    """
    return _loader.render('action/prompt.md', state)


def get_action_system_prompt(state: dict) -> str:
    """System message для action node. Загружается из action/system.md."""
    return _loader.render_action_system(state)


def get_action_user_prompt(state: dict) -> str:
    """User message для action node. Загружается из action/user.md."""
    return _loader.render_action_user(state)


def get_action_retry_prompt(state: dict) -> str:
    """Retry message для action node. Загружается из action/user.md."""
    return _loader.render_action_retry(state)


# ---------------------------------------------------------------------------
# OBSERVATION NODE PROMPTS
# ---------------------------------------------------------------------------

def get_observation_system_prompt(state: dict) -> str:
    """System message для observation node. Загружается из observation/system.md."""
    return _loader.render_observation_system(state)


def get_observation_user_prompt(state: dict) -> str:
    """User message для observation node. Загружается из observation/user.md."""
    return _loader.render_observation_user(state)


def get_observation_retry_prompt(state: dict) -> str:
    """Retry message для observation node. Загружается из observation/user.md."""
    return _loader.render_observation_retry(state)


# ---------------------------------------------------------------------------
# REFINE NODE PROMPTS
# ---------------------------------------------------------------------------

def get_refine_system_prompt(state: dict) -> str:
    """System message для refine node. Загружается из refine/system.md."""
    return _loader.render_refine_system(state)


def get_refine_user_prompt(state: dict) -> str:
    """User message для refine node. Загружается из refine/user.md."""
    return _loader.render_refine_user(state)


def get_refine_retry_prompt(state: dict) -> str:
    """Retry message для refine node. Загружается из refine/user.md."""
    return _loader.render_refine_retry(state)


# ---------------------------------------------------------------------------
# FINAL NODE PROMPTS
# ---------------------------------------------------------------------------

def get_final_prompt(state: dict) -> str:
    """
    Полный промпт для final node (включает SYSTEM, MESSAGES, USER).
    Загружается из final/prompt.md.

    Использует: final/system.md, final/user.md, messages
    """
    return _loader.render('final/prompt.md', state)


def get_final_system_prompt(state: dict) -> str:
    """System message для final node. Загружается из final/system.md."""
    return _loader.render_final_system(state)


def get_final_user_prompt(state: dict) -> str:
    """User message для final node. Загружается из final/user.md."""
    return _loader.render_final_user(state)

