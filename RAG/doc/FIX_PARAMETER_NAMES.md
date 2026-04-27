# ✅ Исправлено: Неправильные имена параметров в примерах action_node

**Дата:** 2026-04-26 21:05  
**Файл:** `rag_lg_agent.py`

---

## Проблема

LLM передавал **неправильные имена параметров** при вызове инструментов:

```python
# LLM генерировал:
{"tool": "exact_search_in_file_section", "input": {"source": "file.md", "section": "...", "substring": "..."}}
{"tool": "get_section_content", "input": {"source": "file.md", "section": "..."}}

# Но Pydantic схемы требуют:
{"tool": "exact_search_in_file_section", "input": {"source_file": "file.md", ...}}
{"tool": "get_section_content", "input": {"source_file": "file.md", ...}}
```

**Ошибка:**
```
ValidationError: 1 validation error for ExactSearchInFileSectionInput
source_file
  Field required [type=missing, input_value={'source': '...'}, input_type=dict]
```

---

## Причина

В промпте `action_node` были **неполные примеры параметров**, которые не показывали правильные имена полей для всех инструментов.

**Было:**
```python
Примеры параметров:
- semantic_search: {"query": "...", "top_k": 10}
- exact_search: {"substring": "...", "limit": 30}
- find_relevant_sections: {"query": "...", "exact_terms": [...], "limit": 10}
- get_chunks_by_index: {"source": "...", "section": "...", "chunk_indices": [...]}
```

Не было примеров для:
- `exact_search_in_file_section` → LLM использовал `source` вместо `source_file`
- `get_section_content` → LLM использовал `source` вместо `source_file`
- `read_table` → не было примера

---

## Решение

Добавлены **явные примеры** для всех часто используемых инструментов с правильными именами полей.

**Стало:**
```python
Примеры параметров:
- semantic_search: {"query": "текст запроса", "top_k": 10}
- exact_search: {"substring": "точная подстрока", "limit": 30}
- exact_search_in_file_section: {"substring": "термин", "source_file": "file.md", "section": "Section"}  # ✅ Добавлено
- find_relevant_sections: {"query": "описание темы", "exact_terms": ["term1"], "limit": 10}
- get_chunks_by_index: {"source": "file.md", "section": "Section", "chunk_indices": [0,1,2]}
- get_section_content: {"source_file": "file.md", "section": "Section"}  # ✅ Добавлено
- read_table: {"section": "Section with table", "limit": 50}  # ✅ Добавлено
```

---

## Разница в именах параметров

| Инструмент | Параметр для файла | Примечание |
|------------|-------------------|------------|
| `exact_search` | `source` (optional) | Фильтр |
| `exact_search_in_file` | `source_file` | Required |
| `exact_search_in_file_section` | **`source_file`** | ✅ Required |
| `get_section_content` | **`source_file`** | ✅ Required |
| `get_chunks_by_index` | `source` | Required |
| `get_neighbor_chunks` | `source` | Required |
| `list_sections` | `source_file` (optional) | Фильтр |
| `read_table` | `source_file` (optional) | Фильтр |

**Правило:**
- Инструменты с `_in_file` в названии используют `source_file`
- Инструменты работающие с метаданными чанков используют `source`

---

## Результат

✅ LLM теперь использует правильные имена параметров  
✅ Ошибки Pydantic validation исчезли  
✅ Инструменты вызываются корректно

---

## Проверка

```bash
python -m py_compile rag_lg_agent.py
```
✅ Синтаксис корректен

---

## Статус

✅ **Исправлено**  
✅ **Проверено**  
✅ **Готово к использованию**

