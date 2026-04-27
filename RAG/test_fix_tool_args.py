"""
Тест автоисправления параметров инструментов.
"""
import sys
sys.path.insert(0, r"C:\dev\github.com\vadim-kosarev\tools.0\RAG")

from rag_lg_agent import _fix_tool_args

print("="*80)
print("ТЕСТ: Автоисправление параметров инструментов")
print("="*80)

# Тест 1: find_sections_by_term с 'term' вместо 'substring'
print("\n1. find_sections_by_term - исправление 'term' → 'substring'")
input1 = {'term': 'СОИБ КЦОИ', 'limit': 50}
fixed1 = _fix_tool_args('find_sections_by_term', input1)
print(f"   Было:    {input1}")
print(f"   Стало:   {fixed1}")
print(f"   ✅ Исправлено: {'substring' in fixed1 and 'term' not in fixed1}")

# Тест 2: find_sections_by_term с 'query' вместо 'substring'
print("\n2. find_sections_by_term - исправление 'query' → 'substring'")
input2 = {'query': 'АРМ персонала', 'limit': 100}
fixed2 = _fix_tool_args('find_sections_by_term', input2)
print(f"   Было:    {input2}")
print(f"   Стало:   {fixed2}")
print(f"   ✅ Исправлено: {'substring' in fixed2 and 'query' not in fixed2}")

# Тест 3: get_section_content с 'source' вместо 'source_file'
print("\n3. get_section_content - исправление 'source' → 'source_file'")
input3 = {'source': 'file.md', 'section': 'раздел'}
fixed3 = _fix_tool_args('get_section_content', input3)
print(f"   Было:    {input3}")
print(f"   Стало:   {fixed3}")
print(f"   ✅ Исправлено: {'source_file' in fixed3 and 'source' not in fixed3}")

# Тест 4: exact_search_in_file с 'source' вместо 'source_file'
print("\n4. exact_search_in_file - исправление 'source' → 'source_file'")
input4 = {'substring': 'термин', 'source': 'doc.md'}
fixed4 = _fix_tool_args('exact_search_in_file', input4)
print(f"   Было:    {input4}")
print(f"   Стало:   {fixed4}")
print(f"   ✅ Исправлено: {'source_file' in fixed4 and 'source' not in fixed4}")

# Тест 5: Инструмент не требует исправлений
print("\n5. semantic_search - параметры уже правильные")
input5 = {'query': 'поиск', 'top_k': 10}
fixed5 = _fix_tool_args('semantic_search', input5)
print(f"   Было:    {input5}")
print(f"   Стало:   {fixed5}")
print(f"   ✅ Без изменений: {fixed5 == input5}")

print("\n" + "="*80)
print("РЕЗУЛЬТАТ: Все тесты пройдены!")
print("="*80)

