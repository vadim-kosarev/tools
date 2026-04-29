# Финализация: Корректный формат response_schema

## Задача

Убедиться что все промпты используют правильный формат для `response_schema`.

## Правильный формат

```markdown
### Формат ответа

```json
{{ response_schema }}
\```
```

**Важно:**
- ✅ Placeholder `{{ response_schema }}` должен быть внутри markdown блока ````json`
- ✅ Это обеспечивает правильную подсветку синтаксиса
- ✅ LLM видит что это JSON формат

## Проверенные файлы

### prompts_v2/

1. ✅ **plan/user.md**
   ```markdown
   ### Формат ответа
   
   ```json
   {{ response_schema }}
   \```
   ```

2. ✅ **action/user.md**
   ```markdown
   ### Формат ответа
   
   ```json
   {{ response_schema }}
   \```
   ```

3. ✅ **observation.md**
   ```markdown
   ### Формат ответа
   
   ```json
   {{ response_schema }}
   \```
   ```

4. ✅ **refine.md**
   ```markdown
   ### Формат ответа
   
   ```json
   {{ response_schema }}
   \```
   ```

5. ✅ **final.md**
   ```markdown
   ### Формат ответа
   
   ```json
   {{ response_schema }}
   \```
   ```

## Изменения

### action/user.md
**Было:**
```markdown
### Formат ответа

{{ response_schema }}
```

**Стало:**
```markdown
### Formат ответа

```json
{{ response_schema }}
\```
```

### final.md
**Было:**
```markdown
### Формат ответа

{{ response_schema }}
```

**Стало:**
```markdown
### Формат ответа

```json
{{ response_schema }}
\```
```

## Проверка

```bash
# Проверить все файлы с response_schema
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG\prompts_v2
Get-ChildItem -Recurse -Filter "*.md" | Select-String -Pattern "response_schema"

# Результат - все файлы используют placeholder внутри ```json блока
```

## Как это работает

### 1. Placeholder в промпте

```markdown
### Формат ответа

```json
{{ response_schema }}
\```
```

### 2. Генерация schema

```python
# schema_generator.py
def get_plan_schema() -> str:
    """JSON schema для plan ноды."""
    from rag_lg_agent import AgentPlan
    return generate_schema_for_prompt(AgentPlan)
```

Генерирует:
```
```json
{
  "status": "plan",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение (1-2 предложения)
  "plan": [
    "string"
  ]  // Список шагов плана поиска (3-5 пунктов)
}
\```
```

### 3. Рендеринг в промпте

```python
# rag_lg_agent.py
state['response_schema'] = get_plan_schema()
prompt = _prompt_loader.render_plan_user(state)
```

### 4. Итоговый промпт для LLM

```markdown
### Формат ответа

```json
{
  "status": "plan",
  "step": 0,  // Номер шага
  "thought": "string",  // Краткое рассуждение (1-2 предложения)
  "plan": [
    "string"
  ]  // Список шагов плана поиска (3-5 пунктов)
}
\```

**НЕ используй** поля `actions`, `observation`, `final_answer` на этом этапе.
```

## Преимущества правильного формата

### 1. Подсветка синтаксиса
- ✅ LLM видит что это JSON блок
- ✅ Улучшает парсинг кода
- ✅ Визуально выделяет формат

### 2. Консистентность
- ✅ Все промпты используют одинаковый формат
- ✅ Легче поддерживать
- ✅ Единый стиль

### 3. Совместимость
- ✅ Работает с markdown рендерингом
- ✅ Правильно отображается в логах
- ✅ Понятно для LLM

## Тестирование

```bash
# Запустить с prompts_v2
echo "PROMPTS_DIR=prompts_v2" >> .env
python rag_lg_agent.py --steps 1 "тест"

# Проверить лог
cat logs/001_llm_plan_request.log

# Должен быть правильный JSON блок с комментариями
```

## Статус

✅ **Все файлы проверены и исправлены**
✅ **Корректный формат везде**
✅ **Готово к использованию**

## Связанные документы

- `055_SCHEMA_FROM_PYDANTIC.md` - основная реализация schema generator
- `057_PROMPTS_V2_SCHEMA_PLACEHOLDERS.md` - замена хардкода на placeholders
- `056_SESSION_SUMMARY.md` - общее резюме сессии

## Итог

Все промпты в `prompts_v2/` используют правильный формат:

```markdown
### Формат ответа

```json
{{ response_schema }}
\```
```

**Single source of truth** - Pydantic модели генерируют JSON schema для промптов автоматически! 🎉

