"""
Утилиты для работы с Pydantic моделями.

Функции для конвертации Pydantic моделей в читаемый Markdown формат
и извлечения статистической информации из результатов инструментов.
"""
from __future__ import annotations

from typing import Any, Type
from pydantic import BaseModel


def pydantic_schema_to_markdown(schema: Type[BaseModel]) -> str:
    """
    Конвертирует Pydantic схему (класс BaseModel) в читаемый Markdown.
    
    Используется для документирования параметров инструментов.
    Показывает все поля с типами, значениями по умолчанию и описаниями.
    
    Args:
        schema: Класс Pydantic BaseModel (не экземпляр!)
    
    Returns:
        Строка в Markdown формате
        
    Examples:
        >>> from pydantic import BaseModel, Field
        >>> class SearchInput(BaseModel):
        ...     query: str = Field(description="Search query")
        ...     limit: int = Field(default=10, description="Max results")
        >>> print(pydantic_schema_to_markdown(SearchInput))
        - **`query`**: `str` **(обязательный)**
          - Search query
        - **`limit`**: `int` = `10`
          - Max results
    """
    lines = []
    
    if not hasattr(schema, 'model_fields'):
        return "_(нет параметров)_"
    
    for field_name, field_info in schema.model_fields.items():
        # Получаем тип поля
        field_type = str(field_info.annotation)
        # Убираем лишние символы
        field_type = field_type.replace("typing.", "")
        field_type = field_type.replace("<class '", "").replace("'>", "")
        # Заменяем Optional на | None
        if "Optional[" in field_type:
            field_type = field_type.replace("Optional[", "").replace("]", " | None")
        # Заменяем List/Dict на list/dict
        field_type = field_type.replace("List[", "list[").replace("Dict[", "dict[")
        
        # Определяем обязательность поля
        is_required = field_info.is_required()
        
        # Значение по умолчанию
        default_info = ""
        if not is_required:
            if hasattr(field_info, 'default') and field_info.default is not None:
                default_val = field_info.default
                # Для пустой строки показываем явно
                if default_val == "":
                    default_info = ' = `""`'
                else:
                    # Экранируем для markdown
                    default_str = str(default_val).replace("`", "\\`")
                    default_info = f" = `{default_str}`"
            else:
                default_info = " = `None`"
        else:
            default_info = " **(обязательный)**"
        
        # Описание поля
        field_desc = field_info.description or ""
        
        # Формируем строку параметра
        lines.append(f"- **`{field_name}`**: `{field_type}`{default_info}")
        
        # Добавляем описание с отступом
        if field_desc:
            # Разбиваем многострочное описание
            desc_lines = field_desc.split("\n")
            for desc_line in desc_lines:
                desc_line = desc_line.strip()
                if desc_line:
                    lines.append(f"  - {desc_line}")
    
    if not lines:
        return "_(нет параметров)_"
    
    return "\n".join(lines)


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
                    # Если значение - список, раскрываем его содержимое
                    if isinstance(v, list):
                        lines.append(f"{prefix}  - **{k}:** ({len(v)} элементов)")
                        for i, item in enumerate(v):
                            lines.append(f"{prefix}    {i+1}. {_format_item(item, indent + 3)}")
                    else:
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
    Для ChunkResult (dict с metadata) выводит source и section явно для удобства агента.
    """
    # Dict с metadata (ChunkResult после model_dump)
    if isinstance(item, dict) and 'metadata' in item and isinstance(item.get('metadata'), dict):
        metadata = item['metadata']
        # Извлекаем ключевые поля из metadata
        source = metadata.get('source', '?')
        section = metadata.get('section', '?')
        line_start = metadata.get('line_start', '?')
        
        # ВАЖНО: Выводим content ПОЛНОСТЬЮ, БЕЗ ОБРЕЗКИ!
        content = item.get('content', '')

        # Формируем компактный вывод с явными source и section
        return f"ChunkResult(source={source}, section={section}, line={line_start}, content={repr(content)})"

    # Pydantic BaseModel
    elif isinstance(item, BaseModel):
        # Для Pydantic моделей показываем все поля в одну строку
        fields = item.model_dump()
        parts = [f"{k}={_format_value(v)}" for k, v in fields.items()]
        return f"{item.__class__.__name__}({', '.join(parts)})"
    
    # Обычный dict
    elif isinstance(item, dict):
        # Показываем все ключи
        parts = [f"{k}={_format_value(v)}" for k, v in item.items()]
        return "{" + ", ".join(parts) + "}"
    
    # Список
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


def pydantic_to_markdown_detailed(obj: Any, show_full_metadata: bool = True) -> str:
    """
    Конвертирует Pydantic объект в детальный Markdown с табличным представлением чанков.

    Эта версия раскрывает ВСЕ поля metadata в виде таблицы для удобства чтения агентом.
    Особенно полезна для детального анализа результатов поиска.

    Args:
        obj: Объект для конвертации (обычно SearchChunksResult или MultiTermSearchResult)
        show_full_metadata: Если True, показывает все поля metadata в таблице

    Returns:
        Строка в Markdown формате с таблицами

    Examples:
        >>> result = SearchChunksResult(...)
        >>> print(pydantic_to_markdown_detailed(result))
        **SearchChunksResult**
        Query: "PostgreSQL"
        Total found: 2

        | # | Source | Section | Line | Content |
        |---|--------|---------|------|---------|
        | 1 | database_servers.md | Серверы СУБД > PostgreSQL | 150 | PostgreSQL установлен... |
        | 2 | technical_spec.md | Программное обеспечение > Базы данных | 230 | Кластер PostgreSQL... |
    """
    if not isinstance(obj, BaseModel):
        # Для не-Pydantic объектов используем обычное форматирование
        return pydantic_to_markdown(obj)

    lines = []
    lines.append(f"**{obj.__class__.__name__}**\n")

    data = obj.model_dump()

    # Извлекаем ключевые поля
    query = data.get('query', data.get('substring', data.get('pattern', '')))
    total = data.get('total_found', data.get('total_chunks', data.get('total_matches', 0)))

    if query:
        lines.append(f"Query: \"{query}\"")
    if total:
        lines.append(f"Total found: {total}")
    lines.append("")

    # Если есть список чанков - форматируем как таблицу
    if 'chunks' in data and data['chunks']:
        chunks = data['chunks']
        lines.append("| # | Source | Section | Line | Content |")
        lines.append("|---|--------|---------|------|---------|")

        for i, chunk in enumerate(chunks, 1):
            if isinstance(chunk, dict) and 'metadata' in chunk:
                meta = chunk['metadata']
                source = meta.get('source', '?')
                section = meta.get('section', '?')
                line = meta.get('line_start', '?')
                content = chunk.get('content', '')

                # Обрезаем длинный content для таблицы
                if len(content) > 50:
                    content = content[:47] + '...'

                # Экранируем pipe символы
                content = content.replace('|', '\\|')
                section = section.replace('|', '\\|')

                lines.append(f"| {i} | {source} | {section} | {line} | {content} |")

    # Если есть chunks_by_coverage - форматируем по группам
    elif 'chunks_by_coverage' in data and data['chunks_by_coverage']:
        coverage = data['chunks_by_coverage']
        for match_count in sorted(coverage.keys(), reverse=True):
            chunks = coverage[match_count]
            lines.append(f"\n**Chunks matching {match_count} terms:**\n")
            lines.append("| # | Source | Section | Line | Content |")
            lines.append("|---|--------|---------|------|---------|")

            for i, chunk in enumerate(chunks, 1):
                if isinstance(chunk, dict) and 'metadata' in chunk:
                    meta = chunk['metadata']
                    source = meta.get('source', '?')
                    section = meta.get('section', '?')
                    line = meta.get('line_start', '?')
                    content = chunk.get('content', '')

                    if len(content) > 50:
                        content = content[:47] + '...'

                    content = content.replace('|', '\\|')
                    section = section.replace('|', '\\|')

                    lines.append(f"| {i} | {source} | {section} | {line} | {content} |")

    # Если есть sections - форматируем как таблицу
    elif 'sections' in data and data['sections']:
        sections = data['sections']
        lines.append("| # | Source | Section | Matches | Type |")
        lines.append("|---|--------|---------|---------|------|")

        for i, sec in enumerate(sections, 1):
            if isinstance(sec, dict):
                source = sec.get('source', '?')
                section = sec.get('section', '?')
                match_count = sec.get('match_count', 0)
                match_type = sec.get('match_type', '-')

                section = section.replace('|', '\\|')

                lines.append(f"| {i} | {source} | {section} | {match_count} | {match_type} |")

    # Для остальных полей - обычный вывод
    else:
        for key, value in data.items():
            if key not in ['query', 'substring', 'pattern', 'total_found', 'total_chunks',
                          'total_matches', 'chunks', 'chunks_by_coverage', 'sections']:
                lines.append(f"- **{key}:** {value}")

    return "\n".join(lines)
