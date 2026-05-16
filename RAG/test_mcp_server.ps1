# Скрипт тестирования MCP сервера
# Использование: .\test_mcp_server.ps1

$baseUrl = "http://localhost:8000"

Write-Host "🧪 Тестирование MCP Server..." -ForegroundColor Green
Write-Host ""

# Функция для красивого вывода
function Test-Endpoint {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Url,
        [object]$Body = $null
    )

    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  TEST: $Name" -ForegroundColor Yellow
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  $Method $Url" -ForegroundColor Gray

    try {
        if ($Body) {
            $jsonBody = $Body | ConvertTo-Json -Depth 10
            Write-Host "  Body: $jsonBody" -ForegroundColor Gray
            $response = Invoke-RestMethod -Method $Method -Uri $Url -ContentType "application/json" -Body $jsonBody -ErrorAction Stop
        } else {
            $response = Invoke-RestMethod -Method $Method -Uri $Url -ErrorAction Stop
        }

        Write-Host "  ✓ SUCCESS" -ForegroundColor Green
        Write-Host "  Response:" -ForegroundColor White
        $response | ConvertTo-Json -Depth 5 | Write-Host -ForegroundColor White
        Write-Host ""
        return $true
    }
    catch {
        Write-Host "  ✗ FAILED" -ForegroundColor Red
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        return $false
    }
}

# Счетчики
$passed = 0
$failed = 0

# 1. Health check
if (Test-Endpoint -Name "Health Check" -Method "GET" -Url "$baseUrl/health") {
    $passed++
} else {
    $failed++
}

# 2. List tools
if (Test-Endpoint -Name "List All Tools" -Method "GET" -Url "$baseUrl/tools") {
    $passed++
} else {
    $failed++
}

# 3. Get tool schema
if (Test-Endpoint -Name "Get Tool Schema" -Method "GET" -Url "$baseUrl/tools/semantic_search") {
    $passed++
} else {
    $failed++
}

# 4. Semantic search
$searchBody = @{
    query = "СУБД базы данных"
    top_k = 3
}
if (Test-Endpoint -Name "Semantic Search" -Method "POST" -Url "$baseUrl/tools/semantic_search" -Body $searchBody) {
    $passed++
} else {
    $failed++
}

# 5. Exact search
$exactBody = @{
    substring = "PostgreSQL"
    limit = 5
}
if (Test-Endpoint -Name "Exact Search" -Method "POST" -Url "$baseUrl/tools/exact_search" -Body $exactBody) {
    $passed++
} else {
    $failed++
}

# 6. Find abbreviation
$abbrBody = @{
    abbreviation = "КЦОИ"
}
if (Test-Endpoint -Name "Find Abbreviation" -Method "POST" -Url "$baseUrl/tools/find_abbreviation_expansion" -Body $abbrBody) {
    $passed++
} else {
    $failed++
}

# 7. List sources
$listBody = @{}
if (Test-Endpoint -Name "List Sources" -Method "POST" -Url "$baseUrl/tools/list_sources" -Body $listBody) {
    $passed++
} else {
    $failed++
}

# 8. MCP invoke endpoint
$mcpBody = @{
    tool = "semantic_search"
    arguments = @{
        query = "тест"
        top_k = 2
    }
}
if (Test-Endpoint -Name "MCP Invoke Endpoint" -Method "POST" -Url "$baseUrl/invoke" -Body $mcpBody) {
    $passed++
} else {
    $failed++
}

# Итоги
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ✓ Passed: $passed" -ForegroundColor Green
if ($failed -gt 0) {
    Write-Host "  ✗ Failed: $failed" -ForegroundColor Red
} else {
    Write-Host "  ✗ Failed: $failed" -ForegroundColor Gray
}
Write-Host "  Total:  $($passed + $failed)" -ForegroundColor White
Write-Host ""

if ($failed -eq 0) {
    Write-Host "🎉 ВСЕ ТЕСТЫ ПРОШЛИ УСПЕШНО!" -ForegroundColor Green
} else {
    Write-Host "⚠️  НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ" -ForegroundColor Yellow
}
Write-Host ""

