# MCP Server для Knowledge Base Tools

HTTP API сервер для доступа к инструментам базы знаний, совместимый с MCP (Model Context Protocol).

## 📋 Что это

MCP Server предоставляет REST API для всех 15 инструментов базы знаний:
- `semantic_search` - семантический поиск
- `exact_search` - точный поиск
- `exact_search_in_file` - точный поиск в файле
- `exact_search_in_file_section` - точный поиск в разделе файла
- `multi_term_exact_search` - мультитерминовый поиск
- `find_sections_by_term` - поиск разделов по термину
- `find_relevant_sections` - поиск релевантных разделов
- `regex_search` - regex поиск
- `find_abbreviation_expansion` - поиск расшифровки аббревиатур
- `read_table` - чтение таблицы
- `get_section_content` - получение содержимого раздела
- `list_sections` - список разделов
- `get_neighbor_chunks` - получение соседних чанков
- `get_chunks_by_index` - получение чанков по индексу
- `list_sources` - список источников
- `list_all_sections` - список всех разделов

## 🚀 Быстрый старт

### 1. Установка зависимостей

```bash
pip install fastapi uvicorn
```

### 2. Запуск сервера

```powershell
# Из папки RAG
cd C:\dev\github.com\vadim-kosarev\tools.0\RAG
python mcp_server.py

# Или через uvicorn
uvicorn mcp_server:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Проверка работоспособности

```powershell
# Проверка здоровья сервера
curl http://localhost:8000/health

# Список всех инструментов
curl http://localhost:8000/tools

# Документация API (Swagger)
# Откройте в браузере: http://localhost:8000/docs
```

## 📖 API Endpoints

### GET /health
Проверка работоспособности сервера.

**Response:**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "tools_count": 15,
  "vectorstore_status": "ok"
}
```

### GET /tools
Список всех доступных инструментов.

**Response:**
```json
{
  "tools": [
    {
      "name": "semantic_search",
      "description": "Semantic similarity search in the knowledge base...",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "..."},
          "top_k": {"type": "integer", "default": 10}
        }
      }
    },
    ...
  ],
  "count": 15
}
```

### GET /tools/{tool_name}
Описание конкретного инструмента.

**Example:**
```bash
curl http://localhost:8000/tools/semantic_search
```

**Response:**
```json
{
  "name": "semantic_search",
  "description": "Semantic similarity search...",
  "parameters": {...}
}
```

### POST /tools/{tool_name}
Вызов инструмента по имени из URL.

**Example:**
```bash
curl -X POST http://localhost:8000/tools/semantic_search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "СУБД базы данных",
    "top_k": 5
  }'
```

**Response:**
```json
{
  "tool": "semantic_search",
  "success": true,
  "result": {
    "query": "СУБД базы данных",
    "chunks": [...],
    "total_found": 5
  },
  "error": null
}
```

### POST /invoke
Универсальный эндпоинт (MCP-совместимый).

**Example:**
```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "semantic_search",
    "arguments": {
      "query": "СУБД базы данных",
      "top_k": 5
    }
  }'
```

**Response:** То же, что и для `/tools/{tool_name}`

## 💡 Примеры использования

### 1. Семантический поиск

```bash
curl -X POST http://localhost:8000/tools/semantic_search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "что такое КЦОИ",
    "top_k": 10,
    "chunk_type": "",
    "source": null,
    "section": null
  }'
```

### 2. Точный поиск

```bash
curl -X POST http://localhost:8000/tools/exact_search \
  -H "Content-Type: application/json" \
  -d '{
    "substring": "PostgreSQL",
    "limit": 20,
    "chunk_type": ""
  }'
```

### 3. Regex поиск IP-адресов

```bash
curl -X POST http://localhost:8000/tools/regex_search \
  -H "Content-Type: application/json" \
  -d '{
    "pattern": "\\d+\\.\\d+\\.\\d+\\.\\d+",
    "max_results": 50,
    "context_lines": 2
  }'
```

### 4. Поиск расшифровки аббревиатуры

```bash
curl -X POST http://localhost:8000/tools/find_abbreviation_expansion \
  -H "Content-Type: application/json" \
  -d '{
    "abbreviation": "КЦОИ"
  }'
```

### 5. Получение содержимого раздела

```bash
curl -X POST http://localhost:8000/tools/get_section_content \
  -H "Content-Type: application/json" \
  -d '{
    "source_file": "servers.md",
    "section": "База данных > PostgreSQL"
  }'
```

### 6. Список всех файлов

```bash
curl -X POST http://localhost:8000/tools/list_sources \
  -H "Content-Type: application/json" \
  -d '{}'
```

## 🔧 PowerShell примеры

### Семантический поиск

```powershell
$body = @{
    query = "СУБД базы данных"
    top_k = 5
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/tools/semantic_search" `
  -ContentType "application/json" `
  -Body $body
```

### Точный поиск

```powershell
$body = @{
    substring = "PostgreSQL"
    limit = 10
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/tools/exact_search" `
  -ContentType "application/json" `
  -Body $body
```

### MCP-совместимый вызов

```powershell
$body = @{
    tool = "semantic_search"
    arguments = @{
        query = "СУБД"
        top_k = 5
    }
} | ConvertTo-Json -Depth 3

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/invoke" `
  -ContentType "application/json" `
  -Body $body
```

## 🐍 Python примеры

### Использование с requests

```python
import requests

# Семантический поиск
response = requests.post(
    "http://localhost:8000/tools/semantic_search",
    json={
        "query": "СУБД базы данных",
        "top_k": 5
    }
)

result = response.json()
if result["success"]:
    print(f"Найдено: {result['result']['total_found']} результатов")
    for chunk in result["result"]["chunks"]:
        print(f"- {chunk['metadata']['source']}: {chunk['content'][:100]}...")
else:
    print(f"Ошибка: {result['error']}")
```

### Использование с httpx (async)

```python
import httpx
import asyncio

async def search_knowledge_base():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/tools/semantic_search",
            json={"query": "СУБД", "top_k": 5}
        )
        return response.json()

result = asyncio.run(search_knowledge_base())
print(result)
```

## 📊 Интеграция с LLM

### OpenAI Function Calling

```python
import openai
import requests

# 1. Получаем список инструментов
tools_response = requests.get("http://localhost:8000/tools")
tools = tools_response.json()["tools"]

# 2. Конвертируем в формат OpenAI
openai_tools = []
for tool in tools:
    openai_tools.append({
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"]
        }
    })

# 3. Используем в chat completion
response = openai.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "Найди информацию о СУБД"}
    ],
    tools=openai_tools,
    tool_choice="auto"
)

# 4. Вызываем выбранный инструмент
tool_call = response.choices[0].message.tool_calls[0]
tool_name = tool_call.function.name
tool_args = json.loads(tool_call.function.arguments)

# 5. Выполняем вызов через MCP сервер
result = requests.post(
    f"http://localhost:8000/tools/{tool_name}",
    json=tool_args
).json()

print(result)
```

### Anthropic Claude (MCP native)

```python
import anthropic

client = anthropic.Anthropic(api_key="...")

# Используем MCP сервер как tool provider
# (требуется MCP SDK - в разработке)
```

## 🔐 Безопасность

### Базовая аутентификация

Добавьте middleware в `mcp_server.py`:

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = "admin"
    correct_password = "secret"
    if credentials.username != correct_username or credentials.password != correct_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    return credentials.username

# Применяем ко всем эндпоинтам
@app.post("/tools/{tool_name}", dependencies=[Depends(verify_credentials)])
async def invoke_tool_by_path(...):
    ...
```

### API Key аутентификация

```python
from fastapi import Header, HTTPException

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != "your-secret-api-key":
        raise HTTPException(status_code=403, detail="Invalid API Key")

@app.post("/tools/{tool_name}", dependencies=[Depends(verify_api_key)])
async def invoke_tool_by_path(...):
    ...
```

## 🚢 Деплой

### Docker

Создайте `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Запуск:

```bash
docker build -t mcp-server .
docker run -d -p 8000:8000 --name mcp-server mcp-server
```

### Systemd service

Создайте `/etc/systemd/system/mcp-server.service`:

```ini
[Unit]
Description=MCP Server for KB Tools
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/rag
ExecStart=/opt/rag/.venv/bin/uvicorn mcp_server:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

## 📝 Логирование

Логи сервера сохраняются в `logs/mcp_server.log`.

Настройка логирования в `mcp_server.py`:

```python
# Уже настроено через logging_config.py
logger = setup_logging("mcp_server")

# Уровень логирования можно изменить в logging_config.py
```

## 🧪 Тестирование

### Простой тест

```bash
# Health check
curl http://localhost:8000/health

# Список инструментов
curl http://localhost:8000/tools | jq '.count'

# Простой поиск
curl -X POST http://localhost:8000/tools/semantic_search \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "top_k": 1}' | jq '.success'
```

### Pytest

Создайте `test_mcp_server.py`:

```python
import pytest
from fastapi.testclient import TestClient
from mcp_server import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_list_tools():
    response = client.get("/tools")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] > 0

def test_semantic_search():
    response = client.post(
        "/tools/semantic_search",
        json={"query": "test", "top_k": 5}
    )
    assert response.status_code == 200
   assert response.json()["success"] == True
```

## 📚 Документация API

После запуска сервера:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **OpenAPI Schema:** http://localhost:8000/openapi.json

## 🎯 Следующие шаги

1. **Аутентификация** - добавить API keys или OAuth2
2. **Rate limiting** - ограничение запросов
3. **Кэширование** - Redis для кэширования результатов
4. **Monitoring** - Prometheus метрики
5. **WebSockets** - для streaming результатов
6. **Batching** - пакетная обработка запросов

---

**Готово к использованию! 🚀**

Запустите сервер и откройте http://localhost:8000/docs для интерактивной документации.

