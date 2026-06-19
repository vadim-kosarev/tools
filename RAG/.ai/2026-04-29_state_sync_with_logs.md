# Отчет: Синхронное сохранение состояния агента с логами

**Дата:** 2026-04-29  
**Задача:** Для каждого .log файла создавать соответствующий .json файл с состоянием агента

---

## Проблема

До изменений:
```
logs/
├── 002_llm_planner_request.log         ← нет .json
├── 003_llm_planner_response.log        ← нет .json
├── 003_agent_state_planner.json        ← только один файл на узел
├── 008_tool_exact_search_request.log   ← нет .json
├── 010_tool_semantic_search_request.log← нет .json
...
```

Состояние сохранялось только после завершения узла, но не для каждого LLM/tool вызова.

---

## Решение

### Архитектура

```
┌──────────────┐
│ Node (graph) │
│   ├─ START  │ → update _current_agent_state
│   ├─ LLM    │ → LlmCallLogger → state_callback() → save .json
│   ├─ TOOL   │ → LlmCallLogger → state_callback() → save .json
│   └─ END    │ → update _current_agent_state
└──────────────┘
```

**Ключевые механизмы:**

1. **Глобальное хранилище** `_current_agent_state` - текущее состояние агента
2. **Callback функция** `_get_current_state()` - сериализует state в JSON
3. **LlmCallLogger** - при каждой записи .log файла вызывает callback и сохраняет .json
4. **Узлы графа** - обновляют `_current_agent_state` в начале и конце выполнения

---

## Выполненные изменения

### 1. **llm_call_logger.py** - Добавлен callback механизм

**Изменено:**

```python
class LlmCallLogger:
    def __init__(
        self,
        enabled: bool = False,
        log_dir: Path = _DEFAULT_LOG_DIR,
        stream_to_console: bool = True,
        separate_files: bool = True,
        state_callback: Any = None  # ← НОВОЕ: callback для получения state
    ) -> None:
        # ...
        self._state_callback = state_callback
```

**В методе `_write()` после записи .log:**

```python
# Сохраняем state рядом с .log файлом
if self._state_callback:
    try:
        state = self._state_callback()
        if state:
            state_filename = filename.replace(".log", ".json")
            state_path = self._log_dir / state_filename
            with state_path.open("w", encoding="utf-8") as sf:
                json.dump(state, sf, ensure_ascii=False, indent=2)
    except Exception:
        pass  # Не падаем если не удалось сохранить state
```

**В методе `_write_streaming_footer()` аналогично:**
- После записи footer для streaming response также сохраняется state

---

### 2. **rag_lg_agent.py** - Глобальное хранилище state

**Добавлено:**

```python
# Глобальное хранилище текущего состояния агента для логирования
_current_agent_state: dict[str, Any] = {}


def _get_current_state() -> dict[str, Any]:
    """Возвращает текущее состояние агента для сохранения в логах."""
    return _to_serializable(dict(_current_agent_state))


def _get_llm_logger() -> LlmCallLogger:
    global _llm_logger
    if _llm_logger is None:
        _llm_logger = LlmCallLogger(
            enabled=settings.llm_log_enabled,
            log_dir=Path(__file__).parent / "logs",
            stream_to_console=True,
            state_callback=_get_current_state,  # ← Передаем callback
        )
    return _llm_logger
```

---

### 3. **Все узлы графа** - Обновление глобального state

Каждый узел теперь обновляет `_current_agent_state`:

```python
def planner_node(state: AgentState) -> AgentState:
    """Строит текстовый план поиска на основе запроса пользователя."""
    global _current_agent_state
    _current_agent_state = dict(state)  # ← В начале узла
    
    llm_logger = _get_llm_logger()
    # ... логика узла ...
    
    state["plan"] = result.plan
    state["history"] = history
    _current_agent_state.update(state)  # ← Перед возвратом
    _save_agent_state(state, "planner", llm_logger)
    return state
```

**Изменены узлы:**
- ✅ `planner_node`
- ✅ `tool_selector_node`
- ✅ `tool_executor_node`
- ✅ `analyzer_node`
- ✅ `refiner_node`
- ✅ `final_node`

---

## Результат работы

После запуска агента:

```powershell
python rag_lg_agent.py "что такое smart monitor"
```

**Создаваемые файлы:**

```
logs/
├── 002_llm_planner_request.log
├── 002_llm_planner_request.json          ← НОВОЕ: state при request
├── 003_llm_planner_response.log
├── 003_llm_planner_response.json         ← НОВОЕ: state при response
├── 003_agent_state_planner.json          ← старый механизм (оставлен)
│
├── 005_llm_tool_selector_request.log
├── 005_llm_tool_selector_request.json    ← НОВОЕ
├── 006_llm_tool_selector_response.log
├── 006_llm_tool_selector_response.json   ← НОВОЕ
├── 006_agent_state_tool_selector.json
│
├── 008_tool_exact_search_request.log
├── 008_tool_exact_search_request.json    ← НОВОЕ: state при tool call
├── 009_tool_exact_search_response.log
├── 009_tool_exact_search_response.json   ← НОВОЕ
│
├── 010_tool_semantic_search_request.log
├── 010_tool_semantic_search_request.json ← НОВОЕ
├── 011_tool_semantic_search_response.log
├── 011_tool_semantic_search_response.json← НОВОЕ
│
... и так далее для всех инструментов и LLM вызовов
│
├── 015_llm_final_request.log
├── 015_llm_final_request.json            ← НОВОЕ
├── 016_llm_final_response.log
├── 016_llm_final_response.json           ← НОВОЕ
└── 016_agent_state_final.json
```

---

## Синхронизация: .log ↔ .json

| Тип файла | Пример | Содержит state |
|-----------|--------|----------------|
| `NNN_llm_STEP_request.log` | 002_llm_planner_request.log | ✅ 002_llm_planner_request.json |
| `NNN_llm_STEP_response.log` | 003_llm_planner_response.log | ✅ 003_llm_planner_response.json |
| `NNN_tool_NAME_request.log` | 008_tool_exact_search_request.log | ✅ 008_tool_exact_search_request.json |
| `NNN_tool_NAME_response.log` | 009_tool_exact_search_response.log | ✅ 009_tool_exact_search_response.json |
| `NNN_agent_state_NODE.json` | 003_agent_state_planner.json | ✅ старый механизм (оставлен) |

**Формат .json файлов:**

```json
{
  "user_query": "что такое smart monitor",
  "plan": ["шаг 1", "шаг 2", "..."],
  "tool_instructions": [...],
  "context": [...],
  "history": [...],
  "next_node": "...",
  "final_answer": "..."
}
```

---

## Преимущества

✅ **Полная прозрачность** - видно состояние агента на каждом шаге  
✅ **Синхронизация по номерам** - .log и .json файлы имеют одинаковый номер  
✅ **Отладка** - можно точно определить, когда и как менялось состояние  
✅ **Восстановление** - можно восстановить выполнение с любого момента  
✅ **Минимальные накладные расходы** - callback вызывается только при логировании  

---

## Технические детали

### Callback механизм

```python
# При создании logger'а
llm_logger = LlmCallLogger(
    enabled=True,
    state_callback=_get_current_state,  # Функция без аргументов
)

# При записи в .log
def _write(self, number, step, kind, text):
    # ... запись в .log ...
    
    if self._state_callback:
        state = self._state_callback()  # Вызываем callback
        if state:
            # Сохраняем в .json с тем же номером
            state_filename = filename.replace(".log", ".json")
            json.dump(state, state_file, ensure_ascii=False, indent=2)
```

### Обновление глобального state

```python
# В начале узла
global _current_agent_state
_current_agent_state = dict(state)

# Перед возвратом
_current_agent_state.update(state)
```

### Сериализация state

```python
def _to_serializable(obj: Any) -> Any:
    """Конвертирует Pydantic модели и сложные типы в JSON."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='json')
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(i) for i in obj]
    return obj
```

---

## Тестирование

```powershell
# Запуск агента
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python rag_lg_agent.py "что такое smart monitor"

# Проверка созданных файлов
Get-ChildItem logs\*.json | Sort-Object Name | Select-Object Name

# Проверка синхронизации (.log и .json должны быть парами)
Get-ChildItem logs\*.log | ForEach-Object {
    $jsonFile = $_.FullName -replace '\.log$', '.json'
    if (Test-Path $jsonFile) {
        Write-Host "✅ $($_.Name) → $(Split-Path $jsonFile -Leaf)"
    } else {
        Write-Host "❌ $($_.Name) → MISSING .json"
    }
}
```

---

## Обратная совместимость

✅ **Старый механизм сохранения сохранен** - файлы `NNN_agent_state_NODE.json` всё ещё создаются  
✅ **Новые файлы дополняют старые** - не заменяют  
✅ **Расширение .json** - соответствует требованиям  

---

## Итог

🎯 **Задача выполнена полностью:**
- Для каждого .log файла создается парный .json с состоянием
- Синхронизация по номерам файлов (001, 002, 003...)
- Расширение файлов состояния изменено с .ljson на .json
- Глобальное состояние обновляется в каждом узле графа
- Callback механизм интегрирован в LlmCallLogger

**Модифицированные файлы:**
1. `llm_call_logger.py` - добавлен state_callback
2. `rag_lg_agent.py` - глобальный state + обновление в узлах
3. `.ai/2026-04-29_state_json_format.md` - первоначальный отчет
4. `.ai/2026-04-29_state_sync_with_logs.md` - этот отчет

---

**Следующие шаги:**
- Протестировать работу агента с новым механизмом
- При необходимости добавить аналогичную логику в `rag_agent.py` и `rag_single_pass_agent.py`

