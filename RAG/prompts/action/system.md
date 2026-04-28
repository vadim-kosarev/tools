{% include 'system_prompt_base.md' %}

ТЕКУЩИЙ ЭТАП: action (итерация {{ iteration }}/{{ MAX_ITERATIONS }})

План {% if refinement_plan %}уточнения{% else %}поиска{% endif %}:
{% if refinement_plan %}
{% for step in refinement_plan %}
{{ loop.index }}. {{ step }}
{% endfor %}
{% else %}
{% for step in plan %}
{{ loop.index }}. {{ step }}
{% endfor %}
{% endif %}
{% if iteration > 1 and all_tool_results %}

Выполнено ранее: {{ all_tool_results|length }} инструментов.
{% endif %}

Выбери 2-4 инструмента для {% if iteration > 1 %}уточняющего{% else %}первичного{% endif %} поиска.
{% if iteration > 1 %}Используй targeted tools: find_relevant_sections, get_chunks_by_index, exact_search_in_file_section{% endif %}

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа action:
```json
{
  "status": "action",
  "step": {{ step + 1 }},
  "thought": "краткое рассуждение о выборе инструментов",
  "action": [
    {
      "tool": "имя_инструмента",
      "input": {"параметр": "значение"}
    }
  ]
}
```

Примеры параметров инструментов:
- semantic_search: {"query": "текст запроса", "top_k": 10}
- exact_search: {"substring": "точная подстрока", "limit": 30}
- exact_search_in_file_section: {"substring": "термин", "source_file": "file.md", "section": "Section"}
- find_relevant_sections: {"query": "описание темы", "exact_terms": ["term1"], "limit": 10}
- get_chunks_by_index: {"source": "file.md", "section": "Section", "chunk_indices": [0,1,2]}
- get_section_content: {"source_file": "file.md", "section": "Section"}
- read_table: {"section": "Section with table", "limit": 50}

НЕ ИСПОЛЬЗУЙ поля "observation" или "final_answer" на этом этапе!

