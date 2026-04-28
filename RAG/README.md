# RAG - Retrieval-Augmented Generation System

Консольный чат по корпусу документов `.md` с агентным подходом поиска и анализа информации.

## Стек технологий

- **LLM**: Ollama (qwen3:8b) - генерация и tool calling
- **Embeddings**: bge-m3 через Ollama (1024-мерные векторы)
- **Vector Store**: ClickHouse с cosineDistance
- **Framework**: LangChain 1.x + LangGraph
- **Streaming**: Live streaming токенов LLM в реальном времени

## Быстрый старт

```powershell
# Активировать виртуальное окружение
.\.venv\Scripts\Activate.ps1

# Базовый чат (простой RAG)
python rag_chat.py "что такое КЦОИ"

# LangGraph-агент (single-pass, рекомендуется)
python rag_lg_agent.py "найди все СУБД с IP-адресами"

# Интерактивный режим
python rag_lg_agent.py
```

## Режимы работы

| Скрипт | Стратегия | Когда использовать |
|--------|-----------|-------------------|
| `rag_chat.py` | 1 semantic search → LLM | Быстрые концептуальные вопросы |
| `rag_lg_agent.py` | **До 5 итераций** single-pass: plan → action → observation → refine → final + **автоматическое расширение контекста** | **Рекомендуется**: глубокий структурированный поиск с JSON-ответами |
| `rag_lc_agent.py` | ReAct + query expansion + evaluation | Общий случай с cross-session памятью |
| `rag_agent.py` | Фиксированный пайплайн (устаревший) | Сложные вопросы с таблицами |

### Особенности rag_lg_agent.py (v2)

- ✅ **До 5 итераций** уточнения вместо 3
- ✅ **Автоматическое расширение контекста**:
  - Анализирует результаты поиска с метками `line_start`
  - Автоматически вызывает `get_neighbor_chunks` (±3 чанка)
  - Максимум 5 расширений на итерацию
  - Прозрачное логирование всех операций
- ✅ **Рекомендации разделов документации**:
  - В финальном ответе агент предлагает разделы для изучения
  - Разделение по релевантности: **высокая** (прям точно помогут) и **средняя** (вроде полезные по теме)
  - Объяснение почему каждый раздел будет полезен
- ✅ **Развернутые и информативные ответы**:
  - Даже если точного ответа нет - агент описывает весь найденный контекст
  - Включает аналогии, примеры, связи между понятиями
  - Помогает пользователю понять общую картину и сориентироваться в теме
  - Детальные observation на каждой итерации с полным перечнем находок
- ✅ Полное логирование messages
- ✅ Следует system_prompt.md
- ✅ Retry при ошибках парсинга JSON

## Основные модули

| Модуль | Назначение |
|--------|------------|
| `rag_lg_agent.py` | ⭐ LangGraph агент (single-pass) с полным логированием |
| `kb_tools.py` | 14 LangChain Tools для работы с базой знаний |
| `clickhouse_store.py` | ClickHouseVectorStore - векторное хранилище |
| `llm_call_logger.py` | Логирование всех LLM вызовов и tool calls |
| `system_prompt.md` | Системный промпт для аналитического агента |
| `md_splitter.py` | Парсинг и индексация `.md` файлов |
| `session_memory.py` | Cross-session память в ClickHouse |
| `query_refiner.py` | Query expansion + Answer evaluation |

## Инструменты (Tools)

Агент имеет доступ к 14 специализированным инструментам:

- **Семантический поиск**: `semantic_search`, `find_relevant_sections` (PRIMARY)
- **Точный поиск**: `exact_search`, `exact_search_in_file`, `multi_term_exact_search`
- **Навигация**: `list_sections`, `list_sources`, `find_sections_by_term`
- **Чтение**: `get_section_content`, `read_table`, `get_neighbor_chunks`
- **Regex**: `regex_search` (IP, порты, коды)

## Логирование

Все LLM вызовы и tool calls логируются в `logs/_rag_llm.log`:
- ✅ Полная история messages (system, user, assistant, tool)
- ✅ Live streaming токенов LLM
- ✅ Аргументы и результаты всех инструментов
- ✅ DB запросы к ClickHouse

## Настройка

Создайте `.env` файл:

```env
# LLM + embeddings
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b              # Базовая модель (plan, action, observation, refine)
# OLLAMA_FINAL_MODEL=qwen2.5:14b     # Более мощная модель для финального ответа
OLLAMA_FINAL_MODEL=hf.co/hesamation/Qwen3.6-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q4_K_M  # Claude-distilled модель
OLLAMA_EMBED_MODEL=bge-m3

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
RETRIEVER_TOP_K=10
MAX_CONTEXT_CHARS=100000

# Logging
LLM_LOG_ENABLED=true
```

## Документация

Полная документация в папке `docs/`:
- [docs/README.md](docs/README.md) - Навигация по документации
- [docs/005_README.md](docs/005_README.md) - Подробное описание системы
- [.ai/](..ai/) - Отчеты о крупных изменениях

## Требования

```bash
pip install -r requirements.txt
```

Основные зависимости:
- langchain >= 1.2.15
- langgraph >= 1.1.9
- langchain-ollama >= 1.1.0
- clickhouse-connect >= 0.10.0
- pydantic >= 2.9.2
- markdown-it-py >= 4.0.0

## Индексация документов

```powershell
# Переиндексировать .md файлы в ClickHouse
python rag_chat.py --reindex
```

Поддерживаются:
- ATX заголовки (`# H1`, `## H2`)
- Pipe-таблицы и Grid-таблицы
- Вложенные папки (`**/*.md`)

## Команды интерактивного режима

| Команда | Действие |
|---------|----------|
| `/reset` | Очистить историю диалога |
| `/verbose` | Показать/скрыть детали tool calls |
| `/memory` | Информация о cross-session памяти |
| `exit` | Выход |

## Статус проверки

✅ Все основные скрипты проверены и работоспособны (2026-04-28)

---

**Примечание**: Рекомендуется использовать `rag_lg_agent.py` для всех типов запросов - он обеспечивает наилучший баланс между скоростью и качеством ответов.

