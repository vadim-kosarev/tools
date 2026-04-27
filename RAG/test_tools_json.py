"""Тест генерации JSON списка инструментов."""
import json
from rag_lg_agent import _build_tools_json

# Генерируем JSON
tools_json = _build_tools_json()

# Парсим
tools = json.loads(tools_json)

print(f"\n{'='*80}")
print(f"Сгенерировано инструментов: {len(tools)}")
print(f"Размер JSON: {len(tools_json)} символов")
print(f"{'='*80}\n")

# Показываем первые 3 инструмента
for i, tool in enumerate(tools[:3], 1):
    print(f"{i}. {tool['name']}")
    print(f"   Описание: {tool['description'][:60]}...")
    params = tool.get('parameters', {}).get('properties', {})
    required = tool.get('parameters', {}).get('required', [])
    print(f"   Параметров: {len(params)} (обязательных: {len(required)})")
    if params:
        print(f"   Первые параметры: {list(params.keys())[:3]}")
    print()

# Проверяем структуру одного инструмента
print(f"\n{'='*80}")
print(f"Пример: {tools[0]['name']}")
print(f"{'='*80}")
print(json.dumps(tools[0], ensure_ascii=False, indent=2)[:500])
print("...")

