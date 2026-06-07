# RAG - Техническая документация

## Архитектура

```
MCP-клиент (Continue.dev, Claude Code, ...)
         |
         | MCP protocol (HTTP или stdio)
         |
  kb_tools_mcp_http.py  /  kb_tools_mcp_stdio.py
         |
         | LangChain BaseTool.invoke()
         |
      kb_tools.py  (16 инструментов)
         |
         +---> clickhouse_store.py  (vector search, exact search)
         |           |
         |      ClickHouse DB  (soib_kcoi_v2.chunks)
         |
         +---> rag_chat.py  (regex_search, Settings)
```

Все инструменты читают данные исключительно из ClickHouse. Исходные `.md`-файлы
используются только при индексации (`rag_chat.py --reindex` / `md_splitter.py`).

## ClickHouse: схема таблицы

Таблица `soib_kcoi_v2.chunks`:

| Колонка | Тип | Описание |
|---------|-----|---------|
| `chunk_id` | String | UUID чанка |
| `source` | String | Имя файла-источника |
| `section` | String | Путь раздела (H1 > H2 > H3) |
| `chunk_type` | String | `""` (текст) или `"table"` |
| `content` | String | Текст чанка |
| `line_start` | UInt32 | Начальная строка в исходном файле |
| `line_end` | UInt32 | Конечная строка |
| `chunk_index` | UInt32 | Порядковый номер чанка в разделе |
| `table_headers` | Array(String) | Заголовки таблицы (если chunk_type = "table") |
| `embedding` | Array(Float32) | Вектор эмбеддинга (1024 dim, bge-m3) |

## Индексация

Процесс загрузки документов в ClickHouse:

1. `md_splitter.py` — рекурсивно читает `.md`-файлы из `KNOWLEDGE_DIR`
2. Разбивает по ATX-заголовкам и таблицам на чанки с метаданными
3. `clickhouse_store.py` — вычисляет эмбеддинги (bge-m3 через Ollama) и записывает в БД

```powershell
python rag_chat.py --reindex
```

## Инструменты: справочник по типам поиска

### Семантический (по смыслу)
`semantic_search`, `find_relevant_sections`

Используют cosineDistance по полю `embedding`.

### Точный (по подстроке)
`exact_search`, `exact_search_in_file`, `exact_search_in_file_section`, `multi_term_exact_search`

Используют `positionCaseInsensitiveUTF8()` в ClickHouse — корректно работает с кириллицей.

### Regex
`regex_search`, `find_abbreviation_expansion`

Сканируют исходные `.md`-файлы через Python `re`. Зависят от доступности `KNOWLEDGE_DIR`.

### Навигация
`list_sections`, `list_sources`, `list_all_sections`, `get_section_content`,
`get_neighbor_chunks`, `get_chunks_by_index`, `read_table`

Читают из ClickHouse по фильтрам (source, section, chunk_index).

## Переменные окружения

| Переменная | Описание |
|-----------|---------|
| `OLLAMA_BASE_URL` | URL Ollama (по умолчанию `http://localhost:11434`) |
| `OLLAMA_MODEL` | Модель для LLM |
| `OLLAMA_EMBED_MODEL` | Модель для эмбеддингов (bge-m3) |
| `KNOWLEDGE_DIR` | Путь к папке с `.md`-файлами |
| `CLICKHOUSE_HOST` | Хост ClickHouse |
| `CLICKHOUSE_PORT` | Порт ClickHouse (по умолчанию 8123) |
| `CLICKHOUSE_USERNAME` | Пользователь |
| `CLICKHOUSE_PASSWORD` | Пароль |
| `CLICKHOUSE_DATABASE` | База данных |
| `CLICKHOUSE_TABLE` | Таблица |
| `RETRIEVER_TOP_K` | Количество результатов семантического поиска |
| `MCP_HTTP_HOST` | Хост MCP HTTP сервера (по умолчанию `0.0.0.0`) |
| `MCP_HTTP_PORT` | Порт MCP HTTP сервера (по умолчанию `8000`) |
| `MCP_HTTP_DEBUG` | `1`/`true` — DEBUG-логи модулей kb_tools* |
