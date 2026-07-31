# MCP Server для Knowledge Base Tools

Сервер на официальном **MCP SDK** (пакет `mcp`), публикующий инструменты базы знаний
по протоколу **MCP** (Model Context Protocol). Инструменты берутся из
`kb_tools.create_kb_tools()`, их JSON-схемы — из `args_schema` LangChain-инструментов.

## Транспорты

| Файл | Транспорт | Запуск | Назначение |
|------|-----------|--------|-----------|
| `kb_tools_mcp_http.py` | Streamable HTTP | `uv` / `python` | Сетевой доступ, любой HTTP MCP-клиент |
| `kb_tools_mcp_stdio.py` | stdio | клиент сам запускает процесс | Локальные клиенты (Continue.dev) |

## Инструменты (13)

| Инструмент | Назначение |
|-----------|-----------|
| `semantic_search` | Семантический поиск по эмбеддингам (концептуальные вопросы) |
| `exact_search` | Точный поиск по подстроке (термины, названия, коды) |
| `multi_term_exact_search` | Поиск по нескольким терминам с ранжированием по покрытию |
| `search_section_by_name` | Поиск разделов: название + семантика + опечатки (ngram) + термины |
| `regex_search` | RE2-regex по содержимому чанков (IP, порты, VLAN) |
| `search_abbreviation` | Расшифровка аббревиатур (КЦОИ, RAM, API) |
| `read_table` | Чтение строк таблицы по названию раздела |
| `get_section_content` | Полный текст раздела (сборка из чанков ClickHouse) |
| `list_sections` | Дерево разделов документации |
| `get_neighbor_chunks` | Соседние чанки вокруг найденного фрагмента |
| `get_chunks_by_index` | Чанки по индексам (source, section, chunk_indices) |
| `list_sources` | Список файлов в базе знаний |
| `list_all_sections` | Все уникальные пары (source, section) |

## Установка

Все зависимости, включая MCP (`mcp`, `uvicorn`, `starlette`), — в одном файле:

```powershell
pip install -r requirements.txt
```

## Запуск: Streamable HTTP

```powershell
# Скрипт (активирует .venv, при необходимости ставит зависимости, запускает через uv)
.\start_kb_tools_mcp_http.ps1                 # 0.0.0.0:8000
.\start_kb_tools_mcp_http.ps1 -Port 8765

# Или вручную (venv активирован)
uv run --active --no-project kb_tools_mcp_http.py --host 0.0.0.0 --port 8000
```

Параметры: `--host`, `--port`, `--json-response` (ответы как `application/json` вместо
SSE — удобно для отладки). Env-аналоги: `MCP_HTTP_HOST`, `MCP_HTTP_PORT`, `MCP_HTTP_DEBUG`.

### Эндпоинты

- `POST /mcp` — протокол MCP (Streamable HTTP). Это адрес для MCP-клиентов.
- `GET /health` — статус сервера и список инструментов:

```powershell
Invoke-RestMethod http://localhost:8000/health
# { "status": "ok", "transport": "streamable-http", "mcp_endpoint": "/mcp",
#   "tools_count": 13, "tools": [ ... ] }
```

## Запуск: stdio

Отдельно запускать не нужно — клиент стартует процесс сам через `kb_tools_mcp_stdio.bat`
(активирует `.venv` и запускает `kb_tools_mcp_stdio.py`).

Ручная проверка:

```powershell
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python kb_tools_mcp_stdio.py 2>$null
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python kb_tools_mcp_stdio.py 2>$null
```

## Подключение клиента

### Streamable HTTP (нужен запущенный сервер)

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
    command: C:/dev/github.com/vadim-kosarev/tools/RAG/kb_tools_mcp_stdio.bat
    args: []
    env: {}
```

Готовый пример: `continue.config.example.yaml`.

## Проверка

```powershell
# Автотест: /health + MCP handshake (initialize -> tools/list -> tools/call)
.\test_mcp_server.ps1
.\test_mcp_server.ps1 -Port 8765
```

Через браузер: откройте `kb_tools_mcp_client.html` — минимальный HTML-клиент для
ручного вызова инструментов по HTTP.

Через Python (MCP SDK):

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    async with streamablehttp_client("http://localhost:8000/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])
            res = await session.call_tool("exact_search", {"substring": "СКДПУ"})
            print(res.content[0].text[:300])


asyncio.run(main())
```

## Как устроено

- `mcp.server.lowlevel.Server` с обработчиками `list_tools` / `call_tool`.
- Транспорт Streamable HTTP через `StreamableHTTPSessionManager`, смонтированный в
  Starlette-приложение (`/mcp`), сервер — `uvicorn`. Режим **stateless**.
- Синхронный `tool.invoke()` выполняется в пуле потоков (`anyio.to_thread`), чтобы не
  блокировать event loop.
- DNS-rebinding защита транспорта **отключена** (`TransportSecuritySettings`): сервер
  локальный, а при включённой защите с пустым allowlist все запросы отклоняются.

## Решение проблем

- **`ModuleNotFoundError: mcp`** — пакет не установлен в `.venv`:
  `pip install -r requirements.txt`.
- **`uv run` не видит зависимости** — в репозитории нет `pyproject.toml`, а `.venv` общий
  и лежит в корне репозитория. Запускать с активированным venv и флагами
  `uv run --active --no-project ...` (именно так делает `start_kb_tools_mcp_http.ps1`).
- **`.venv` не найден скриптом** — окружение в корне репозитория (`..\.venv`), не в `RAG`.
- **Клиент не подключается по HTTP** — адрес именно `/mcp` (не `/`), сервер должен быть
  запущен; проверьте `GET /health`.
