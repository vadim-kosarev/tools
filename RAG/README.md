# RAG - Knowledge Base Tools

Система инструментов для работы с корпусом документов `.md`. Инструменты публикуются
по протоколу **MCP** (Model Context Protocol) — AI-агенты подключаются как MCP-клиенты.

## Стек технологий

- **Embeddings**: bge-m3 через Ollama (1024-мерные векторы)
- **Vector Store**: ClickHouse с cosineDistance
- **Framework**: LangChain — инструменты (`BaseTool`)
- **MCP**: официальный MCP SDK, два транспорта (Streamable HTTP + stdio)

## Быстрый старт

```powershell
# Активировать виртуальное окружение
.\.venv\Scripts\Activate.ps1

# MCP Server (Streamable HTTP)
.\start_kb_tools_mcp_http.ps1
# Endpoint: http://localhost:8000/mcp  |  Статус: http://localhost:8000/health

# CLI для инструментов базы знаний
python kb_tools.py list                                          # Список инструментов
python kb_tools.py help semantic_search                          # Справка по инструменту
python kb_tools.py run exact_search substring=КЦОИ limit=10     # Выполнить поиск

# Базовый RAG чат / переиндексация
python rag_chat.py "что такое КЦОИ"
python rag_chat.py --reindex
```

## MCP Server

Все 16 инструментов публикуются по протоколу MCP. Доступны два транспорта:

| Файл | Транспорт | Когда использовать |
|------|-----------|--------------------|
| `kb_tools_mcp_http.py` | Streamable HTTP | Сетевой доступ, любой HTTP MCP-клиент |
| `kb_tools_mcp_stdio.py` | stdio | Локальный запуск клиентом (Continue.dev) |

### HTTP (Streamable HTTP)

```powershell
.\start_kb_tools_mcp_http.ps1                                   # 0.0.0.0:8000
.\start_kb_tools_mcp_http.ps1 -Port 8765
.\start_kb_tools_mcp_http.ps1 -DebugLog                         # DEBUG-логи kb_tools*

# Или вручную (venv активирован)
uv run --active --no-project kb_tools_mcp_http.py --host 0.0.0.0 --port 8000

# Проверка статуса
Invoke-RestMethod http://localhost:8000/health
```

Конфиг MCP-клиента:
```yaml
mcpServers:
  - name: kb-tools
    transport:
      type: streamable-http
      url: http://localhost:8000/mcp
```

### stdio (Continue.dev)

```yaml
mcpServers:
  - name: kb-tools
    command: C:/dev/github.com/vadim-kosarev/tools.0/RAG/kb_tools_mcp_stdio.bat
```

Готовые примеры конфига: `continue.config.example.yaml`, `continue.config.example.json`.

### Тестирование

```powershell
.\test_mcp_server.ps1           # /health + MCP handshake + вызов инструмента
.\test_mcp_server.ps1 -Port 8765
```

## Инструменты базы знаний (16 Tools)

### Семантический поиск
- `semantic_search` — поиск по эмбеддингам (концептуальные вопросы)
- `find_relevant_sections` — двухэтапный поиск: по названию раздела + по содержимому

### Точный поиск
- `exact_search` — case-insensitive поиск по подстроке (UTF-8, включая кириллицу)
- `exact_search_in_file` — точный поиск в конкретном файле
- `exact_search_in_file_section` — точный поиск в разделе файла
- `multi_term_exact_search` — поиск по списку терминов с ранжированием по покрытию

### Специализированный поиск
- `find_abbreviation_expansion` — расшифровка аббревиатур (КЦОИ, RAM, API)
- `find_sections_by_term` — разделы, содержащие термин в тексте
- `regex_search` — regex-поиск (IP, порты, коды)

### Навигация и чтение
- `list_sections` — дерево разделов базы знаний
- `list_sources` — список файлов с количеством чанков
- `list_all_sections` — уникальные пары (source, section)
- `get_section_content` — полный текст раздела
- `get_neighbor_chunks` — соседние чанки вокруг найденного фрагмента
- `get_chunks_by_index` — чанки по индексам (source, section, chunk_indices)
- `read_table` — строки таблицы по названию раздела

## Основные модули

| Модуль | Назначение |
|--------|------------|
| `kb_tools_mcp_http.py` | MCP-сервер (Streamable HTTP) |
| `kb_tools_mcp_stdio.py` | MCP-сервер (stdio) |
| `kb_tools.py` | 16 LangChain Tools + CLI |
| `clickhouse_store.py` | ClickHouseVectorStore (векторное хранилище) |
| `rag_chat.py` | Базовый RAG чат + индексация + `regex_search` |
| `md_splitter.py` | Парсинг и индексация `.md` файлов |
| `llm_call_logger.py` | Логирование LLM-вызовов |
| `logging_config.py` | Настройка логирования |
| `text_utils.py` | Нормализация текста для эмбеддингов |

## Настройка

Создайте `.env` (используйте `.env.example` как шаблон):

```env
# LLM + embeddings
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
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
```

## Индексация документов

```powershell
# Переиндексировать .md файлы в ClickHouse
python rag_chat.py --reindex
```

Поддерживаются:
- ATX заголовки (`# H1`, `## H2`, `### H3`)
- Pipe-таблицы (GitHub Flavored Markdown)
- Grid-таблицы (reStructuredText style)
- Вложенные папки (`**/*.md` рекурсивный поиск)

## Документация

- [_MCP_SERVER.md](_MCP_SERVER.md) — подробно про MCP-сервер, эндпоинты, клиенты
- [_QUICKSTART_MCP.md](_QUICKSTART_MCP.md) — быстрый старт MCP для Continue.dev
- [docs/kb_tools.md](docs/kb_tools.md) — CLI интерфейс `kb_tools.py`
- [docs/_find_abbreviation_expansion.md](docs/_find_abbreviation_expansion.md) — инструмент расшифровки аббревиатур
- [.ai/](.ai/) — отчёты об изменениях по датам

## Требования

```powershell
pip install -r requirements.txt
pip install -r requirements_mcp.txt   # дополнительно для MCP-сервера
```
