# ♻️ Автоматическая Очистка Логов

## Что Делается

При каждом запуске `rag_lg_agent.py` **автоматически очищается** директория `logs/`:

### ✅ Удаляются
- `001_*.log`, `002_*.log`, ... - отдельные файлы запросов/ответов
- `_rag_llm.log` - единый файл логов (старый формат)

### ✅ Сохраняются
- `README.md` - документация
- `test/` - тестовые данные
- `rag_lg_agent_v2.log` - общий лог приложения
- Любые другие файлы без нумерации

## Зачем Это Нужно

1. **Чистая нумерация** - каждый запуск начинается с 001
2. **Нет путаницы** - логи только от текущего запуска
3. **Проще читать** - не смешиваются логи разных запусков
4. **Меньше места** - старые логи не накапливаются

## Пример

### Было (без очистки)
```
logs/
  001_llm_plan_request.log        # от предыдущего запуска
  002_llm_plan_response.log       # от предыдущего запуска
  003_tool_exact_search_request.log   # от предыдущего запуска
  ...
  050_llm_final_response.log      # от предыдущего запуска
  051_llm_plan_request.log        # от нового запуска ← непонятно!
  052_llm_plan_response.log       # от нового запуска
  ...
```

### Стало (с очисткой)
```
# Перед запуском: logs/ пуста

# После запуска:
logs/
  001_llm_plan_request.log        # ← новый запуск, начинается с 001
  002_llm_plan_response.log
  003_tool_exact_search_request.log
  ...
  010_llm_final_response.log
```

## Как Отключить (Если Нужно)

Если хотите сохранять логи между запусками, закомментируйте в `rag_lg_agent.py`:

```python
def main() -> None:
    args = parse_args()
    
    # Очищаем логи перед запуском
    # clear_logs_directory()  # ← закомментируйте эту строку
    
    logger.info(...)
```

## Сохранение Логов Перед Очисткой

Если нужно сохранить логи конкретного запуска:

```bash
# PowerShell: копируем логи в отдельную папку
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
New-Item -ItemType Directory -Path "logs\archive\$timestamp"
Copy-Item logs\[0-9][0-9][0-9]_*.log "logs\archive\$timestamp\"

# Bash: то же самое
timestamp=$(date +%Y-%m-%d_%H-%M-%S)
mkdir -p logs/archive/$timestamp
cp logs/[0-9][0-9][0-9]_*.log logs/archive/$timestamp/
```

Потом запускайте агента - старые логи будут в archive, новые - в logs.

## Тестирование

```bash
# Проверить работу очистки
python test_clear_logs.py
```

Тест создает файлы, вызывает `clear_logs_directory()`, и проверяет результат.

---

**Обновлено:** 2026-04-27  
**Версия:** 2.1  
**Файл:** `rag_lg_agent.py` (функция `clear_logs_directory()`)

