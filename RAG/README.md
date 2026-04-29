# RAG - Retrieval-Augmented Generation System

Консольный чат по корпусу документов `.md` с агентным подходом поиска и анализа информации.

## Стек технологий

- **LLM**: Ollama (qwen3:8b базовая, опционально qwen2.5:14b или Claude-distilled модели для финального ответа)
- **Embeddings**: bge-m3 через Ollama (1024-мерные векторы)
- **Vector Store**: ClickHouse с cosineDistance
- **Framework**: LangChain 1.x + LangGraph
- **Промпты**: Jinja2-шаблоны (prompts_v2/)
- **Streaming**: Live streaming токенов LLM в реальном времени

## Быстрый старт

```powershell
# Активировать виртуальное окружение
.\.venv\Scripts\Activate.ps1

# LangGraph-агент v3 (линейная архитектура, рекомендуется)
python rag_lg_agent.py "найди все СУБД с IP-адресами"

# Интерактивный режим
python rag_lg_agent.py

# Базовый чат (простой RAG без агента)
python rag_chat.py "что такое КЦОИ"

# CLI для инструментов базы знаний
python kb_tools.py list                                          # Список всех инструментов
python kb_tools.py help semantic_search                          # Справка по инструменту
python kb_tools.py run exact_search substring=КЦОИ limit=10      # Выполнить поиск
```

## Режимы работы

| Скрипт | Стратегия | Когда использовать |
|--------|-----------|-------------------|
| `rag_lg_agent.py` | **v3 (линейная)**: planner → tool_selector → tool_executor → analyzer → refiner → final | **Рекомендуется**: основной режим работы, оптимальный баланс скорости и качества |
| `rag_chat.py` | 1 semantic search → LLM | Быстрые концептуальные вопросы без глубокого анализа |
| `rag_agent.py` | Многошаговый пайплайн с рефакторингом | Сложные многоэтапные запросы |
| `kb_tools.py` | CLI интерфейс для инструментов | Прямой вызов инструментов из командной строки |

### Архитектура rag_lg_agent.py v3 (линейная)

**Простая линейная цепочка без циклов:**
```
START → planner → tool_selector → tool_executor → analyzer → refiner → final → END
```

**Особенности:**
- ✅ **Линейный граф** - без циклов, всегда 6 узлов (быстрее, предсказуемее)
- ✅ **История и контекст раздельно**:
  - `history` - полная структурированная история действий (для LLM контекста)
  - `context` - отфильтрованные результаты для финального ответа
- ✅ **Retry логика** - автоматическое исправление ошибок парсинга JSON (до 2 попыток)
- ✅ **Детальное логирование** - все LLM вызовы, tool calls и результаты в `logs/_rag_llm.log`
- ✅ **Prompts v2** - Jinja2-шаблоны в `prompts_v2/` (легче изменять и тестировать)
- ✅ **Самооценка агента** - поле `self_assessment` в финальном ответе
- ✅ **Рекомендации разделов** - агент предлагает разделы для дальнейшего изучения

## Основные модули

| Модуль | Назначение |
|--------|------------|
| `rag_lg_agent.py` | ⭐ LangGraph агент v3 (линейная архитектура) с детальным логированием |
| `kb_tools.py` | 15 LangChain Tools для работы с базой знаний + CLI интерфейс |
| `clickhouse_store.py` | ClickHouseVectorStore - векторное хранилище с поддержкой score |
| `llm_call_logger.py` | Логирование всех LLM вызовов, tool calls и структурированной истории |
| `prompt_loader.py` | Jinja2 загрузчик промптов из `prompts_v2/` |
| `schema_generator.py` | Генерация JSON схем для structured output из Pydantic моделей |
| `pydantic_utils.py` | Конвертация Pydantic/dict в Markdown для LLM |
| `md_splitter.py` | Парсинг и индексация `.md` файлов с ATX/Grid таблицами |
| `rag_chat.py` | Простой RAG чат + базовые функции (build_vectorstore, regex_search) |
| `rag_agent.py` | Многошаговый пайплайн агент (альтернативный режим) |

## Инструменты базы знаний (Tools)

Агент имеет доступ к **15 специализированным инструментам** (13 автоматически выбираемых + 2 вспомогательных):

### Семантический поиск
- `semantic_search` - поиск по эмбеддингам с score (PRIMARY)
- `find_relevant_sections` - двухэтапный поиск: по названию раздела + по терминам в содержимом

### Точный поиск
- `exact_search` - case-insensitive поиск по подстроке
- `exact_search_in_file` - точный поиск в конкретном файле
- `exact_search_in_file_section` - точный поиск в разделе файла
- `multi_term_exact_search` - поиск по списку терминов с ранжированием по покрытию

### Специализированный поиск
- `find_abbreviation_expansion` - поиск расшифровки аббревиатур (КЦОИ → "Корпоративный Центр...")
- `find_sections_by_term` - поиск разделов содержащих термин
- `regex_search` - regex-поиск в исходных .md файлах (IP, порты, коды)

### Навигация и чтение
- `list_sections` - дерево разделов базы знаний
- `list_sources` - список файлов с количеством чанков
- `list_all_sections` - уникальные пары (source, section)
- `get_section_content` - полный текст раздела из исходного файла
- `get_neighbor_chunks` - соседние чанки вокруг якоря по line_start
- `read_table` - чтение строк таблицы по разделу

### CLI интерфейс для инструментов

```powershell
# Список всех инструментов с параметрами
python kb_tools.py list

# Детальная справка по инструменту
python kb_tools.py help find_abbreviation_expansion

# Запуск инструмента
python kb_tools.py run exact_search substring=КЦОИ limit=10
python kb_tools.py run find_abbreviation_expansion abbreviation=RAM
python kb_tools.py run semantic_search query="что такое RAG" top_k=5
```

**Подробнее:** См. файл `KB_TOOLS_CLI.md` для полного описания CLI интерфейса.

## Логирование

Все LLM вызовы, tool calls и структурированная история логируются в `logs/_rag_llm.log`:

- ✅ **Структурированная история** - HistoryEntry объекты (user_prompt, llm_reply, tool_execution, tool_summary, refiner_summary)
- ✅ **LLM запросы и ответы** - полные messages с system, user, assistant, tool ролями
- ✅ **Live streaming токенов** - потоковый вывод LLM в реальном времени
- ✅ **Tool calls** - аргументы и результаты всех инструментов в читаемом Markdown формате
- ✅ **Retry попытки** - логирование raw-ответов при ошибках парсинга JSON
- ✅ **DB запросы** - SQL запросы к ClickHouse с параметрами
- ✅ **Полная трассировка** - от запроса до финального ответа со всеми промежуточными шагами

**Формат логов:**
```
## 📋 2026-04-29 12:34:56 PLANNER NODE START
## 🎯 Вопрос: найди все СУБД с IP-адресами

## 🔧 TOOL_SELECTOR NODE - выбор инструментов
## ✅ Выбрано инструментов: 3

## ⚙️ TOOL_EXECUTOR NODE - выполнение инструментов
## 📦 Инструмент 1/3: semantic_search
##   Аргументы: {"query": "СУБД базы данных", "top_k": 10}
##   Результат: [10 чанков найдено]

## 📊 ANALYZER NODE - анализ результатов
## ✨ Проанализировано: 10 результатов → 8 релевантных

## 🎯 FINAL NODE - формирование ответа
## ✅ Финальный ответ готов (confidence: 0.95)
```

**Просмотр логов:**
```powershell
# Последние записи
python show_logs.py

# Просмотр в редакторе
notepad logs/_rag_llm.log
```

## Настройка

Создайте `.env` файл (используйте `.env.example` как шаблон):

```env
# LLM + embeddings
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b              # Базовая модель (planner, tool_selector, analyzer)

# Опциональная модель для финального ответа (более мощная)
# OLLAMA_FINAL_MODEL=qwen2.5:14b     # Более мощная модель
OLLAMA_FINAL_MODEL=hf.co/hesamation/Qwen3.6-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q4_K_M  # Claude-distilled

OLLAMA_EMBED_MODEL=bge-m3          # Эмбеддинги (1024-мерные векторы)

# Knowledge base
KNOWLEDGE_DIR=path/to/markdown/files

# ClickHouse
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USERNAME=clickhouse
CLICKHOUSE_PASSWORD=clickhouse
CLICKHOUSE_DATABASE=soib_kcoi_v2
CLICKHOUSE_TABLE=chunks

# Search settings
RETRIEVER_TOP_K=10                 # Количество результатов при семантическом поиске
MAX_CONTEXT_CHARS=100000           # Максимум символов в контексте для LLM

# Logging
LLM_LOG_ENABLED=true               # Включить логирование LLM вызовов

# Prompts (v2 с Jinja2)
PROMPTS_DIR=prompts_v2             # Папка с шаблонами промптов
```

### Промпты v2 (Jinja2)

Все промпты находятся в папке `prompts_v2/` и используют Jinja2-шаблоны:

```
prompts_v2/
├── plan/           - промпты для planner узла
├── action/         - промпты для tool_selector узла
├── analyzer/       - промпты для analyzer узла
├── refiner/        - промпты для refiner узла
├── final/          - промпты для final узла
└── retry/          - промпты для retry логики
```

**Преимущества:**
- ✅ Условная логика (`{% if %}`, `{% for %}`)
- ✅ Включение других шаблонов (`{% include %}`)
- ✅ Переменные и фильтры (`{{ variable | filter }}`)
- ✅ Легко изменять без изменения кода Python

## Документация

### Основная документация
- [docs/005_README.md](docs/005_README.md) - Детальная техническая документация системы (архитектура, API, примеры)
- [docs/001_find_abbreviation_expansion.md](docs/001_find_abbreviation_expansion.md) - Документация инструмента поиска расшифровок аббревиатур
- [KB_TOOLS_CLI.md](KB_TOOLS_CLI.md) - CLI интерфейс для инструментов базы знаний
- [.ai/](.ai/) - Отчеты о крупных изменениях по датам

### Справка по командам
```bash
# Справка по основному агенту
python rag_lg_agent.py --help

# Справка по инструментам
python kb_tools.py list              # Список всех инструментов
python kb_tools.py help <tool_name>  # Детальная справка по инструменту
```


## Требования

```bash
pip install -r requirements.txt
```

Основные зависимости:
- langchain >= 1.0
- langgraph >= 1.0
- langchain-ollama >= 0.3
- langchain-community >= 0.3
- clickhouse-connect >= 0.7
- pydantic >= 2.7
- pydantic-settings >= 2.5
- python-dotenv >= 1.0
- markdown-it-py >= 3.0
- flashrank >= 0.2

## Индексация документов

```powershell
# Переиндексировать .md файлы в ClickHouse
python rag_chat.py --reindex

# Или через CLI агента
python rag_lg_agent.py --reindex
```

Поддерживаются:
- **ATX заголовки** (`# H1`, `## H2`, `### H3`, etc.)
- **Pipe-таблицы** (GitHub Flavored Markdown)
- **Grid-таблицы** (reStructuredText style)
- **Вложенные папки** (`**/*.md` рекурсивный поиск)
- **Метаданные** - автоматическое извлечение source, section, chunk_type, line_start/end

## Команды интерактивного режима

| Команда | Действие |
|---------|----------|
| `/reset` | Очистить историю диалога |
| `/verbose` | Показать/скрыть детали tool calls |
| `/debug` | Включить/выключить debug режим |
| `exit`, `quit` | Выход |

## Архитектура решения

```
┌─────────────────────────────────────────────────────────────┐
│                     RAG Agent (LangGraph)                    │
│                                                              │
│  START                                                       │
│    ↓                                                         │
│  planner (LLM)           - анализ вопроса и план поиска     │
│    ↓                                                         │
│  tool_selector (LLM)     - выбор инструментов + инструкции  │
│    ↓                                                         │
│  tool_executor           - выполнение инструментов          │
│    ↓                                                         │
│  analyzer                - фильтрация и анализ результатов  │
│    ↓                                                         │
│  refiner                 - решение о следующем шаге         │
│    ↓                                                         │
│  final (LLM)             - формирование ответа              │
│    ↓                                                         │
│  END                                                         │
└─────────────────────────────────────────────────────────────┘
         │                              │
         ↓                              ↓
┌──────────────────┐          ┌──────────────────┐
│   kb_tools.py    │          │  ClickHouse      │
│  15 инструментов │←────────→│  Vector Store    │
│  + CLI           │          │  (embeddings)    │
└──────────────────┘          └──────────────────┘
         │
         ↓
┌──────────────────┐
│  Исходные .md    │
│  файлы           │
│  (get_section,   │
│   regex_search)  │
└──────────────────┘
```

## Статус проверки

✅ **Все основные скрипты проверены и работоспособны**

- ✅ `rag_lg_agent.py` - линейная архитектура v3, retry логика, prompts v2
- ✅ `kb_tools.py` - 15 инструментов + CLI интерфейс
- ✅ `rag_chat.py` - базовый RAG чат + индексация
- ✅ `rag_agent.py` - многошаговый пайплайн агент
- ✅ Логирование - структурированная история в `_rag_llm.log`
- ✅ Промпты v2 - Jinja2 шаблоны в `prompts_v2/`

---

**Рекомендация**: Используйте `rag_lg_agent.py` как основной режим работы - он обеспечивает оптимальный баланс между скоростью, качеством и предсказуемостью результатов.


