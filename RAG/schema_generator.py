"""
Утилита для генерации JSON schema из Pydantic моделей для использования в промптах.

Генерирует читаемые примеры JSON с описаниями полей для LLM промптов.
"""

import json
from typing import Type, Any, get_origin, get_args
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo


def generate_json_example(model: Type[BaseModel], indent: int = 2) -> str:
    """
    Генерирует пример JSON с описаниями из Pydantic модели.

    Args:
        model: Pydantic модель
        indent: Отступ для форматирования

    Returns:
        Строка с JSON примером и комментариями

    Example:
        >>> class MyModel(BaseModel):
        ...     name: str = Field(description="User name")
        ...     age: int = Field(description="User age", ge=0)
        >>> print(generate_json_example(MyModel))
        {
          "name": "string",  // User name
          "age": 0  // User age (>=0)
        }
    """
    example = {}
    comments = {}

    for field_name, field_info in model.model_fields.items():
        # Получаем тип поля
        field_type = field_info.annotation

        # Генерируем пример значения
        example_value = _get_example_value(field_type, field_info)
        example[field_name] = example_value

        # Собираем комментарий из description и constraints
        comment_parts = []
        if field_info.description:
            comment_parts.append(field_info.description)

        # Добавляем constraints если есть
        constraints = _get_constraints_description(field_info)
        if constraints:
            comment_parts.append(constraints)

        if comment_parts:
            comments[field_name] = " ".join(comment_parts)

    # Форматируем JSON с комментариями
    return _format_json_with_comments(example, comments, indent)


def _get_example_value(field_type: Any, field_info: FieldInfo) -> Any:
    """Генерирует пример значения для поля."""
    origin = get_origin(field_type)

    # Проверяем default значение (но не Ellipsis и не PydanticUndefined)
    if (field_info.default is not None and
        field_info.default is not ... and
        not hasattr(field_info.default, '__class__') or
        field_info.default.__class__.__name__ != 'PydanticUndefinedType'):
        # Есть валидный default
        if callable(field_info.default):
            # default_factory
            try:
                return field_info.default()
            except:
                pass  # Fallback to type-based example
        else:
            return field_info.default

    # list[...]
    if origin is list:
        args = get_args(field_type)
        if args:
            item_type = args[0]
            # Для вложенных моделей
            if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                return [_model_to_dict(item_type)]
            else:
                return [_get_simple_type_example(item_type)]
        return []

    # dict[...]
    if origin is dict:
        return {}

    # Literal["value"]
    if origin is type(None):  # Optional
        args = get_args(field_type)
        if args:
            return _get_example_value(args[0], field_info)
        return None

    # Вложенная Pydantic модель
    if isinstance(field_type, type) and issubclass(field_type, BaseModel):
        return _model_to_dict(field_type)

    # Простые типы
    return _get_simple_type_example(field_type)


def _get_simple_type_example(field_type: Any) -> Any:
    """Возвращает пример значения для простого типа."""
    type_examples = {
        str: "string",
        int: 0,
        float: 0.0,
        bool: False,
    }

    # Проверяем Literal
    origin = get_origin(field_type)
    if hasattr(field_type, '__origin__'):  # Literal check
        args = get_args(field_type)
        if args:
            return args[0]  # Первое значение из Literal

    return type_examples.get(field_type, "value")


def _model_to_dict(model: Type[BaseModel]) -> dict:
    """Конвертирует модель в словарь с примерами значений."""
    result = {}
    for field_name, field_info in model.model_fields.items():
        result[field_name] = _get_example_value(field_info.annotation, field_info)
    return result


def _get_constraints_description(field_info: FieldInfo) -> str:
    """Извлекает описание constraints из метаданных поля."""
    parts = []

    # Проверяем metadata для constraints
    if hasattr(field_info, 'metadata'):
        for meta in field_info.metadata:
            if hasattr(meta, 'ge'):
                parts.append(f">={meta.ge}")
            if hasattr(meta, 'le'):
                parts.append(f"<={meta.le}")
            if hasattr(meta, 'gt'):
                parts.append(f">{meta.gt}")
            if hasattr(meta, 'lt'):
                parts.append(f"<{meta.lt}")

    return ", ".join(parts) if parts else ""


def _format_json_with_comments(data: dict, comments: dict, indent: int = 2) -> str:
    """
    Форматирует JSON с инлайн комментариями.

    Args:
        data: Данные для форматирования
        comments: Словарь {field_name: comment}
        indent: Размер отступа

    Returns:
        Отформатированный JSON с комментариями
    """
    lines = []
    lines.append("{")

    items = list(data.items())
    for i, (key, value) in enumerate(items):
        is_last = i == len(items) - 1
        indent_str = " " * indent

        # Форматируем значение
        value_str = json.dumps(value, ensure_ascii=False, indent=indent)

        # Если значение многострочное, форматируем его правильно
        if "\n" in value_str:
            value_lines = value_str.split("\n")
            value_str = value_lines[0]
            for line in value_lines[1:]:
                value_str += "\n" + indent_str + line

        # Основная строка
        comma = "," if not is_last else ""
        line = f'{indent_str}"{key}": {value_str}{comma}'

        # Добавляем комментарий если есть
        if key in comments:
            line += f'  // {comments[key]}'

        lines.append(line)

    lines.append("}")
    return "\n".join(lines)


def generate_schema_for_prompt(model: Type[BaseModel]) -> str:
    """
    Генерирует JSON schema для использования в промпте.

    Включает:
    - Пример валидного JSON
    - Описания всех полей
    - Constraints

    Args:
        model: Pydantic модель

    Returns:
        Отформатированная строка для промпта
    """
    return f"""{generate_json_example(model)}"""


# Удобные функции для быстрого доступа к schema каждой ноды
def get_plan_schema() -> str:
    """JSON schema для plan ноды."""
    from rag_lg_agent import AgentPlan
    return generate_schema_for_prompt(AgentPlan)


def get_action_schema() -> str:
    """JSON schema для action ноды."""
    from rag_lg_agent import AgentAction
    return generate_schema_for_prompt(AgentAction)


def get_observation_schema() -> str:
    """JSON schema для observation ноды."""
    from rag_lg_agent import AgentObservation
    return generate_schema_for_prompt(AgentObservation)


def get_refine_schema() -> str:
    """JSON schema для refine ноды."""
    from rag_lg_agent import AgentRefine
    return generate_schema_for_prompt(AgentRefine)


def get_final_schema() -> str:
    """JSON schema для final ноды."""
    from rag_lg_agent import AgentFinal
    return generate_schema_for_prompt(AgentFinal)


if __name__ == "__main__":
    # Тестирование генерации schema
    print("=== PLAN SCHEMA ===")
    print(get_plan_schema())
    print("\n=== ACTION SCHEMA ===")
    print(get_action_schema())
    print("\n=== OBSERVATION SCHEMA ===")
    print(get_observation_schema())
    print("\n=== REFINE SCHEMA ===")
    print(get_refine_schema())
    print("\n=== FINAL SCHEMA ===")
    print(get_final_schema())

