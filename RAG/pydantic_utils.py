"""
Утилиты для работы с Pydantic моделями.

Функции для конвертации Pydantic моделей в читаемый Markdown формат
и извлечения статистической информации из результатов инструментов.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel


def pydantic_to_markdown(obj: Any, indent: int = 0) -> str:
    """
    Конвертирует Pydantic объект в читаемый Markdown текст.

    Для списков и словарей создаёт структурированное представление.
    Для примитивных типов возвращает строковое представление.
    
    ВАЖНО: Функция выдает ВСЕ данные полностью, БЕЗ СОКРАЩЕНИЙ.

    Args:
        obj: Объект для конвертации (BaseModel, list, dict или примитив)
        indent: Уровень отступа (для рекурсивных вызовов)

    Returns:
        Строка в Markdown формате

    Examples:
        >>> from pydantic import BaseModel, Field
        >>> class User(BaseModel):
        ...     name: str
        ...     age: int
        >>> user = User(name="John", age=30)
        >>> print(pydantic_to_markdown(user))
        **User**
        - **name:** John
        - **age:** 30
    """
    prefix = "  " * indent

    # Pydantic BaseModel
    if isinstance(obj, BaseModel):
        lines = []
        if indent == 0:
            lines.append(f"**{obj.__class__.__name__}**\n")

        for field_name, field_value in obj.model_dump().items():
            if isinstance(field_value, list):
                lines.append(f"{prefix}- **{field_name}:** ({len(field_value)} элементов)")
                if field_value:
                    # Показываем ВСЕ элементы
                    for i, item in enumerate(field_value):
                        lines.append(f"{prefix}  {i+1}. {_format_item(item, indent + 2)}")
            elif isinstance(field_value, dict):
                lines.append(f"{prefix}- **{field_name}:**")
                for k, v in field_value.items():
                    lines.append(f"{prefix}  - **{k}:** {_format_item(v, indent + 2)}")
            elif isinstance(field_value, BaseModel):
                lines.append(f"{prefix}- **{field_name}:**")
                lines.append(pydantic_to_markdown(field_value, indent + 1))
            else:
                lines.append(f"{prefix}- **{field_name}:** {_format_value(field_value)}")

        return "\n".join(lines)

    # Список
    elif isinstance(obj, list):
        if not obj:
            return f"{prefix}_(пусто)_"
        lines = [f"{prefix}**Список** ({len(obj)} элементов):"]
        # Показываем ВСЕ элементы
        for i, item in enumerate(obj):
            lines.append(f"{prefix}  {i+1}. {_format_item(item, indent + 1)}")
        return "\n".join(lines)

    # Словарь
    elif isinstance(obj, dict):
        if not obj:
            return f"{prefix}_(пусто)_"
        lines = [f"{prefix}**Словарь** ({len(obj)} ключей):"]
        # Показываем ВСЕ пары
        for k, v in obj.items():
            lines.append(f"{prefix}  - **{k}:** {_format_value(v)}")
        return "\n".join(lines)

    # Примитивный тип
    else:
        return f"{prefix}{_format_value(obj)}"


def _format_value(value: Any) -> str:
    """
    Форматирует значение для вывода.
    
    Выводит значение ПОЛНОСТЬЮ, без сокращений.
    """
    if value is None:
        return "_(не задано)_"
    elif isinstance(value, str):
        return value  # Выводим полностью, без обрезки
    elif isinstance(value, (int, float, bool)):
        return str(value)
    else:
        return str(value)  # Выводим полностью, без обрезки


def _format_item(item: Any, indent: int = 0) -> str:
    """
    Форматирует элемент списка или словаря.
    
    Выводит данные ПОЛНОСТЬЮ, без сокращений.
    """
    if isinstance(item, BaseModel):
        # Для Pydantic моделей показываем все поля в одну строку
        fields = item.model_dump()
        parts = [f"{k}={_format_value(v)}" for k, v in fields.items()]
        return f"{item.__class__.__name__}({', '.join(parts)})"
    elif isinstance(item, dict):
        # Показываем все ключи
        parts = [f"{k}={_format_value(v)}" for k, v in item.items()]
        return "{" + ", ".join(parts) + "}"
    elif isinstance(item, list):
        # Для списков показываем количество элементов
        return f"[...] ({len(item)} элементов)"
    else:
        return _format_value(item)


def get_result_count(result: Any) -> int | None:
    """
    Извлекает количество элементов из результата инструмента.

    Пытается найти поля: total_found, total_chunks, returned_count, total_sources
    или подсчитывает длину списков sections, chunks, sources, matches.

    Args:
        result: Результат инструмента (Pydantic модель, список, строка)

    Returns:
        Количество элементов или None если не удалось определить

    Examples:
        >>> from pydantic import BaseModel
        >>> class SearchResult(BaseModel):
        ...     sections: list[str]
        ...     total_found: int
        >>> result = SearchResult(sections=["a", "b", "c"], total_found=10)
        >>> get_result_count(result)
        10
    """
    # Pydantic модель
    if isinstance(result, BaseModel):
        data = result.model_dump()

        # Приоритетные поля для подсчёта
        for field in ['total_found', 'total_chunks', 'returned_count', 'total_sources', 'total_matches']:
            if field in data and isinstance(data[field], int):
                return data[field]

        # Подсчёт по спискам
        for field in ['sections', 'chunks', 'sources', 'matches', 'sources_list']:
            if field in data and isinstance(data[field], list):
                return len(data[field])

        return None

    # Список
    elif isinstance(result, list):
        return len(result)

    # Словарь
    elif isinstance(result, dict):
        # Те же приоритетные поля
        for field in ['total_found', 'total_chunks', 'returned_count', 'total_sources', 'total_matches']:
            if field in result and isinstance(result[field], int):
                return result[field]

        # Подсчёт по спискам в словаре
        for field in ['sections', 'chunks', 'sources', 'matches']:
            if field in result and isinstance(result[field], list):
                return len(result[field])

        return None

    # Строка или другое
    else:
        return None


def format_result_summary(result: Any) -> str:
    """
    Создаёт краткое резюме о результате инструмента.

    Args:
        result: Результат инструмента

    Returns:
        Строка с кратким описанием (для логирования)

    Examples:
        >>> from pydantic import BaseModel
        >>> class SearchResult(BaseModel):
        ...     sections: list[str] = []
        ...     total_found: int = 0
        >>> result = SearchResult(sections=["a", "b"], total_found=10)
        >>> format_result_summary(result)
        'SearchResult: 10 элементов'
    """
    if isinstance(result, BaseModel):
        count = get_result_count(result)
        if count is not None:
            return f"{result.__class__.__name__}: {count} элементов"
        else:
            return f"{result.__class__.__name__}"

    elif isinstance(result, list):
        return f"Список: {len(result)} элементов"

    elif isinstance(result, dict):
        count = get_result_count(result)
        if count is not None:
            return f"Словарь: {count} элементов"
        else:
            return f"Словарь: {len(result)} ключей"

    elif isinstance(result, str):
        return f"Строка: {len(result)} символов"

    else:
        return f"{type(result).__name__}"


def format_sections_list(result: Any, max_sections: int = 1000) -> str:
    """
    Форматирует список разделов для использования в LLM промптах.
    
    Создаёт компактный список всех разделов, сгруппированных по файлам.
    Используется для передачи структуры документации в промпт PLANNER.
    
    Args:
        result: Результат инструмента list_all_sections (Pydantic модель или словарь)
        max_sections: Максимальное количество разделов для вывода
        
    Returns:
        Строка с отформатированным списком разделов
        
    Examples:
        >>> result = list_all_sections_tool.invoke({})
        >>> sections_text = format_sections_list(result)
        >>> print(sections_text)
        Доступные разделы документации (827 разделов из 12 файлов):
        
        [F] file1.md:
          • Section A
          • Section B
        ...
    """
    from collections import defaultdict
    
    # Извлекаем данные
    if isinstance(result, BaseModel):
        data = result.model_dump()
    elif isinstance(result, dict):
        data = result
    else:
        return str(result)
    
    sections = data.get('sections', [])
    total_found = data.get('total_found', len(sections))
    
    if not sections:
        return "Список разделов пуст"
    
    # Группируем по файлам
    by_file = defaultdict(list)
    for item in sections[:max_sections]:
        if isinstance(item, dict):
            source = item.get('source', '???')
            section = item.get('section', '???')
        else:
            # Если это Pydantic модель
            source = getattr(item, 'source', '???')
            section = getattr(item, 'section', '???')
        by_file[source].append(section)
    
    # Форматируем вывод
    lines = []
    file_count = len(by_file)
    lines.append(
        f"Доступные разделы документации ({total_found} разделов из {file_count} файлов):\n"
    )
    
    for source in sorted(by_file.keys()):
        lines.append(f"[F] {source}:")
        for section in sorted(by_file[source]):
            lines.append(f"  • {section}")
        lines.append("")  # Пустая строка между файлами
    
    if len(sections) > max_sections:
        lines.append(f"... и ещё {len(sections) - max_sections} разделов")
    
    return "\n".join(lines)


