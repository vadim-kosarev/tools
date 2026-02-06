"""Test script to verify FB2 parsing logic"""
from bs4 import BeautifulSoup
from parse_and_summarize import (
    get_section_title,
    extract_section_content,
    process_sections_recursive
)
import os
import json

# Пример структуры из файла
fb2_sample = '''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <body>
    <title>
      <p>Эрих Мария Ремарк</p>
      <p>Возлюби ближнего своего</p>
    </title>
    <section>
      <title>
        <p>ЧАСТЬ ПЕРВАЯ</p>
      </title>
      <section>
        <title>
          <p>1</p>
        </title>
        <p>Тяжелый кошмарный сон мигом пропал.</p>
        <p>Керн прислушался.</p>
      </section>
      <section>
        <title>
          <p>2</p>
        </title>
        <p>Это вторая секция с текстом.</p>
      </section>
    </section>
    <section>
      <title>
        <p>ЧАСТЬ ВТОРАЯ</p>
      </title>
      <section>
        <title>
          <p>1</p>
        </title>
        <p>Текст из второй части первой главы.</p>
      </section>
    </section>
  </body>
</FictionBook>'''

# Парсим пример
soup = BeautifulSoup(fb2_sample, 'xml')

print("=== TEST: get_section_title ===")
test_section = soup.find('section')
if test_section:
    title = get_section_title(test_section)
    print(f"Title extracted: '{title}'")
    assert title == "ЧАСТЬ ПЕРВАЯ", f"Expected 'ЧАСТЬ ПЕРВАЯ', got '{title}'"
    print("✓ PASS\n")

print("=== TEST: extract_section_content ===")
# Найди самую глубокую секцию (листовую)
leaf_section = soup.find_all('section')[2]  # Это будет секция с id=1
content = extract_section_content(leaf_section)
print(f"Content extracted: '{content}'")
assert "Тяжелый кошмарный сон" in content, "Expected content not found"
assert "ЧАСТЬ ПЕРВАЯ" not in content, "Should not contain parent title"
print("✓ PASS\n")

print("=== TEST: process_sections_recursive ===")
test_dir = 'test_output'
sections_list = []
section_counter = [0]

body = soup.find('body')
for child in body.children:
    if hasattr(child, 'name') and child.name == 'section':
        print(f"Processing top-level section...")
        process_sections_recursive(child, test_dir, sections_list, section_counter)

print(f"Total sections processed: {len(sections_list)}")
for sec in sections_list:
    print(f"  - Section {sec['idx']}: {sec['title']}")

# Проверяем файлы
print("\n=== Checking generated files ===")
if os.path.exists(f'{test_dir}/sections_metadata.json'):
    with open(f'{test_dir}/sections_metadata.json', 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    print(f"Metadata contains {len(metadata)} sections")
    for sec in metadata:
        print(f"  - {sec['title']}")
    print("✓ Files created successfully")

# Cleanup
import shutil
if os.path.exists(test_dir):
    shutil.rmtree(test_dir)
    print(f"\nTest directory cleaned up")

print("\n✓ ALL TESTS PASSED!")
