# Запуск MCP-сервера (Streamable HTTP) для Knowledge Base Tools через uv.
# Использование:
#   .\start_kb_tools_mcp_http.ps1                  # 0.0.0.0:8000
#   .\start_kb_tools_mcp_http.ps1 -Port 8765       # другой порт
#   .\start_kb_tools_mcp_http.ps1 -BindHost 127.0.0.1 -Port 8000
#   .\start_kb_tools_mcp_http.ps1 -DebugLog        # подробные логи (level=DEBUG)
param(
    [int]$Port = 8000,
    [string]$BindHost = "0.0.0.0",
    [switch]$DebugLog
)

$ErrorActionPreference = "Stop"

Write-Host "Запуск MCP Server (Streamable HTTP) для Knowledge Base Tools..." -ForegroundColor Green

# Поиск виртуального окружения: текущая папка или родительская.
$venvActivate = $null
foreach ($candidate in @(".\.venv\Scripts\Activate.ps1", "..\.venv\Scripts\Activate.ps1")) {
    if (Test-Path $candidate) { $venvActivate = $candidate; break }
}
if (-not $venvActivate) {
    Write-Host "Виртуальное окружение не найдено (.venv в текущей или родительской папке)." -ForegroundColor Red
    exit 1
}
Write-Host "Активация venv: $venvActivate" -ForegroundColor Yellow
. $venvActivate

# Проверка пакета mcp; при отсутствии — установка зависимостей MCP.
$mcpInstalled = python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('mcp') else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Пакет mcp не найден. Установка requirements_mcp.txt..." -ForegroundColor Yellow
    pip install -r requirements_mcp.txt
}

if (-not (Test-Path ".env")) {
    Write-Host "Файл .env не найден. Скопируйте .env.example и настройте параметры." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  MCP Server (Streamable HTTP)" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  MCP endpoint:  http://localhost:$Port/mcp" -ForegroundColor White
Write-Host "  Health:        http://localhost:$Port/health" -ForegroundColor White
Write-Host "  Остановка:     Ctrl+C" -ForegroundColor Yellow
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

# Запуск через uv в активном venv (--active использует активированное окружение,
# --no-project отключает поиск pyproject.toml вверх по дереву).
$serverArgs = @("--host", $BindHost, "--port", $Port)
if ($DebugLog) { $serverArgs += "--debug" }
$cmdLine = "uv run --active --no-project kb_tools_mcp_http.py $($serverArgs -join ' ')"
Write-Host "Команда запуска:" -ForegroundColor Yellow
Write-Host "  $cmdLine" -ForegroundColor White
Write-Host ""
uv run --active --no-project kb_tools_mcp_http.py @serverArgs
