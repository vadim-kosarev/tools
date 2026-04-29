"""
Скрипт для аккуратного удаления дублирующего логирования из kb_tools.py
"""
import re

file_path = r'C:\dev\github.com\vadim-kosarev\tools.0\RAG\kb_tools.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Шаг 1: Удаляем определения функций _format_tool_args и _db_request
# Найдем блок от def _format_tool_args до # ── Pydantic schemas
pattern1 = r'    def _format_tool_args\(.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n        return json\.dumps\(kwargs, ensure_ascii=False, indent=2\)\n    \n    def _db_request\(step: str, request: str\):\n        if llm_logger is not None:\n            rec = llm_logger\.start_record\(step\)\n            rec\.set_request\(request\)\n            return rec\n        return None\n\n'
content = re.sub(pattern1,'', content, flags=re.DOTALL)

# Шаг 2: Удаляем использование этих функций
# Удалим все вызовы rec = _db_request и if rec: блоки
lines = content.split('\n')
result_lines = []
i = 0
skip_until_empty = False

while i < len(lines):
    line = lines[i]

    # Пропускаем блоки args_dict
    if '# Формируем JSON' in line or '# Формируем JSON' in line:
        # Пропускаем до rec = _db_request включительно
        i += 1
        while i < len(lines) and 'rec = _db_request' not in lines[i]:
            i += 1
        i += 1  # Пропускаем саму строку rec = _db_request
        continue

    # Пропускаем rec = _db_request
    if 'rec = _db_request' in line:
        # Пропускаем многострочный вызов
        if line.strip().endswith('('):
            while i < len(lines) and ')' not in lines[i]:
                i += 1
            i += 1  # Пропускаем закрывающую скобку
        else:
            i += 1
        continue

    # Пропускаем блоки # Логирование + if rec:
    if '# Логирование' in line:
        i += 1  # Пропускаем саму строку # Логирование
        if i < len(lines) and 'if rec:' in lines[i]:
            i += 1  # Пропускаем if rec:
            if i < len(lines) and 'rec.set_response' in lines[i]:
                i += 1  # Пропускаем rec.set_response
        continue

    # Пропускаем одиночные if rec: блоки
    if 'if rec:' in line:
        i += 1  # Пропускаем if rec:
        if i < len(lines) and 'rec.set_response' in lines[i]:
            i += 1  # Пропускаем rec.set_response
        continue

    result_lines.append(line)
    i += 1

content = '\n'.join(result_lines)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Дублирующее логирование удалено аккуратно")

