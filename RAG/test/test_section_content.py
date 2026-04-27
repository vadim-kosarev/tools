"""
Тест получения полного содержимого раздела с информацией о ПО.
Тестируем исправленную get_neighbor_chunks() с параметром include_anchor.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from chroma_utils import get_store
from kb_tools import create_kb_tools

# Получаем store
store = get_store()

# 1. Находим чанк с ключевой фразой
print("="*80)
print("ШАГ 1: Находим якорный чанк с ключевой фразой")
print("="*80)
docs = store.exact_search("На АРМ эксплуатационного персонала СОИБ КЦОИ установлены следующие", limit=1)

if not docs:
    print("❌ Чанк не найден")
    sys.exit(1)

anchor = docs[0]
print(f"✅ Найден якорный чанк:")
print(f"  source: {anchor.metadata['source']}")
print(f"  section: {anchor.metadata['section']}")
print(f"  line_start: {anchor.metadata['line_start']}")
print(f"  content: {anchor.page_content[:200]}...")

# 2. Тестируем СТАРОЕ поведение (include_anchor=False)
print("\n" + "="*80)
print("ШАГ 2: Тест СТАРОГО поведения (include_anchor=False)")
print("="*80)

tools = create_kb_tools(store, "")
get_neighbor_chunks_tool = [t for t in tools if t.name == "get_neighbor_chunks"][0]

result_old = get_neighbor_chunks_tool.invoke({
    "source": anchor.metadata['source'],
    "line_start": anchor.metadata['line_start'],
    "before": 10,
    "after": 10,
    "include_anchor": False
})

print(f"Якорь включен: {result_old.anchor_chunk is not None}")
print(f"Чанков до: {len(result_old.chunks_before)}")
print(f"Чанков после: {len(result_old.chunks_after)}")

# 3. Тестируем НОВОЕ поведение (include_anchor=True, по умолчанию)
print("\n" + "="*80)
print("ШАГ 3: Тест НОВОГО поведения (include_anchor=True)")
print("="*80)

result_new = get_neighbor_chunks_tool.invoke({
    "source": anchor.metadata['source'],
    "line_start": anchor.metadata['line_start'],
    "before": 10,
    "after": 10,
    "include_anchor": True
})

print(f"✅ Якорь включен: {result_new.anchor_chunk is not None}")
print(f"Чанков до: {len(result_new.chunks_before)}")
print(f"Чанков после: {len(result_new.chunks_after)}")

if result_new.anchor_chunk:
    print(f"\n📍 Якорный чанк:")
    print(f"  line_start: {result_new.anchor_chunk.metadata.line_start}")
    print(f"  content: {result_new.anchor_chunk.content[:200]}...")

# 4. Собираем полный текст (якорь + соседи)
print("\n" + "="*80)
print("ШАГ 4: ПОЛНЫЙ ТЕКСТ РАЗДЕЛА (включая якорь)")
print("="*80)

# Собираем все чанки в правильном порядке
all_chunks_data = []

# Добавляем чанки до
for chunk in result_new.chunks_before:
    all_chunks_data.append((chunk.metadata.line_start, chunk.content))

# Добавляем якорь
if result_new.anchor_chunk:
    all_chunks_data.append((result_new.anchor_chunk.metadata.line_start, result_new.anchor_chunk.content))

# Добавляем чанки после
for chunk in result_new.chunks_after:
    all_chunks_data.append((chunk.metadata.line_start, chunk.content))

# Сортируем по line_start
all_chunks_data.sort(key=lambda x: x[0])

# Собираем текст
full_text = "\n".join(content for _, content in all_chunks_data)
print(full_text[:2000])  # Первые 2000 символов

# 5. Анализ списка ПО
print("\n" + "="*80)
print("ШАГ 5: АНАЛИЗ - Найденное ПО")
print("="*80)

if "установлены следующие" in full_text:
    print("✅ УСПЕХ! Найдена фраза 'установлены следующие'")

    # Ищем список ПО после этой фразы
    start_idx = full_text.find("установлены следующие")
    po_list = full_text[start_idx:start_idx+1000]

    print("\n📋 Фрагмент со списком ПО:")
    print("-"*80)
    print(po_list)
    print("-"*80)

    print("\n✅ Исправление работает! Якорный чанк теперь включается в результат.")
else:
    print("❌ FAIL: Фраза 'установлены следующие' не найдена")
    print(f"   Длина полного текста: {len(full_text)} символов")
    print("   Возможно якорь всё ещё не включён?")

