"""
Скрипт для удаления дублирующего логирования из kb_tools.py
"""
import re

def remove_duplicate_logging(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Паттерн 1: Удаляем блоки создания args_dict и вызов _db_request
    # Ищем:        # Формируем JSON...
    #         args_dict = {  ... }
    #         if source: ...
    #         rec = _db_request(...)

    pattern1 = r'\n        # Формируем JSON[^\n]*\n(?:        .*\n)*?        rec = _db_request\([^\)]+\)\n'
    content = re.sub(pattern1, '\n', content)

    # Паттерн 2: Удаляем однострочные вызовы _db_request
    pattern2 = r'\n        rec = _db_request\([^\)]+\)\n'
    content = re.sub(pattern2, '\n', content)

    # Паттерн 3: Удаляем многострочные вызовы _db_request
    pattern3 = r'\n        rec = _db_request\(\n(?:            [^\n]+\n)*?        \)\n'
    content = re.sub(pattern3, '\n', content)

    # Паттерн 4: Удаляем блоки if rec: rec.set_response(...)
    pattern4 = r'\n        # Логирование\n        if rec:\n            rec\.set_response\([^\)]+\)\n'
    content = re.sub(pattern4, '\n', content)

    # Паттерн 5: Удаляем простые if rec: блоки
    pattern5 = r'\n        if rec:\n            rec\.set_response\([^\n]+\n'
    content = re.sub(pattern5, '\n', content)

    # Паттерн 6: Удаляем многострочные if rec: блоки
    pattern6 = r'\n        if rec:\n            rec\.set_response\(f"[^"]*"\n(?:                [^\n]+\n)*?            \)\n'
    content = re.sub(pattern6, '\n', content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print("✅ Дублирующее логирование удалено")

# Запуск
remove_duplicate_logging(r'C:\dev\github.com\vadim-kosarev\tools.0\RAG\kb_tools.py')

