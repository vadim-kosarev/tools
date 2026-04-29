⚠️ ОШИБКА ПАРСИНГА JSON!

ОБЯЗАТЕЛЬНАЯ структура для этапа refine:
```json
{
  "status": "refine",  ← ОБЯЗАТЕЛЬНО "refine"!
  "step": {{ step + 1 }},
  "thought": "краткое рассуждение",
  "needs_refinement": true,  ← ОБЯЗАТЕЛЬНО boolean!
  "refinement_plan": ["шаг 1", "шаг 2"]  ← массив строк (если needs_refinement=true)
}
```

Попробуй еще раз. Верни ТОЛЬКО JSON, строго в формате выше.

