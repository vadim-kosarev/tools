"""Тестовый скрипт для проверки форматирования JSON в логах."""
import json

# Симуляция данных, которые будут в логах
test_json_data = {
    "tool": "exact_search",
    "input": {
        "substring": "smart monitor",
        "limit": 30,
        "chunk_type": ""
    },
    "result": {
        "query": "smart monitor",
        "chunks": [
            {
                "content": "На сервере Smart Monitor...",
                "metadata": {
                    "source": "Общее описание системы.md",
                    "section": "Основные технические решения",
                    "chunk_type": "",
                    "line_start": 1155,
                    "line_end": 1158
                }
            }
        ],
        "total_found": 26
    }
}

# Старый формат (без тегов)
print("=" * 80)
print("СТАРЫЙ ФОРМАТ (без тегов):")
print("=" * 80)
old_format = json.dumps(test_json_data, ensure_ascii=False, indent=2)
print(f"[#5 TOOL_RESULT: exact_search]")
print(old_format)

# Новый формат (с тегами ```json)
print("\n" + "=" * 80)
print("НОВЫЙ ФОРМАТ (с тегами ```json):")
print("=" * 80)
json_content = json.dumps(test_json_data, ensure_ascii=False, indent=2)
new_format = f"```json\n{json_content}\n```"
print(f"[#5 TOOL_RESULT: exact_search]")
print(new_format)

print("\n" + "=" * 80)
print("✅ Теперь JSON-блоки будут с синтаксической подсветкой!")
print("=" * 80)

