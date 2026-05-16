# 🚀 Быстрый старт MCP Server

## Установка

```powershell
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
pip install -r requirements_mcp.txt
```

---

## ⚙️ Конфиг Continue.dev (`~/.continue/config.yaml`)

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

### Способ 2: SSE транспорт (нужен запущенный сервер)

Запустите сервер:
```powershell
.\start_kb_tools_mcp_http.ps1
```

Конфиг:
```yaml
mcpServers:
  - name: kb-tools
    transport:
      type: sse
      url: http://localhost:8000/sse
```

---

## 📁 Файлы MCP сервера

| Файл | Назначение |
|------|-----------|
| `kb_tools_mcp_stdio.py` | stdio транспорт (для Continue.dev `command`) |
| `kb_tools_mcp_stdio.bat` | bat-обёртка для запуска kb_tools_mcp_stdio.py с venv |
| `kb_tools_mcp_http.py` | SSE транспорт (HTTP сервер `uvicorn`) |
| `start_kb_tools_mcp_http.ps1` | скрипт запуска SSE сервера |
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

## Документация

- **Swagger UI (SSE режим):** http://localhost:8000/docs
- **Полная документация:** MCP_SERVER.md

Готово! 🎉

