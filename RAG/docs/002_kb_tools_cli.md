# KB Tools CLI - Краткая справка

**Последнее обновление:** 2026-04-29

---

## Команды

```bash
# Общий help
python kb_tools.py
python kb_tools.py help
python kb_tools.py --help

# Список инструментов (кратко)
python kb_tools.py list

# Детальная справка по инструменту
python kb_tools.py help <tool_name>

# Запуск инструмента
python kb_tools.py run <tool_name> param=value ...
```

---

## Примеры

### Найти расшифровку аббревиатуры

```bash
python kb_tools.py run find_abbreviation_expansion abbreviation=КЦОИ
python kb_tools.py run find_abbreviation_expansion abbreviation=AK47
```

### Точный поиск

```bash
python kb_tools.py run exact_search substring=КЦОИ
python kb_tools.py run exact_search substring=КЦОИ limit=5
```

### Семантический поиск

```bash
python kb_tools.py run semantic_search query="что такое RAG" top_k=10
```

### Список инструментов

```bash
python kb_tools.py run list_sources
```

**Вывод таблицей:**
```
+------------------------------+-----------------------------------------------+
| Инструмент                   | Параметры                                     |
+------------------------------+-----------------------------------------------+
| semantic_search              | query, [top_k], [chunk_type], [source], ...  |
| exact_search                 | substring, [limit], [chunk_type], ...         |
| find_abbreviation_expansion  | abbreviation, [max_results]                   |
| ...                          | ...                                           |
+------------------------------+-----------------------------------------------+

Всего инструментов: 16
```

### Содержимое секции

```bash
python kb_tools.py run get_section_content source_file="file.md" section="Section Name"
```

---

## Формат параметров

- **param=value** - основной формат
- **Числа** автоматически конвертируются: `limit=10`
- **JSON** для массивов: `terms='["term1","term2"]'`
- **Кириллица** работает без экранирования

---

## Вывод

- **stdout** - только JSON результат (для pipe)
- **stderr** - логи и ошибки

```powershell
# Только JSON, без логов
python kb_tools.py run exact_search substring=КЦОИ 2>$null

# Парсинг JSON в PowerShell
python kb_tools.py run exact_search substring=КЦОИ 2>$null | ConvertFrom-Json
```

---

## Все инструменты (15)

1. `semantic_search` - семантический поиск
2. `exact_search` - точный поиск
3. `exact_search_in_file` - поиск в файле
4. `exact_search_in_file_section` - поиск в секции файла
5. `multi_term_exact_search` - поиск по нескольким терминам
6. `find_sections_by_term` - найти секции с термином
7. `find_relevant_sections` - релевантные секции
8. `regex_search` - regex поиск
9. `find_abbreviation_expansion` - расшифровка аббревиатур
10. `read_table` - чтение таблицы
11. `get_section_content` - содержимое секции
12. `list_sections` - список секций
13. `get_neighbor_chunks` - соседние чанки
14. `get_chunks_by_index` - чанки по индексу
15. `list_sources` - список файлов

Используйте `python kb_tools.py help <tool>` для детальной информации!

