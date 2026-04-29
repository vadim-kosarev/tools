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

🚫 **КРИТИЧЕСКОЕ ПРАВИЛО: НЕ ПОВТОРЯЙ ВЫЗОВЫ!**
**ОБЯЗАТЕЛЬНО проверь список уже выполненных инструментов (см. user message):**
- Если инструмент с ТАКИМИ ЖЕ параметрами уже был вызван - НЕ вызывай его снова!
- Результаты уже есть в истории сообщений
- Ищи информацию ДРУГИМИ способами:
  * Используй другой инструмент (semantic_search вместо exact_search)
  * Используй другие параметры (другие термины, другой раздел, другой файл)
  * Если нашли упоминание раздела - читай его полностью через get_section_content
  
**Примеры правильного поведения:**
- ❌ НЕПРАВИЛЬНО: exact_search("ПО") → уже было → снова exact_search("ПО")
- ✅ ПРАВИЛЬНО: exact_search("ПО") → уже было → get_section_content(file="...", section="...")
- ✅ ПРАВИЛЬНО: semantic_search("АРМ оператора") → уже было → exact_search_in_file_section(file="...", section="...")

{% if iteration > 1 %}
🎯 **Используй TARGETED TOOLS для уточнения:**

**ПРИОРИТЕТ: Если в observation были указаны next_targets (конкретные разделы):**
- Используй get_section_content для получения ПОЛНОГО содержимого этих разделов
- ИЛИ exact_search_in_file_section для поиска конкретных терминов в этих разделах
- Параметры source_file и section берутся из next_targets

**Примеры уточняющих инструментов:**
- get_section_content: получить полный текст раздела (если знаем source + section)
- exact_search_in_file_section: искать термины в конкретном файле/разделе
- get_chunks_by_index: получить конкретные чанки по индексам
- find_relevant_sections: найти разделы по описанию темы
{% endif %}

разных⚠️ ПРИ ИСПОЛЬЗОВАНИИ multi_term_exact_search:
**ОБЯЗАТЕЛЬНО определи РАСШИРЕННЫЙ список терминов (15-20):**
1. Основные термины из вопроса
2. Синонимы каждого термина
3. Связанные слова (смежные понятия)
4. Синонимы связанных слов
5. Словосочетания из запроса и их комбинации

→ Итого: 15-20 терминов в массиве

⚠️ ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА для этапа action:
```json
{
  "status": "action",
  "step": {{ step + 1 }},
  "thought": "краткое рассуждение о выборе инструментов",
  "actions": [
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
- multi_term_exact_search: {"terms": ["термин1", "синоним1", "термин2", "синоним2", "связанное_слово", "словосочетание"], "limit": 50}
- exact_search_in_file_section: {"substring": "термин", "source_file": "filename.md", "section": "Section Name"}
- find_relevant_sections: {"query": "описание темы", "exact_terms": ["term1"], "limit": 10}
- get_chunks_by_index: {"source": "file.md", "section": "Section", "chunk_indices": [0,1,2]}
- get_section_content: {"source_file": "filename.md", "section": "Section Name"}
- read_table: {"section": "Section with table", "limit": 50}

НЕ ИСПОЛЬЗУЙ поля "observation" или "final_answer" на этом этапе!

