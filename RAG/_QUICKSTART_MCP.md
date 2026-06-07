# Быстрый старт MCP Server

## Установка

```powershell
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
pip install -r requirements_mcp.txt
```

---

## Конфиг Continue.dev (`~/.continue/config.yaml`)

### Способ 1: stdio транспорт (рекомендуется — без отдельного сервера)

Continue.dev сам запускает процесс и общается через stdin/stdout.

```yaml
name: KB Tools MCP
version: 0.0.1
schema: v1
mcpServers:
  - name: kb-tools
    command: C:/dev/github.com/vadim-kosarev/tools.0/RAG/kb_tools_mcp_stdio.bat
    args: []
    env: {}
```

Или напрямую через Python (если venv активирован):

```yaml
name: KB Tools MCP
version: 0.0.1
schema: v1
mcpServers:
  - name: kb-tools
    command: C:/dev/github.com/vadim-kosarev/tools.0/.venv/Scripts/python.exe
    args:
      - C:/dev/github.com/vadim-kosarev/tools.0/RAG/kb_tools_mcp_stdio.py
    env: {}
```

### Способ 2: Streamable HTTP транспорт (нужен запущенный сервер)

Запустите сервер (через uv, использует `.venv` проекта):
```powershell
.\start_kb_tools_mcp_http.ps1                 # http://localhost:8000/mcp
# или вручную (venv активирован):
uv run --active --no-project kb_tools_mcp_http.py --host 0.0.0.0 --port 8000
```

Проверка статуса:
```powershell
Invoke-RestMethod http://localhost:8000/health
```

Конфиг:
```yaml
mcpServers:
  - name: kb-tools
    transport:
      type: streamable-http
      url: http://localhost:8000/mcp
```

---

## Файлы MCP сервера

| Файл | Назначение |
|------|-----------|
| `kb_tools_mcp_stdio.py` | stdio транспорт (для Continue.dev `command`) |
| `kb_tools_mcp_stdio.bat` | bat-обёртка для запуска с venv |
| `kb_tools_mcp_http.py` | Streamable HTTP транспорт (официальный MCP SDK) |
| `start_kb_tools_mcp_http.ps1` | запуск HTTP сервера через uv |
| `continue.config.example.yaml` | готовый конфиг с вариантами |

---

## Проверка stdio вручную

```powershell
# Тест handshake
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python kb_tools_mcp_stdio.py 2>$null

# Тест списка инструментов
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python kb_tools_mcp_stdio.py 2>$null
```

---

## Ссылки

- Статус сервера: http://localhost:8000/health
- MCP endpoint: http://localhost:8000/mcp
- Подробно: [_MCP_SERVER.md](_MCP_SERVER.md)
