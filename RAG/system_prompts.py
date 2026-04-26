"""
Системные промпты для RAG агентов.

Централизованное хранилище промптов с поддержкой:
  - Загрузка из файлов (system_prompt.md)
  - Шаблонизация через f-strings
  - Кэширование загруженных промптов

Использование:
    from system_prompts import load_system_prompt, ANALYTICAL_AGENT_PROMPT

    # Загрузка из файла
    prompt = load_system_prompt()

    # Или использование готовой константы
    prompt = ANALYTICAL_AGENT_PROMPT
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Путь к файлу с системным промптом
_SYSTEM_PROMPT_FILE = Path(__file__).parent / "system_prompt.md"

# Кэш загруженных промптов
_PROMPT_CACHE: dict[str, str] = {}


def load_system_prompt(
    file_path: Path | None = None,
    force_reload: bool = False,
) -> str:
    """
    Загружает системный промпт из markdown файла.

    Args:
        file_path: Путь к файлу с промптом (по умолчанию system_prompt.md)
        force_reload: Принудительная перезагрузка (игнорируя кэш)

    Returns:
        Системный промпт в виде строки
    """
    if file_path is None:
        file_path = _SYSTEM_PROMPT_FILE

    cache_key = str(file_path)

    # Проверяем кэш
    if not force_reload and cache_key in _PROMPT_CACHE:
        logger.debug(f"Системный промпт загружен из кэша: {file_path.name}")
        return _PROMPT_CACHE[cache_key]

    # Загружаем из файла
    try:
        content = file_path.read_text(encoding="utf-8")
        _PROMPT_CACHE[cache_key] = content
        logger.info(f"Системный промпт загружен: {file_path.name} ({len(content)} символов)")
        return content
    except Exception as exc:
        logger.error(f"Ошибка загрузки системного промпта из {file_path}: {exc}")
        # Возвращаем fallback промпт
        return _FALLBACK_PROMPT


def format_system_prompt(
    template: str,
    **kwargs: Any,
) -> str:
    """
    Форматирует системный промпт с подстановкой переменных.

    Args:
        template: Шаблон промпта
        **kwargs: Переменные для подстановки

    Returns:
        Отформатированный промпт

    Example:
        >>> prompt = format_system_prompt(
        ...     template=load_system_prompt(),
        ...     available_tools=["search", "read"],
        ...     max_iterations=5,
        ... )
    """
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        logger.warning(f"Переменная {exc} не найдена в промпте, пропускаем")
        return template


# ---------------------------------------------------------------------------
# Готовые промпты
# ---------------------------------------------------------------------------

# Аналитический агент (из system_prompt.md)
ANALYTICAL_AGENT_PROMPT = load_system_prompt()

# Fallback промпт на случай ошибки загрузки
_FALLBACK_PROMPT = """# System Prompt для AI-Агента

Ты — аналитический AI-агент, работающий с документацией через tools.

**Основной принцип:** работай итеративно через plan → action → observation → final.

**Формат ответа:** строго структурированный JSON.

```json
{
  "status": "plan | action | final | error",
  "step": 1,
  "thought": "краткое рассуждение",
  "plan": ["шаг 1", "шаг 2"],
  "action": {
    "tool": "tool_name",
    "input": {}
  },
  "observation": "результат предыдущего шага",
  "final_answer": {
    "summary": "краткий ответ",
    "details": "подробное объяснение",
    "data": [],
    "sources": [],
    "confidence": 0.0
  }
}
```

**Правила:**
- Не выдумывай данные
- Используй tools для проверки фактов
- Учитывай историю сообщений (messages)
- Возвращай только JSON, без текста вне структуры
"""

# Промпт для simple чата (без JSON структуры)
SIMPLE_CHAT_PROMPT = """Ты - эксперт-аналитик по технической документации системы СОИБ КЦОИ Банка России.

Твоя задача - находить информацию в документации используя доступные инструменты.

СТРОГИЕ ПРАВИЛА:
  [!] ЗАПРЕЩЕНО придумывать, домысливать или использовать общие знания
  [+] Работай ТОЛЬКО с информацией из инструментов (tools)
  [+] Используй историю из messages для понимания контекста
  [+] Если какой-то инструмент не дал результатов - попробуй другой подход
  [+] Каждое утверждение должно иметь источник из найденных данных

Доступные инструменты:
  - semantic_search: семантический поиск по эмбеддингам
  - exact_search: точный поиск по подстроке
  - multi_term_exact_search: поиск по списку терминов с ранжированием
  - find_sections_by_term: поиск разделов содержащих термин
  - find_relevant_sections: двухэтапный поиск разделов
  - get_section_content: полный текст раздела из файла
  - read_table: чтение строк таблицы
  - regex_search: regex-поиск по файлам

Отвечай на русском языке, структурированно, с указанием источников."""

# Промпт для query expansion
QUERY_EXPANSION_PROMPT = """Ты - эксперт по формулированию поисковых запросов для технической документации.

Проанализируй запрос пользователя и сформируй:
  1. Список перефразировок для семантического поиска (2-4 варианта)
  2. Ключевые термины для точного поиска (3-6 коротких фраз)
  3. Regex-паттерны (если нужны IP, порты, подсети)

ВАЖНО:
  - Раскрывай аббревиатуры (СУБД → "система управления базами данных")
  - Добавляй синонимы и вариации терминов
  - Формулируй запросы как естественные вопросы для semantic search
  - Для exact terms используй конкретные названия, коды, аббревиатуры

Отвечай ТОЛЬКО структурированным объектом без дополнительных объяснений."""

# Промпт для evaluation ответов
ANSWER_EVALUATION_PROMPT = """Ты - строгий аналитик качества ответов на основе технической документации.

Оцени ответ агента по критериям:
  1. Релевантность - отвечает ли на вопрос пользователя
  2. Полнота - достаточно ли информации
  3. Точность - подтверждены ли факты источниками

ВАЖНО:
  - Оценивай ТОЛЬКО на основе предоставленных данных
  - НЕ домысливай что "могло бы быть"
  - Если в ответе противоречия - снижай оценку
  - Если источники не указаны - снижай оценку

Формат оценки:
  - completeness: 1-5 (насколько полон ответ)
  - relevance: 1-5 (насколько релевантен вопросу)
  - missing: список того, чего не хватает

Отвечай ТОЛЬКО структурированным объектом без дополнительных объяснений."""


# ---------------------------------------------------------------------------
# Утилиты для работы с промптами
# ---------------------------------------------------------------------------

def get_available_prompts() -> dict[str, str]:
    """
    Возвращает словарь доступных промптов.

    Returns:
        Словарь {имя: описание}
    """
    return {
        "analytical_agent": "Аналитический агент с JSON структурой (из system_prompt.md)",
        "simple_chat": "Простой чат без структурированного ответа",
        "query_expansion": "Расширение запроса для поиска",
        "answer_evaluation": "Оценка качества ответа",
    }


def get_prompt_by_name(name: str) -> str:
    """
    Получить промпт по имени.

    Args:
        name: Имя промпта (analytical_agent, simple_chat, etc.)

    Returns:
        Текст промпта

    Raises:
        ValueError: Если промпт не найден
    """
    prompts = {
        "analytical_agent": ANALYTICAL_AGENT_PROMPT,
        "simple_chat": SIMPLE_CHAT_PROMPT,
        "query_expansion": QUERY_EXPANSION_PROMPT,
        "answer_evaluation": ANSWER_EVALUATION_PROMPT,
    }

    if name not in prompts:
        available = ", ".join(prompts.keys())
        raise ValueError(f"Промпт '{name}' не найден. Доступные: {available}")

    return prompts[name]


def print_prompt_info() -> None:
    """Выводит информацию о доступных промптах."""
    print("\n" + "=" * 80)
    print("ДОСТУПНЫЕ СИСТЕМНЫЕ ПРОМПТЫ")
    print("=" * 80 + "\n")

    for name, description in get_available_prompts().items():
        prompt = get_prompt_by_name(name)
        lines = prompt.count("\n") + 1
        chars = len(prompt)
        print(f"📝 {name}")
        print(f"   {description}")
        print(f"   Размер: {lines} строк, {chars:,} символов")
        print()

    print("=" * 80)
    print(f"\nИспользование:")
    print(f"  from system_prompts import get_prompt_by_name")
    print(f"  prompt = get_prompt_by_name('analytical_agent')")
    print()


# ---------------------------------------------------------------------------
# CLI для проверки
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI для проверки загрузки промптов."""
    import argparse

    parser = argparse.ArgumentParser(description="Утилита работы с системными промптами")
    parser.add_argument("--list", action="store_true", help="Показать список промптов")
    parser.add_argument("--show", metavar="NAME", help="Показать промпт по имени")
    parser.add_argument("--reload", action="store_true", help="Перезагрузить из файла")

    args = parser.parse_args()

    if args.list:
        print_prompt_info()
    elif args.show:
        try:
            prompt = get_prompt_by_name(args.show)
            print(f"\n{'=' * 80}")
            print(f"ПРОМПТ: {args.show}")
            print(f"{'=' * 80}\n")
            print(prompt)
            print(f"\n{'=' * 80}\n")
        except ValueError as exc:
            print(f"❌ Ошибка: {exc}")
    else:
        print_prompt_info()


if __name__ == "__main__":
    main()

