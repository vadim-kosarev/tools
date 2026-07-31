# RAG - агент по локальной документации

RAG-агент и инструменты работы с корпусом документов **.docx** (и `.md`).
Всё работает локально: LLM и эмбеддинги — через Ollama, хранилище — ClickHouse,
наружу не уходит ничего. Те же инструменты публикуются по протоколу **MCP**,
поэтому базой знаний может пользоваться и внешний AI-клиент.

## Стек технологий

- **Документы**: python-docx (.docx), markdown-it-py (.md)
- **Embeddings**: bge-m3 через Ollama (1024-мерные векторы)
- **Vector Store**: ClickHouse с cosineDistance
- **Агент**: tool-calling поверх LangChain-инструментов, локальная LLM в Ollama
- **MCP**: официальный MCP SDK, два транспорта (Streamable HTTP + stdio)

```
документы .docx/.md
      |  docx_splitter.py / md_splitter.py  (общий контракт чанка — chunking.py)
      v
  ClickHouse (chunks + chunks_sections)
      ^
      |  kb_tools.py — 13 инструментов
      |
  rag_agent.py (локальная LLM)     kb_tools_mcp_http.py / _stdio.py (внешние клиенты)
```

## Быстрый старт

```powershell
# Виртуальное окружение (venv в корне репозитория)
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. Проиндексировать корпус из KNOWLEDGE_DIR
python rag_chat.py --reindex

# 2. Спросить агента
python rag_agent.py ask "какие серверы входят в состав системы"
python rag_agent.py chat                    # интерактивный режим
python rag_agent.py tools                   # список инструментов

# Прямой вызов инструментов без LLM
python kb_tools.py list
python kb_tools.py run exact_search substring=КЦОИ limit=10
```

## Агент

`rag_agent.py` — tool-calling агент с ограниченным циклом: LLM сама выбирает
инструменты, выполняет их против ClickHouse и формирует ответ. После
`AGENT_MAX_ITERATIONS` раундов агент обязан ответить без вызова инструментов,
поэтому зациклиться он не может.

```powershell
python rag_agent.py ask "что такое КЦОИ" --show-tools   # с трассой вызовов
python rag_agent.py --debug ask "..."                   # DEBUG-логи
python rag_agent.py --env ..\other.env chat             # другой .env
```

В ответе агент печатает разделы-источники, попавшие в контекст, — по ним видно,
откуда взяты факты.

| Переменная | Назначение |
|-----------|-----------|
| `AGENT_MAX_ITERATIONS` | Максимум раундов вызова инструментов (6) |
| `AGENT_MAX_TOOL_CHARS` | Предел символов результата инструмента, отдаваемого LLM (12000) |
| `OLLAMA_TIMEOUT` | Таймаут запроса к Ollama в секундах (300) |

## Индексация документов

```powershell
python rag_chat.py --reindex             # пересоздать таблицу и проиндексировать заново
python kb_tools.py build-section-index   # только индекс названий разделов
```

Обходятся все `**/*.docx` и `**/*.md` в `KNOWLEDGE_DIR`; временные файлы Word
(`~$имя.docx`) пропускаются. Ошибка одного документа не останавливает индексацию.

Что распознаётся в `.docx`:
- иерархия разделов по стилям заголовков, включая корпоративные
  (`Заголовок 2`, `Приложение: Заголовок 2`) и `w:outlineLvl` для нестандартных стилей;
- служебные заголовки (`Содержание`, `Оглавление`) не попадают в путь раздела;
- таблицы — построчно (`table_row`) плюс целиком (`table_full`), с заголовками столбцов;
- списки — с сохранением маркеров;
- документы с битыми связями пакета (`Target="NULL"`) чинятся в памяти, исходник не меняется.

Для `.md`: ATX-заголовки, pipe-таблицы (GFM), grid-таблицы (RST), вложенные папки.

## Инструменты базы знаний (13)

### Поиск по содержимому
- `semantic_search` — поиск по эмбеддингам (концептуальные вопросы)
- `exact_search` — case-insensitive поиск по подстроке (UTF-8, включая кириллицу)
- `multi_term_exact_search` — поиск по списку терминов с ранжированием по покрытию
- `regex_search` — RE2-regex по содержимому чанков (IP, порты, коды)
- `search_abbreviation` — расшифровка аббревиатур (КЦОИ, RAM, API)

### Поиск по названиям разделов
- `search_section_by_name` — четыре сигнала: подстрока в названии, семантика,
  нечёткое совпадение (ngram), термины в содержимом

### Чтение контента
- `get_section_content` — полный текст раздела
- `read_table` — строки таблицы по названию раздела
- `get_neighbor_chunks` — соседние чанки вокруг найденного фрагмента
- `get_chunks_by_index` — чанки по индексам (source, section, chunk_indices)

### Навигация
- `list_sources` — список файлов с количеством чанков
- `list_sections` — дерево разделов базы знаний
- `list_all_sections` — уникальные пары (source, section)

## MCP Server

Те же 13 инструментов публикуются по MCP — для Continue.dev, Claude Code и любых
других MCP-клиентов:

```powershell
.\start_kb_tools_mcp_http.ps1                 # 0.0.0.0:8000, endpoint /mcp
Invoke-RestMethod http://localhost:8000/health
```

Подробности, конфиги клиентов и тесты — [_MCP_SERVER.md](_MCP_SERVER.md).

## Основные модули

| Модуль | Назначение |
|--------|------------|
| `rag_agent.py` | RAG-агент: локальная LLM + инструменты, CLI (`ask` / `chat` / `tools`) |
| `kb_tools.py` | 13 LangChain Tools + CLI |
| `clickhouse_store.py` | ClickHouseVectorStore (векторное хранилище, все виды поиска) |
| `chunking.py` | Общий контракт чанка: метаданные, разбиение по размеру, таблицы |
| `docx_splitter.py` | Разбор `.docx` (python-docx): заголовки, таблицы, списки |
| `md_splitter.py` | Разбор `.md` (markdown-it-py) |
| `rag_chat.py` | Индексация корпуса, настройки (`Settings`), простой RAG-чат |
| `kb_tools_mcp_http.py` | MCP-сервер (Streamable HTTP) |
| `kb_tools_mcp_stdio.py` | MCP-сервер (stdio) |
| `llm_call_logger.py` | Логирование LLM-вызовов и запросов к БД |
| `logging_config.py` | Настройка логирования |
| `text_utils.py` | Нормализация текста для эмбеддингов |

## Настройка

Создайте `.env` (шаблон — `.env.example`):

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_EMBED_MODEL=bge-m3

KNOWLEDGE_DIR=path/to/documents

CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USERNAME=clickhouse
CLICKHOUSE_PASSWORD=clickhouse
CLICKHOUSE_DATABASE=kcoi
CLICKHOUSE_TABLE=chunks

RETRIEVER_TOP_K=10
AGENT_MAX_ITERATIONS=6
LOG_LEVEL=INFO
```

Модели должны быть загружены в Ollama:

```powershell
ollama pull bge-m3
ollama pull qwen3.5:9b
```

## Документация

- [_MCP_SERVER.md](_MCP_SERVER.md) — MCP-сервер: транспорты, эндпоинты, клиенты
- [docs/README.md](docs/README.md) — архитектура, схема ClickHouse, справочник по поиску
- `.ai/` — отчёты об изменениях по датам

## Требования

```powershell
pip install -r requirements.txt
```
