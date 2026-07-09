<#
.SYNOPSIS
  Failover Immich standby (starlight) -> primary. Запускать ТОЛЬКО когда brightsky реально
  недоступен: промоушен необратим без пересидинга standby с нуля (pg_basebackup -R).

.PARAMETER Force
  Пропустить интерактивное подтверждение.
#>
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$composeDir = "C:\dev\github.com\vadim-kosarev\tools\immich\docker"
$composeFiles = @("-f", "docker-compose.prod.yml", "-f", "docker-compose.starlight.override.yml")
$envFile = @("--env-file", ".env.starlight")

if (-not $Force) {
    $answer = Read-Host "Промоутнуть standby на starlight в primary и поднять полный стек? (yes/no)"
    if ($answer -ne "yes") {
        Write-Host "Отменено."
        exit 1
    }
}

Set-Location $composeDir

Write-Host "==> 1/4: pg_promote() standby -> primary"
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_promote();"

Write-Host "==> 2/4: pull актуального образа immich-server (IMMICH_VERSION=release - плавающий тег)"
docker compose @composeFiles @envFile pull immich-server immich-machine-learning

Write-Host "==> 3/4: поднимаем полный стек (redis, immich-server, ML, frpc)"
docker compose @composeFiles @envFile up -d redis immich-server immich-machine-learning frpc

Write-Host "==> 4/4: готово. Проверка:"
Write-Host "    - локально:  http://localhost:2283"
Write-Host "    - снаружи:   https://vkosarev.name:7601 (после того как frpc перехватит регистрацию у brightsky)"
docker compose @composeFiles @envFile ps
