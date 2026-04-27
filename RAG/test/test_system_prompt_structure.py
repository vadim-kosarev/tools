"""Проверка структуры system prompt"""
from rag_lg_agent import _SYSTEM_PROMPT

print("="*80)
print("ПРОВЕРКА СТРУКТУРЫ SYSTEM PROMPT")
print("="*80)

# Проверяем наличие секций
sections = {
    "[AVAILABLE_TOOLS]": "[AVAILABLE_TOOLS]" in _SYSTEM_PROMPT,
    "## Доступные инструменты": "## Доступные инструменты" in _SYSTEM_PROMPT,
    "{available_tools}": "{available_tools}" in _SYSTEM_PROMPT,
}

print("\nНаличие секций:")
for section, present in sections.items():
    status = "✅ Есть" if present else "❌ Нет"
    print(f"  {status}: {section}")

# Показываем фрагмент с AVAILABLE_TOOLS
if sections["[AVAILABLE_TOOLS]"]:
    idx = _SYSTEM_PROMPT.find("[AVAILABLE_TOOLS]")
    print(f"\n{'='*80}")
    print("ФРАГМЕНТ С [AVAILABLE_TOOLS]:")
    print(f"{'='*80}")
    # Показываем 200 символов до и 800 после
    start = max(0, idx - 200)
    end = min(len(_SYSTEM_PROMPT), idx + 800)
    fragment = _SYSTEM_PROMPT[start:end]
    print(fragment)
    print("...")
else:
    print("\n⚠️  Секция [AVAILABLE_TOOLS] НЕ НАЙДЕНА!")
    print("\nВозможные причины:")
    print("  1. Placeholder {available_tools} не заменен")
    print("  2. system_prompt.md не содержит placeholder")

    # Проверим наличие placeholder
    if sections["{available_tools}"]:
        print("\n❌ ПРОБЛЕМА: Placeholder {available_tools} НЕ ЗАМЕНЕН!")
        idx = _SYSTEM_PROMPT.find("{available_tools}")
        print(f"\nКонтекст вокруг placeholder:")
        start = max(0, idx - 100)
        end = min(len(_SYSTEM_PROMPT), idx + 100)
        print(_SYSTEM_PROMPT[start:end])

print(f"\n{'='*80}")
print(f"Общая длина system prompt: {len(_SYSTEM_PROMPT)} символов")
print(f"{'='*80}")

