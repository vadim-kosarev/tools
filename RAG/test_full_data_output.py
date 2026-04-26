"""
Тест для проверки, что pydantic_to_markdown выводит ВСЕ данные без сокращений.
"""
from pydantic import BaseModel, Field
from pydantic_utils import pydantic_to_markdown


class ItemInfo(BaseModel):
    """Информация об элементе"""
    id: int
    name: str
    description: str
    status: str
    value: float


class TestResult(BaseModel):
    """Тестовый результат с большим количеством элементов"""
    query: str
    items: list[ItemInfo]
    summary: str
    total_count: int


# Создаем тестовые данные с 20 элементами (чтобы проверить, что все выводятся)
test_items = [
    ItemInfo(
        id=i,
        name=f"Item {i}",
        description=f"Very long description for item {i} " * 10,  # Длинная строка
        status="active" if i % 2 == 0 else "inactive",
        value=i * 3.14
    )
    for i in range(1, 21)  # 20 элементов
]

test_result = TestResult(
    query="Test query with a very long text " * 20,  # Длинная строка в запросе
    items=test_items,
    summary="Summary text that is also quite long " * 15,  # Длинная строка в summary
    total_count=20
)

# Форматируем и выводим
print("="*80)
print("ТЕСТ: 20 элементов с длинными строками")
print("="*80)

formatted = pydantic_to_markdown(test_result)
print(formatted)

# Проверяем, что все элементы присутствуют
print("\n" + "="*80)
print("ПРОВЕРКА:")
print("="*80)

lines = formatted.split('\n')
item_lines = [line for line in lines if '{id=' in line]
print(f"Найдено элементов в выводе: {len(item_lines)}")
print(f"Ожидалось: 20")
print(f"Все элементы выведены: {'✅ ДА' if len(item_lines) == 20 else '❌ НЕТ'}")

# Проверяем длинные строки
query_line = [line for line in lines if 'query:' in line][0]
has_ellipsis = '...' in query_line
print(f"\nСтрока query обрезана: {'❌ ДА (плохо!)' if has_ellipsis else '✅ НЕТ (хорошо!)'}")

description_lines = [line for line in lines if 'description=' in line]
if description_lines:
    has_ellipsis_in_desc = any('...' in line for line in description_lines)
    print(f"Описания обрезаны: {'❌ ДА (плохо!)' if has_ellipsis_in_desc else '✅ НЕТ (хорошо!)'}")

print("\n" + "="*80)

