# Тест MCP-сервера (Streamable HTTP).
# Проверяет GET /health и полный MCP-цикл (initialize -> tools/list -> tools/call)
# через клиент официального MCP SDK.
#
# Использование:
#   .\test_mcp_server.ps1                 # localhost:8000
#   .\test_mcp_server.ps1 -Port 8765
param(
    [int]$Port = 8000,
    [string]$BindHost = "localhost"
)

$base = "http://${BindHost}:$Port"
Write-Host "Тестирование MCP-сервера: $base" -ForegroundColor Green

# Поиск интерпретатора venv (корневой .venv проекта или локальный).
$venvPy = $null
foreach ($c in @("$PSScriptRoot\..\.venv\Scripts\python.exe", "$PSScriptRoot\.venv\Scripts\python.exe")) {
    if (Test-Path $c) { $venvPy = $c; break }
}
if (-not $venvPy) {
    Write-Host "Не найден python в .venv (корневой или локальный)." -ForegroundColor Red
    exit 1
}

# 1) Health check (обычный GET).
Write-Host "`n[1] GET /health" -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  OK: status=$($health.status), tools_count=$($health.tools_count)" -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Сервер запущен? .\start_kb_tools_mcp_http.ps1" -ForegroundColor Red
    exit 1
}

# 2) MCP handshake + tools/list + tools/call через клиент SDK (исходник ASCII,
#    поисковый термин передаётся аргументом, чтобы избежать проблем с кодировкой).
Write-Host "`n[2] MCP initialize -> tools/list -> tools/call" -ForegroundColor Yellow
$pySrc = @'
import asyncio, sys
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

url, term = sys.argv[1], sys.argv[2]

async def main():
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("  server:", init.serverInfo.name, init.serverInfo.version)
            tools = await session.list_tools()
            print("  tools:", len(tools.tools))
            res = await session.call_tool("exact_search", {"substring": term})
            text = "".join(c.text for c in res.content if getattr(c, "type", None) == "text")
            print("  call isError:", res.isError)
            print("  call head:", text[:120].replace("\n", " "))
            return 1 if res.isError else 0

rc = asyncio.run(main())
sys.exit(rc)
'@
$pySrc | & $venvPy - "$base/mcp" "СКДПУ"
$rc = $LASTEXITCODE

Write-Host ""
if ($rc -eq 0) {
    Write-Host "ВСЕ ПРОВЕРКИ ПРОШЛИ" -ForegroundColor Green
} else {
    Write-Host "ПРОВЕРКА MCP НЕ ПРОШЛА (код $rc)" -ForegroundColor Red
}
exit $rc
