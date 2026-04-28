"""
Утилита для загрузки и рендеринга промптов из .md файлов с использованием Jinja2.

Особенности:
- Загрузка промптов из файловой системы (папка RAG/prompts/)
- Поддержка Jinja2 шаблонов с переменными {{ variable }}
- Поддержка включения файлов {% include 'file.md' %}
- Кеширование загруженных шаблонов
- Автоматическая обработка placeholders из state

Использование:
    loader = PromptLoader()

    # Загрузить и отрендерить промпт
    text = loader.render('plan/system.md', state)

    # Или использовать удобные методы
    text = loader.render_plan_system(state)
"""

from pathlib import Path
from typing import Dict, Any
from jinja2 import Environment, FileSystemLoader, select_autoescape


class PromptLoader:
    """
    Загрузчик и рендерер промптов на основе Jinja2.

    Все промпты находятся в папке RAG/prompts/ и используют Jinja2 синтаксис:
    - {{ variable }} - подстановка переменной
    - {% include 'file.md' %} - включение другого промпта
    - {% if condition %}...{% endif %} - условия
    - {% for item in list %}...{% endfor %} - циклы
    """

    def __init__(self, prompts_dir: Path = None):
        """
        Инициализация загрузчика.

        Args:
            prompts_dir: Путь к папке с промптами.
                        По умолчанию: RAG/prompts/ относительно этого файла
        """
        if prompts_dir is None:
            # По умолчанию - папка prompts/ рядом с этим файлом
            prompts_dir = Path(__file__).parent / 'prompts'

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

    def render(self, template_name: str, state: Dict[str, Any]) -> str:
        """
        Загрузить и отрендерить шаблон с подстановкой переменных из state.

        Args:
            template_name: Имя шаблона относительно prompts_dir (например, 'plan/system.md')
            state: Словарь с переменными для подстановки

        Returns:
            Отрендеренный текст промпта

        Example:
            >>> loader = PromptLoader()
            >>> text = loader.render('plan/system.md', {'user_query': 'test'})
        """
        template = self.env.get_template(template_name)
        return template.render(**state)

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

    def render_plan_retry(self, state: Dict[str, Any]) -> str:
        """Retry prompt для plan node."""
        return self.render('plan/retry.md', state)

    # ACTION NODE
    def render_action_system(self, state: Dict[str, Any]) -> str:
        """System prompt для action node."""
        return self.render('action/system.md', state)

    def render_action_user(self, state: Dict[str, Any]) -> str:
        """User prompt для action node."""
        return self.render('action/user.md', state)

    def render_action_retry(self, state: Dict[str, Any]) -> str:
        """Retry prompt для action node."""
        return self.render('action/retry.md', state)

    # OBSERVATION NODE
    def render_observation_system(self, state: Dict[str, Any]) -> str:
        """System prompt для observation node."""
        return self.render('observation/system.md', state)

    def render_observation_user(self, state: Dict[str, Any]) -> str:
        """User prompt для observation node."""
        return self.render('observation/user.md', state)

    def render_observation_retry(self, state: Dict[str, Any]) -> str:
        """Retry prompt для observation node."""
        return self.render('observation/retry.md', state)

    # REFINE NODE
    def render_refine_system(self, state: Dict[str, Any]) -> str:
        """System prompt для refine node."""
        return self.render('refine/system.md', state)

    def render_refine_user(self, state: Dict[str, Any]) -> str:
        """User prompt для refine node."""
        return self.render('refine/user.md', state)

    def render_refine_retry(self, state: Dict[str, Any]) -> str:
        """Retry prompt для refine node."""
        return self.render('refine/retry.md', state)

    # FINAL NODE
    def render_final_system(self, state: Dict[str, Any]) -> str:
        """System prompt для final node."""
        return self.render('final/system.md', state)

    def render_final_user(self, state: Dict[str, Any]) -> str:
        """User prompt для final node."""
        return self.render('final/user.md', state)


# Глобальный экземпляр загрузчика для удобства
_loader = None


def get_loader() -> PromptLoader:
    """Получить глобальный экземпляр загрузчика (singleton)."""
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader

