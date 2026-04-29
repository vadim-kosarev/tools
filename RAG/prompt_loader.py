"""
Утилита для загрузки и рендеринга промптов из .md файлов с использованием Jinja2.

Особенности:
- Загрузка промптов из файловой системы (папка RAG/prompts/ или RAG/prompts_v2/)
- Поддержка Jinja2 шаблонов с переменными {{ variable }}
- Поддержка включения файлов {% include 'file.md' %}
- Кеширование загруженных шаблонов
- Автоматическая обработка placeholders из state
- Путь к промптам конфигурируется через PROMPTS_DIR в .env

Использование:
    loader = PromptLoader()

    # Загрузить и отрендерить промпт
    text = loader.render('plan/system.md', state)

    # Или использовать удобные методы
    text = loader.render_plan_system(state)

Настройка папки с промптами (.env):
    PROMPTS_DIR=prompts       # использовать RAG/prompts/
    PROMPTS_DIR=prompts_v2    # использовать RAG/prompts_v2/
"""

from pathlib import Path
from typing import Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape


class PromptLoader:
    """
    Загрузчик и рендерер промптов на основе Jinja2.

    Все промпты находятся в папке RAG/prompts/ (или prompts_v2/) и используют Jinja2 синтаксис:
    - {{ variable }} - подстановка переменной
    - {% include 'file.md' %} - включение другого промпта
    - {% if condition %}...{% endif %} - условия
    - {% for item in list %}...{% endfor %} - циклы
    
    Путь к промптам можно задать через PROMPTS_DIR в .env или передать в конструктор.
    """

    def __init__(self, prompts_dir: Optional[Path] = None):
        """
        Инициализация загрузчика.

        Args:
            prompts_dir: Путь к папке с промптами.
                        Если None - берется из settings.prompts_dir (из .env: PROMPTS_DIR)
                        По умолчанию: RAG/prompts/
        """
        if prompts_dir is None:
            # Импортируем settings для получения пути к промптам
            try:
                from rag_chat import settings
                prompts_dir_name = settings.prompts_dir
            except ImportError:
                # Fallback если rag_chat недоступен
                prompts_dir_name = 'prompts.bak'
            
            # Строим полный путь относительно этого файла
            prompts_dir = Path(__file__).parent / prompts_dir_name

        self.prompts_dir = Path(prompts_dir)

        # Создаем Jinja2 окружение
        self.env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            autoescape=select_autoescape(['html', 'xml']),  # Отключаем для .md
            trim_blocks=True,  # Убираем пустые строки после блоков
            lstrip_blocks=True,  # Убираем пробелы перед блоками
        )

        # Отключаем autoescape для markdown
        self.env.autoescape = False

    def render(self, template_name: str, state: Dict[str, Any], extra: Dict[str, Any] = None) -> str:
        """
        Загрузить и отрендерить шаблон с подстановкой переменных из state.

        Args:
            template_name: Имя шаблона относительно prompts_dir (например, 'plan/system.md')
            state: Словарь с переменными для подстановки
            extra: Дополнительные переменные, которые перекрывают state (например, error_message)

        Returns:
            Отрендеренный текст промпта

        Example:
            >>> loader = PromptLoader()
            >>> text = loader.render('plan/system.md', {'user_query': 'test'})
        """
        template = self.env.get_template(template_name)
        ctx = {**state, **(extra or {})}
        return template.render(**ctx)

    # ---------------------------------------------------------------------------
    # Удобные методы для каждого типа промпта
    # ---------------------------------------------------------------------------

    # PLAN NODE
    def render_plan_system(self, state: Dict[str, Any]) -> str:
        """System prompt для plan node."""
        return self.render('plan/system.md', state)

    def render_plan_user(self, state: Dict[str, Any]) -> str:
        """User prompt для plan node."""
        return self.render('plan/user.md', state)

    def render_plan_retry(self, state: Dict[str, Any], extra: Dict[str, Any] = None) -> str:
        """Retry prompt для plan node."""
        return self.render('plan/retry.md', state, extra)

    # ACTION NODE
    def render_action_system(self, state: Dict[str, Any]) -> str:
        """System prompt для action node."""
        return self.render('action/system.md', state)

    def render_action_user(self, state: Dict[str, Any]) -> str:
        """User prompt для action node."""
        return self.render('action/user.md', state)

    def render_action_retry(self, state: Dict[str, Any], extra: Dict[str, Any] = None) -> str:
        """Retry prompt для action node."""
        return self.render('action/retry.md', state, extra)

    # OBSERVATION NODE
    def render_observation_system(self, state: Dict[str, Any]) -> str:
        """System prompt для observation node."""
        return self.render('observation/system.md', state)

    def render_observation_user(self, state: Dict[str, Any]) -> str:
        """User prompt для observation node."""
        return self.render('observation/user.md', state)

    def render_observation_retry(self, state: Dict[str, Any], extra: Dict[str, Any] = None) -> str:
        """Retry prompt для observation node."""
        return self.render('observation/retry.md', state, extra)

    # REFINE NODE
    def render_refine_system(self, state: Dict[str, Any]) -> str:
        """System prompt для refine node."""
        return self.render('refine/system.md', state)

    def render_refine_user(self, state: Dict[str, Any]) -> str:
        """User prompt для refine node."""
        return self.render('refine/user.md', state)

    def render_refine_retry(self, state: Dict[str, Any], extra: Dict[str, Any] = None) -> str:
        """Retry prompt для refine node."""
        return self.render('refine/retry.md', state, extra)

    # FINAL NODE
    def render_final_system(self, state: Dict[str, Any]) -> str:
        """System prompt для final node."""
        return self.render('final/system.md', state)

    def render_final_user(self, state: Dict[str, Any]) -> str:
        """User prompt для final node."""
        return self.render('final/user.md', state)

    def render_final_retry(self, state: Dict[str, Any], extra: Dict[str, Any] = None) -> str:
        """Retry prompt для final node."""
        return self.render('final/retry.md', state, extra)


# Глобальный экземпляр загрузчика для удобства
_loader = None


def get_loader() -> PromptLoader:
    """Получить глобальный экземпляр загрузчика (singleton)."""
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader

