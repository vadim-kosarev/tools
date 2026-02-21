# Рефакторинг: Разделение на модули

## Изменения

Код был рефакторирован и разделен на три модуля для лучшей организации и переиспользования:

### 1. `llm_config.py` - Конфигурация LLM

**Содержит:**
- ✅ DeepSeek API конфигурация (API key, base URL, model, temperature)
- ✅ LangSmith трейсинг конфигурация (API key, project, endpoint)
- ✅ LangChain настройка
- ✅ Функция `configure_langsmith_tracing()` для автоматической настройки
- ✅ Декоратор `@traceable` (с fallback если langsmith не установлен)

**Переменные окружения:**
```python
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "...")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TEMPERATURE = 0.7

LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "...")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "proj1")
LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"
```

**Использование:**
```python
from llm_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    traceable,
    LANGSMITH_AVAILABLE
)
```

### 2. `llm_dto.py` - Pydantic DTO модели

**Содержит все Pydantic модели:**
- ✅ `ToolDefinition` - определение инструмента
- ✅ `SystemPromptConfig` - конфигурация системного промпта
- ✅ `UserProfile` - профиль пользователя
- ✅ `ConversationSummary` - суммари разговора
- ✅ `ChatMessage` - сообщение в чате
- ✅ `LLMRequest` - полный запрос к LLM

**Все модели имеют:**
- Метод `render()` - для рендеринга в текстовый формат
- Метод `to_langchain_messages()` - для конвертации в LangChain messages (у LLMRequest)
- Pydantic валидация полей
- JSON сериализация/десериализация

**Использование:**
```python
from llm_dto import (
    ToolDefinition,
    SystemPromptConfig,
    UserProfile,
    ChatMessage,
    LLMRequest
)

# Создание запроса
request = LLMRequest(
    system_prompt=SystemPromptConfig(...),
    user_profile=UserProfile(...),
    user_query="Привет!",
    chat_history=[]
)

# Конвертация в LangChain messages
messages = request.to_langchain_messages()

# JSON сериализация
json_str = request.model_dump_json(indent=2)
```

### 3. `langchain_deepseek.py` - Основная логика

**Содержит:**
- ✅ Logging helpers (`dump_request`, `dump_response`)
- ✅ Tools definition (`web_search`, `code_execution`, `browse_page`)
- ✅ LangChain setup (`create_deepseek_llm`, `create_deepseek_with_tools`)
- ✅ Tool calling logic (`invoke_with_tools`)
- ✅ Примеры использования (`main`, `manual_tool_calling_example`)

**Импортирует из модулей:**
```python
from llm_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE,
    LANGSMITH_API_KEY,
    LANGSMITH_PROJECT,
    traceable,
    LANGSMITH_AVAILABLE
)

from llm_dto import (
    ToolDefinition,
    SystemPromptConfig,
    UserProfile,
    ConversationSummary,
    ChatMessage,
    LLMRequest
)
```

## Преимущества рефакторинга

### 1. Разделение ответственности
- **llm_config.py** - только конфигурация
- **llm_dto.py** - только модели данных
- **langchain_deepseek.py** - только бизнес-логика

### 2. Переиспользование
```python
# Можно использовать DTO в других проектах
from llm_dto import LLMRequest, SystemPromptConfig

# Можно переопределить конфигурацию
from llm_config import DEEPSEEK_MODEL
```

### 3. Тестируемость
- Легко мокировать конфигурацию
- Легко тестировать Pydantic модели отдельно
- Легко тестировать бизнес-логику отдельно

### 4. Читаемость
- Меньше кода в каждом файле
- Четкая структура
- Легче найти нужное

## Обратная совместимость

Все существующие API остались без изменений:
- ✅ `create_deepseek_llm()` - работает так же
- ✅ `invoke_with_tools()` - работает так же
- ✅ `LLMRequest` - работает так же
- ✅ `@traceable` - работает так же

## Структура файлов

```
bReader/
├── llm_config.py          # Конфигурация
├── llm_dto.py             # Pydantic модели
├── langchain_deepseek.py  # Основная логика
├── requirements_langchain.txt
└── LANGSMITH_TRACING_README.md
```

## Запуск

```bash
# Установка зависимостей
pip install -r requirements_langchain.txt

# Установка переменных окружения
export DEEPSEEK_API_KEY='your-key'
export LANGSMITH_API_KEY='your-key'

# Запуск
python langchain_deepseek.py
```

## Миграция существующего кода

Если у вас был код, использующий старый `langchain_deepseek.py`, обновите импорты:

**Было:**
```python
from langchain_deepseek import (
    ToolDefinition,
    SystemPromptConfig,
    LLMRequest,
    DEEPSEEK_API_KEY
)
```

**Стало:**
```python
from llm_config import DEEPSEEK_API_KEY
from llm_dto import ToolDefinition, SystemPromptConfig, LLMRequest
from langchain_deepseek import create_deepseek_llm, invoke_with_tools
```

## Проверка работоспособности

```bash
# Компиляция
python -m py_compile llm_config.py llm_dto.py langchain_deepseek.py

# Запуск тестов
python langchain_deepseek.py
```

✅ Все файлы компилируются без ошибок
✅ Весь функционал сохранен
✅ Код стал более модульным и поддерживаемым

