"""MCP Server — SSE транспорт (без mcp пакета).

Реализует протокол MCP (Model Context Protocol) поверх SSE транспорта вручную:
  GET  /sse                → непрерывный SSE поток (Continue.dev подключается сюда)
  POST /messages?sessionId → JSON-RPC сообщения от клиента

Все инструменты базы знаний автоматически регистрируются как MCP tools.

Запуск:
    uvicorn kb_tools_mcp_http:app --host 0.0.0.0 --port 8000 --reload
    # или
    python kb_tools_mcp_http.py

Continue.dev (.continue/config.json):
    {
      "experimental": {
        "modelContextProtocolServers": [
          {
            "transport": { "type": "sse", "url": "http://localhost:8000/sse" }
          }
        ]
      }
    }

REST API (дополнительно, для отладки):
    GET  /health            → статус сервера
    GET  /tools             → список инструментов
    POST /tools/{name}      → прямой вызов инструмента
"""

import asyncio
import json
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from clickhouse_store import ClickHouseStoreSettings, build_store
from kb_tools import create_kb_tools
from rag_chat import Settings

# ---------------------------------------------------------------------------
# Настройка
# ---------------------------------------------------------------------------

settings = Settings()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("kb_tools_mcp_http")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_tools()
    yield


app = FastAPI(
    title="KB Tools MCP Server",
    description="MCP протокол (SSE транспорт) для инструментов базы знаний",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Глобальное состояние
# ---------------------------------------------------------------------------

_tools_map: Dict[str, Any] = {}          # {tool_name: BaseTool}
_sessions: Dict[str, asyncio.Queue] = {} # {session_id: Queue}
_vectorstore = None

# ---------------------------------------------------------------------------
# Инициализация инструментов
# ---------------------------------------------------------------------------

def init_tools() -> None:
    """Инициализирует vectorstore и регистрирует LangChain tools."""
    global _tools_map, _vectorstore

    from langchain_ollama import OllamaEmbeddings

    cfg = ClickHouseStoreSettings(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_username,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        table=settings.clickhouse_table,
    )
    embedding = OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )

    _vectorstore = build_store(cfg=cfg, embedding=embedding, force_reindex=False)

    tools_list = create_kb_tools(
        vectorstore=_vectorstore,
        knowledge_dir=Path(settings.knowledge_dir),
        semantic_top_k=settings.retriever_top_k,
        exact_limit=30,
        regex_max_results=50,
    )
    _tools_map = {t.name: t for t in tools_list}
    logger.info(f"Зарегистрировано {len(_tools_map)} инструментов: {list(_tools_map)}")


# ---------------------------------------------------------------------------
# MCP вспомогательные функции
# ---------------------------------------------------------------------------

def _mcp_tools_list() -> list[dict]:
    """Возвращает список инструментов в формате MCP (inputSchema)."""
    result = []
    for name, tool in _tools_map.items():
        schema: dict = {}
        if hasattr(tool, "args_schema") and tool.args_schema:
            schema = tool.args_schema.model_json_schema()
            schema.pop("title", None)   # MCP не требует title
        result.append({
            "name": name,
            "description": tool.description or name,
            "inputSchema": schema or {"type": "object", "properties": {}},
        })
    return result


def _invoke_tool(name: str, arguments: dict) -> str:
    """Вызывает инструмент и возвращает результат как строку."""
    if name not in _tools_map:
        raise ValueError(f"Инструмент '{name}' не найден")

    tool = _tools_map[name]
    result = tool.invoke(arguments)

    if isinstance(result, str):
        return result
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    try:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(result)


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _handle_jsonrpc(body: dict) -> Optional[dict]:
    """Обрабатывает JSON-RPC запрос, возвращает ответ или None (для уведомлений)."""
    method  = body.get("method", "")
    params  = body.get("params") or {}
    req_id  = body.get("id")        # None для уведомлений

    logger.info(f"← {method} (id={req_id})")

    # Уведомления (нет id) → ответа не требуют
    if req_id is None:
        return None

    try:
        # ── Handshake ─────────────────────────────────────────────────────
        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo":    {"name": "kb-tools", "version": "1.0.0"},
            })

        # ── Список инструментов ───────────────────────────────────────────
        elif method == "tools/list":
            return _ok(req_id, {"tools": _mcp_tools_list()})

        # ── Вызов инструмента ─────────────────────────────────────────────
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                text = _invoke_tool(tool_name, tool_args)
                return _ok(req_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                })
            except Exception as exc:
                return _ok(req_id, {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                })

        # ── Ping ──────────────────────────────────────────────────────────
        elif method == "ping":
            return _ok(req_id, {})

        else:
            return _err(req_id, -32601, f"Метод не найден: {method}")

    except Exception as exc:
        logger.error(f"Ошибка при обработке {method}: {exc}\n{traceback.format_exc()}")
        return _err(req_id, -32603, f"Internal error: {exc}")

# ---------------------------------------------------------------------------
# SSE Transport  (GET /sse  +  POST /messages)
# ---------------------------------------------------------------------------

@app.get("/sse")
async def sse_endpoint(request: Request):
    """
    SSE-поток для MCP клиента (Continue.dev).

    Порядок работы:
    1. Клиент подключается GET /sse
    2. Сервер отправляет event:endpoint с адресом для POST
    3. Клиент посылает JSON-RPC сообщения в POST /messages?sessionId=...
    4. Сервер отправляет ответы обратно через SSE
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sessions[session_id] = queue
    logger.info(f"SSE сессия открыта: {session_id[:8]}…")

    async def generator():
        # Первое событие — адрес для отправки сообщений
        yield f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                # Отправляем накопленные ответы
                try:
                    msg = queue.get_nowait()
                    payload = json.dumps(msg, ensure_ascii=False)
                    yield f"event: message\ndata: {payload}\n\n"
                    logger.info(f"→ [sse:{session_id[:8]}] id={msg.get('id')} method_result")
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)
        finally:
            _sessions.pop(session_id, None)
            logger.info(f"SSE сессия закрыта: {session_id[:8]}…")

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.post("/messages")
async def messages_endpoint(request: Request, sessionId: str = Query(...)):
    """Принимает JSON-RPC сообщения от клиента и отвечает через SSE."""
    queue = _sessions.get(sessionId)
    if not queue:
        return JSONResponse({"error": f"Сессия '{sessionId}' не найдена"}, status_code=404)

    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": f"Некорректный JSON: {exc}"}, status_code=400)

    response = await _handle_jsonrpc(body)
    if response is not None:
        await queue.put(response)

    return JSONResponse({}, status_code=202)

# ---------------------------------------------------------------------------
# REST API (бонус — для отладки через curl / браузер)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "tools_count": len(_tools_map),
        "sessions_active": len(_sessions),
        "mcp_endpoint": "GET /sse",
        "messages_endpoint": "POST /messages?sessionId=...",
    }


@app.get("/tools")
async def list_tools_rest():
    """Список инструментов в читаемом формате (REST)."""
    return {"tools": _mcp_tools_list(), "count": len(_tools_map)}


@app.post("/tools/{tool_name}")
async def invoke_tool_rest(tool_name: str, request: Request):
    """Прямой вызов инструмента (REST, для отладки)."""
    if tool_name not in _tools_map:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    try:
        args = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    try:
        return {"tool": tool_name, "success": True, "result": _invoke_tool(tool_name, args)}
    except Exception as exc:
        return {"tool": tool_name, "success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("kb_tools_mcp_http:app", host="0.0.0.0", port=8000, reload=True)
