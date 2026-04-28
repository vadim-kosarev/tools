"""
Централизованное хранилище промптов для RAG-агента.

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
            system.md             - System message для plan_node
            user.md               - User message для plan_node
            retry.md              - Retry message
        action/system.md, user.md, retry.md
        observation/system.md, user.md, retry.md
        refine/system.md, user.md, retry.md
        final/system.md, user.md

Использование IDE Find Usages:
    - Чтобы найти где используется промпт - нажмите на функцию и выберите Find Usages
    - Чтобы изменить промпт - редактируйте соответствующий .md файл в папке prompts/

Структура API (все функции имеют единую сигнатуру (state: dict)):
    - get_system_prompt_base(state) - базовый системный промпт
    - get_plan_system_prompt(state) - system message для plan_node
    - get_plan_user_prompt(state) - user message для plan_node
    - get_plan_retry_prompt(state) - retry message для plan_node
    - get_action_system_prompt(state) - system message для action_node
    - get_action_user_prompt(state) - user message для action_node
    - get_action_retry_prompt(state) - retry message для action_node
    - get_observation_system_prompt(state) - system message для observation_node
    - get_observation_user_prompt(state) - user message для observation_node
    - get_observation_retry_prompt(state) - retry message для observation_node
    - get_refine_system_prompt(state) - system message для refine_node
    - get_refine_user_prompt(state) - user message для refine_node
    - get_refine_retry_prompt(state) - retry message для refine_node
    - get_final_system_prompt(state) - system message для final_node
    - get_final_user_prompt(state) - user message для final_node

Ожидаемые ключи в state:
    - system_prompt: str - базовый системный промпт (опционально, для совместимости)
    - user_query: str - вопрос пользователя
    - iteration: int - текущая итерация
    - MAX_ITERATIONS: int - максимальное количество итераций
    - step: int - текущий шаг
    - plan: list - план поиска
    - refinement_plan: list - план уточнения (опционально)
    - observation: str - результат наблюдения
    - all_tool_results: list - все результаты инструментов
    - tools_json: str - JSON с результатами инструментов (для observation_node)
    - total_tools: int - общее количество выполненных инструментов (для final_node)
    - all_results_json: str - JSON со всеми результатами (для final_node)
    - available_tools: str - JSON со списком доступных инструментов (для system_prompt_base)
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
    """Retry message для plan node. Загружается из plan/retry.md."""
    return _loader.render_plan_retry(state)


# ---------------------------------------------------------------------------
# ACTION NODE PROMPTS
# ---------------------------------------------------------------------------

def get_action_system_prompt(state: dict) -> str:
    """System message для action node. Загружается из action/system.md."""
    return _loader.render_action_system(state)


def get_action_user_prompt(state: dict) -> str:
    """User message для action node. Загружается из action/user.md."""
    return _loader.render_action_user(state)


def get_action_retry_prompt(state: dict) -> str:
    """Retry message для action node. Загружается из action/retry.md."""
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
    """Retry message для observation node. Загружается из observation/retry.md."""
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
    """Retry message для refine node. Загружается из refine/retry.md."""
    return _loader.render_refine_retry(state)


# ---------------------------------------------------------------------------
# FINAL NODE PROMPTS
# ---------------------------------------------------------------------------

def get_final_system_prompt(state: dict) -> str:
    """System message для final node. Загружается из final/system.md."""
    return _loader.render_final_system(state)


def get_final_user_prompt(state: dict) -> str:
    """User message для final node. Загружается из final/user.md."""
    return _loader.render_final_user(state)

