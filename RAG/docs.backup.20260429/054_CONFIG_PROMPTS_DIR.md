# Конфигурация: Путь к промптам через .env

## Проблема

Путь к папке с промптами был хардкоден в коде:

```python
# prompt_loader.py
if prompts_dir is None:
    prompts_dir = Path(__file__).parent / 'prompts_v2'  # <- хардкод
```

**Проблемы:**
- Чтобы переключиться между `prompts` и `prompts_v2` нужно менять код
- Невозможно настроить путь извне
- Сложно тестировать разные версии промптов

## Решение

Путь к промптам вынесен в настройки через `.env`:

```bash
# .env
PROMPTS_DIR=prompts      # или prompts_v2
```

### Изменения

#### 1. Settings (rag_chat.py)

Добавлено поле `prompts_dir`:

```python
class Settings(BaseSettings):
    # ...
    knowledge_dir: str = r"Z:\ES-Leasing\СОИБ КЦОИ"
    prompts_dir: str = "prompts"  # <- Новое поле
    # ClickHouse connection
    # ...
```

**По умолчанию:** `"prompts"`

#### 2. PromptLoader (prompt_loader.py)

Обновлен конструктор для использования settings:

```python
def __init__(self, prompts_dir: Optional[Path] = None):
    if prompts_dir is None:
        # Импортируем settings для получения пути к промптам
        try:
            from rag_chat import settings
            prompts_dir_name = settings.prompts_dir
        except ImportError:
            # Fallback если rag_chat недоступен
            prompts_dir_name = 'prompts'
        
        # Строим полный путь относительно этого файла
        prompts_dir = Path(__file__).parent / prompts_dir_name
    
    self.prompts_dir = Path(prompts_dir)
    # ...
```

**Логика:**
1. Если `prompts_dir` передан явно → используем его
2. Иначе берем `settings.prompts_dir` из .env
3. Fallback на `'prompts'` если settings недоступен

#### 3. .env.example

Добавлена документация:

```bash
# === Prompts ===
# Папка с промптами относительно RAG/
# PROMPTS_DIR=prompts      # текущая версия (по умолчанию)
# PROMPTS_DIR=prompts_v2    # новая упрощенная версия
PROMPTS_DIR=prompts
```

#### 4. Docstring (rag_chat.py)

Обновлена документация:

```python
"""
Переменные окружения (.env):
    ...
    KNOWLEDGE_DIR          — папка с .md файлами источников знаний
    PROMPTS_DIR            — папка с промптами относительно RAG/ (по умолчанию prompts, можно prompts_v2)
    CLICKHOUSE_HOST        — хост ClickHouse (по умолчанию localhost)
    ...
"""
```

## Использование

### Вариант 1: Через .env (рекомендуется)

```bash
# .env
PROMPTS_DIR=prompts_v2
```

```python
# Код
from prompt_loader import get_loader

loader = get_loader()  # Автоматически использует prompts_v2
loader.render_plan_system(state)
```

### Вариант 2: Явно в коде

```python
from pathlib import Path
from prompt_loader import PromptLoader

# Явно указываем путь
custom_path = Path(__file__).parent / 'my_prompts'
loader = PromptLoader(prompts_dir=custom_path)
```

### Вариант 3: Fallback (если нет .env)

```python
# Если нет .env или rag_chat недоступен
# Используется prompts/ по умолчанию
loader = get_loader()
```

## Переключение между версиями

### Текущая версия (prompts/)

```bash
# .env
PROMPTS_DIR=prompts
```

или просто не указывать (по умолчанию)

### Новая версия (prompts_v2/)

```bash
# .env
PROMPTS_DIR=prompts_v2
```

### A/B тестирование

```bash
# Терминал 1
PROMPTS_DIR=prompts python rag_lg_agent.py "тест"

# Терминал 2
PROMPTS_DIR=prompts_v2 python rag_lg_agent.py "тест"

# Сравниваем результаты
```

## Преимущества

### 1. Гибкость
- ✅ Легко переключаться между версиями промптов
- ✅ Можно тестировать новые промпты без изменения кода
- ✅ Разные окружения могут использовать разные промпты

### 2. Чистота кода
- ✅ Нет хардкода путей
- ✅ Конфигурация отделена от логики
- ✅ Единая точка настройки (.env)

### 3. Тестирование
- ✅ Можно создать тестовые промпты в test_prompts/
- ✅ CI/CD может использовать свои промпты
- ✅ Легко воспроизвести поведение на разных машинах

### 4. Совместимость
- ✅ Обратная совместимость (по умолчанию prompts/)
- ✅ Можно передать путь явно
- ✅ Fallback если settings недоступен

## Структура проекта

```
RAG/
├── .env                    # Конфигурация (PROMPTS_DIR=prompts)
├── .env.example            # Пример конфигурации
├── prompt_loader.py        # Использует settings.prompts_dir
├── rag_chat.py             # Определяет Settings с prompts_dir
├── rag_lg_agent.py         # Использует get_loader()
├── prompts/                # Текущая версия промптов
│   ├── plan/
│   ├── action/
│   ├── observation/
│   ├── refine/
│   └── final/
└── prompts_v2/             # Новая версия (опционально)
    ├── system.md
    ├── plan.md
    ├── action.md
    ├── observation.md
    ├── refine.md
    └── final.md
```

## Миграция существующего кода

### Если использовали хардкод

**Было:**
```python
loader = PromptLoader(Path(__file__).parent / 'prompts_v2')
```

**Стало:**
```python
# В .env
PROMPTS_DIR=prompts_v2

# В коде
loader = get_loader()  # Автоматически использует из .env
```

### Если нужна обратная совместимость

```python
import os
from prompt_loader import get_loader

# Старый код может явно задать путь через env
if os.getenv('OLD_PROMPTS_PATH'):
    loader = PromptLoader(Path(os.getenv('OLD_PROMPTS_PATH')))
else:
    loader = get_loader()  # Используем новый способ
```

## Тестирование

```bash
# 1. Проверить с prompts (по умолчанию)
python rag_lg_agent.py "тест"

# 2. Проверить с prompts_v2
echo "PROMPTS_DIR=prompts_v2" >> .env
python rag_lg_agent.py "тест"

# 3. Проверить явную передачу
PROMPTS_DIR=my_custom_prompts python rag_lg_agent.py "тест"
```

## Связанные изменения

В той же сессии:
- Убран лишний слой `prompts.py` (053_REFACTOR_REMOVE_PROMPTS_LAYER.md)
- Увеличен MAX_ITERATIONS до 5
- Упрощены промпты в prompts/plan/
- Создана альтернативная версия prompts_v2/

## Рекомендации

### Для разработки
```bash
PROMPTS_DIR=prompts_v2  # Тестируем новые промпты
```

### Для продакшена
```bash
PROMPTS_DIR=prompts  # Стабильная версия
```

### Для экспериментов
```bash
PROMPTS_DIR=prompts_experiment  # Создаем копию и экспериментируем
```

## Итог

**Хардкод → Конфигурация**

```
Было:
prompts_dir = Path(__file__).parent / 'prompts_v2'  # <- в коде

Стало:
PROMPTS_DIR=prompts_v2  # <- в .env
```

Легко переключаться, гибко настраивать, удобно тестировать! 🎉

