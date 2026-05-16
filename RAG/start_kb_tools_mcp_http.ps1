# Скрипт запуска MCP сервера
# Использование: .\start_kb_tools_mcp_http.ps1

Write-Host "🚀 Запуск MCP Server для Knowledge Base Tools..." -ForegroundColor Green

# Проверка виртуального окружения
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "✓ Активация виртуального окружения..." -ForegroundColor Yellow
    .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "⚠ Виртуальное окружение не найдено. Создайте его: python -m venv .venv" -ForegroundColor Red
    exit 1
}

# Проверка установки зависимостей
Write-Host "✓ Проверка зависимостей..." -ForegroundColor Yellow
$fastapi = pip show fastapi 2>$null
$uvicorn = pip show uvicorn 2>$null

if (-not $fastapi -or -not $uvicorn) {
    Write-Host "⚠ Не все зависимости установлены. Установка..." -ForegroundColor Yellow
    pip install -r requirements_mcp.txt
}

# Проверка .env файла
if (-not (Test-Path ".env")) {
    Write-Host "⚠ Файл .env не найден. Скопируйте .env.example и настройте параметры." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  MCP Server for Knowledge Base Tools" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""
Write-Host "  📍 URL:          http://localhost:8000" -ForegroundColor White
Write-Host "  📖 Docs (Swagger): http://localhost:8000/docs" -ForegroundColor White
Write-Host "  📚 ReDoc:        http://localhost:8000/redoc" -ForegroundColor White
Write-Host "  🏥 Health:       http://localhost:8000/health" -ForegroundColor White
Write-Host ""
Write-Host "  🛑 Остановка:    Ctrl+C" -ForegroundColor Yellow
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""

# Запуск сервера
uvicorn kb_tools_mcp_http:app --host 0.0.0.0 --port 8000 --reload

