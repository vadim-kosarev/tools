"""MCP Server — stdio транспорт для Continue.dev.

Читает JSON-RPC сообщения из stdin построчно, пишет ответы в stdout.
Все логи → stderr (stdout зарезервирован для протокола).

Continue.dev config.yaml:
    name: KB Tools MCP
    version: 0.0.1
    schema: v1
    mcpServers:
      - name: kb-tools
        command: C:/dev/github.com/vadim-kosarev/tools.0/RAG/mcp_stdio.bat
        args: []
        env: {}
"""

import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# ────────────────────────────────────────────────────────────────────────────
# ВАЖНО: все логи → stderr, stdout только для JSON-RPC ответов
# ────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_stdio")

# ────────────────────────────────────────────────────────────────────────────
# Инициализация инструментов
# ────────────────────────────────────────────────────────────────────────────

_tools_map: Dict[str, Any] = {}


def _init_tools() -> None:
    global _tools_map

    # Добавляем папку RAG в sys.path (на случай запуска из другой директории)
    rag_dir = Path(__file__).parent
    if str(rag_dir) not in sys.path:
        sys.path.insert(0, str(rag_dir))

    from rag_chat import Settings
    from clickhouse_store import ClickHouseStoreSettings, build_store
    from kb_tools import create_kb_tools
    from langchain_ollama import OllamaEmbeddings

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

    vs = build_store(cfg=cfg, embedding=embedding, force_reindex=False)
    tools = create_kb_tools(
        vectorstore=vs,
        knowledge_dir=Path(settings.knowledge_dir),
        semantic_top_k=settings.retriever_top_k,
        exact_limit=30,
        regex_max_results=50,
    )
    _tools_map = {t.name: t for t in tools}
    logger.info(f"Зарегистрировано {len(_tools_map)} инструментов")


# ────────────────────────────────────────────────────────────────────────────
# МCP helpers
# ────────────────────────────────────────────────────────────────────────────

def _tools_schema() -> list[dict]:
    result = []
    for name, tool in _tools_map.items():
        schema: dict = {}
        if hasattr(tool, "args_schema") and tool.args_schema:
            schema = tool.args_schema.model_json_schema()
            schema.pop("title", None)
        result.append({
            "name": name,
            "description": tool.description or name,
            "inputSchema": schema or {"type": "object", "properties": {}},
        })
    return result


def _call_tool(name: str, arguments: dict) -> str:
    if name not in _tools_map:
        raise ValueError(f"Инструмент '{name}' не найден")
    result = _tools_map[name].invoke(arguments)
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


def _handle(body: dict) -> Optional[dict]:
    """Обрабатывает JSON-RPC запрос. Возвращает None для уведомлений."""
    method = body.get("method", "")
    params = body.get("params") or {}
    req_id = body.get("id")

    logger.info(f"← {method} (id={req_id})")

    if req_id is None:   # уведомление — ответ не нужен
        return None

    try:
        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo":    {"name": "kb-tools", "version": "1.0.0"},
            })

        elif method == "tools/list":
            return _ok(req_id, {"tools": _tools_schema()})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                text = _call_tool(tool_name, tool_args)
                return _ok(req_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                })
            except Exception as exc:
                return _ok(req_id, {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                })

        elif method == "ping":
            return _ok(req_id, {})

        else:
            return _err(req_id, -32601, f"Метод не найден: {method}")

    except Exception as exc:
        logger.error(f"Ошибка {method}: {exc}\n{traceback.format_exc()}")
        return _err(req_id, -32603, f"Internal error: {exc}")


# ────────────────────────────────────────────────────────────────────────────
# Main loop
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("MCP stdio сервер запускается…")
    _init_tools()
    logger.info("MCP stdio сервер готов. Ожидание сообщений…")

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            body = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = _err(None, -32700, f"Parse error: {exc}")
            print(json.dumps(resp, ensure_ascii=False), flush=True)
            continue

        response = _handle(body)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
            logger.info(f"→ id={response.get('id')}")


if __name__ == "__main__":
    main()

