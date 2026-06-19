"""MCP-сервер (Streamable HTTP) для инструментов базы знаний.

Использует официальный MCP SDK (пакет ``mcp``) с транспортом Streamable HTTP.
Все инструменты из ``kb_tools.create_kb_tools()`` автоматически публикуются как
MCP tools: их JSON-схемы берутся из ``args_schema`` LangChain-инструментов.

Эндпоинт MCP:  http://<host>:<port>/mcp
Проверка статуса (обычный GET):  http://<host>:<port>/health

Запуск через uv (рекомендуется, используя уже наполненный .venv проекта):
    uv run --active kb_tools_mcp_http.py
    uv run --active kb_tools_mcp_http.py --host 0.0.0.0 --port 8000

Запуск напрямую интерпретатором venv:
    python kb_tools_mcp_http.py

Конфиг MCP-клиента (Streamable HTTP):
    {
      "mcpServers": {
        "kb-tools": { "url": "http://localhost:8000/mcp" }
      }
    }
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

# Папка RAG в sys.path — чтобы локальные модули (kb_tools, clickhouse_store,
# rag_chat) импортировались при запуске из любой директории.
_RAG_DIR = Path(__file__).parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

# Кириллица в логах PowerShell без иероглифов.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("kb_tools_mcp_http")

# Реестр инструментов {tool_name: BaseTool}, заполняется в _init_tools().
_tools_map: dict[str, Any] = {}


def _init_tools() -> None:
    """Инициализирует vectorstore и регистрирует LangChain-инструменты."""
    global _tools_map
    if _tools_map:
        return

    from langchain_ollama import OllamaEmbeddings

    from clickhouse_store import ClickHouseStoreSettings, build_store
    from kb_tools import create_kb_tools
    from rag_chat import Settings

    settings = Settings()

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
    vectorstore = build_store(cfg=cfg, embedding=embedding, force_reindex=False)

    tools = create_kb_tools(
        vectorstore=vectorstore,
        semantic_top_k=settings.retriever_top_k,
        exact_limit=30,
        regex_max_results=50,
    )
    _tools_map = {t.name: t for t in tools}
    logger.info("Зарегистрировано %d инструментов: %s", len(_tools_map), list(_tools_map))


def _tool_input_schema(tool: Any) -> dict:
    """JSON-схема параметров инструмента в формате MCP inputSchema."""
    schema: dict = {}
    if getattr(tool, "args_schema", None):
        schema = tool.args_schema.model_json_schema()
        schema.pop("title", None)
    return schema or {"type": "object", "properties": {}}


def _result_to_text(result: Any) -> str:
    """Приводит результат инструмента к строке для MCP TextContent."""
    if isinstance(result, str):
        return result
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    try:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(result)


def build_mcp_server() -> Server:
    """Создаёт low-level MCP-сервер с обработчиками list_tools / call_tool."""
    server: Server = Server("kb-tools")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=tool.description or name,
                inputSchema=_tool_input_schema(tool),
            )
            for name, tool in _tools_map.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name not in _tools_map:
            raise ValueError(f"Инструмент '{name}' не найден")
        tool = _tools_map[name]
        # Логируем имя инструмента и аргументы, которыми его зовёт клиент —
        # видно, что именно запрашивает агент (полезно при «ничего не найдено»).
        logger.info("call_tool: %s args=%s", name, json.dumps(arguments, ensure_ascii=False))
        # tool.invoke синхронный и может быть медленным (ClickHouse/Ollama) —
        # выполняем в пуле потоков, чтобы не блокировать event loop.
        result = await anyio.to_thread.run_sync(lambda: tool.invoke(arguments))
        text = _result_to_text(result)
        # Читаемый результат (с русскими буквами) — байтовый дамп SSE-транспорта
        # их экранирует, поэтому логируем сами на уровне DEBUG.
        logger.debug("result %s: %s", name, text)
        return [types.TextContent(type="text", text=text)]

    return server


def build_app(json_response: bool = False) -> Starlette:
    """Собирает Starlette-приложение с MCP-эндпоинтом /mcp и /health."""
    server = build_mcp_server()

    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=json_response,
        stateless=True,
        # Локальный доверенный сервер — отключаем DNS-rebinding защиту,
        # иначе при пустом allowlist все запросы отклоняются.
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "transport": "streamable-http",
                "mcp_endpoint": "/mcp",
                "tools_count": len(_tools_map),
                "tools": list(_tools_map),
            }
        )

    _CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    async def handle_call(request: Request) -> JSONResponse:
        """REST-эндпоинт для HTML-клиента: POST /call {"tool": "...", "args": {...}}"""
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=_CORS_HEADERS)
        data = await request.json()
        tool_name = data.get("tool")
        args = data.get("args", {})
        if tool_name not in _tools_map:
            return JSONResponse(
                {"ok": False, "error": f"Tool '{tool_name}' not found"},
                status_code=404, headers=_CORS_HEADERS,
            )
        tool = _tools_map[tool_name]
        logger.info("REST /call: %s args=%s", tool_name, json.dumps(args, ensure_ascii=False))
        try:
            result = await anyio.to_thread.run_sync(lambda: tool.invoke(args))
            text = _result_to_text(result)
            try:
                result_data = json.loads(text)
            except Exception:
                result_data = text
            return JSONResponse({"ok": True, "result": result_data}, headers=_CORS_HEADERS)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)},
                status_code=500, headers=_CORS_HEADERS,
            )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        _init_tools()
        async with session_manager.run():
            logger.info("MCP-сервер готов. Эндпоинт: /mcp, статус: /health, REST: /call")
            yield
            logger.info("MCP-сервер остановлен")

    return Starlette(
        debug=False,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/call", handle_call, methods=["POST", "OPTIONS"]),
            Mount("/mcp", app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP Streamable HTTP сервер для инструментов базы знаний",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HTTP_HOST", "0.0.0.0"),
        help="Адрес привязки (по умолчанию 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_HTTP_PORT", "8000")),
        help="Порт (по умолчанию 8000)",
    )
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Отдавать ответы как application/json вместо SSE (удобно для отладки curl)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.getenv("MCP_HTTP_DEBUG", "").lower() in ("1", "true", "yes"),
        help="Подробное логирование (level=DEBUG): аргументы инструментов, SQL и пр.",
    )
    args = parser.parse_args()

    from logging_config import setup_logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    setup_logging("kb_tools_mcp_http", level=log_level)
    if args.debug:
        logging.getLogger("kb_tools").setLevel(logging.DEBUG)
        # Заглушаем особо шумные DEBUG-логгеры транспорта на всякий случай.
        logging.getLogger("sse_starlette").setLevel(logging.INFO)
        logging.getLogger("mcp.server.streamable_http").setLevel(logging.INFO)
        logger.debug("DEBUG-логирование включено (только модули kb_tools*)")

    app = build_app(json_response=args.json_response)

    logger.info("Запуск MCP-сервера на http://%s:%d/mcp", args.host, args.port)
    # timeout_graceful_shutdown: по Ctrl+C не ждём бесконечно закрытия
    # keep-alive/SSE-соединений клиента — через 5 с рвём принудительно.
    uvicorn.run(app, host=args.host, port=args.port, timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
