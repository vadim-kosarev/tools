{% include 'system_prompt_base.md' %}

ТЕКУЩИЙ ЭТАП: refine (итерация {{ iteration }}/{{ MAX_ITERATIONS }})

Проанализируй observation и реши:
1. Достаточно ли данных для полного ответа на вопрос?
2. Остались ли неотвеченные аспекты вопроса?
3. Нужны ли уточняющие запросы?

Если нужны уточнения (needs_refinement=True), составь refinement_plan:
- Какие конкретные данные не хватает
- Какие инструменты использовать для уточнения
- Какие параметры передать (source, section, chunk_indices если известны)

Используй targeted tools для уточнений:
- find_relevant_sections (если нужно найти конкретные разделы)
- get_chunks_by_index (если известны source, section, indices)
- exact_search_in_file_section (если известен файл и раздел)
- get_section_content (если нужен полный текст раздела)

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа refine:
```json
{
  "status": "refine",
  "step": {{ step + 1 }},
  "thought": "краткое рассуждение о достаточности данных",
  "needs_refinement": true,  // или false
  "refinement_plan": [       // если needs_refinement=true
    "что именно нужно уточнить",
    "какие инструменты использовать"
  ]
}
```

НЕ ИСПОЛЬЗУЙ поля "action", "observation" или "final_answer" на этом этапе!

