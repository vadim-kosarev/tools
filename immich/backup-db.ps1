$ErrorActionPreference = "Stop"

$envFile = Join-Path $PSScriptRoot "docker\.env"
if (-not (Test-Path $envFile)) {
    Write-Error ".env not found: $envFile"
    exit 1
}

$env = @{}
Get-Content $envFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.+?)\s*$') {
        $env[$Matches[1]] = $Matches[2]
    }
}

$uploadLocation = $env["UPLOAD_LOCATION"]
if (-not $uploadLocation) {
    Write-Error "UPLOAD_LOCATION not defined in .env"
    exit 1
}

$Source = Join-Path $uploadLocation "photos\backups"

$Destination = $env["BACKUP_DESTINATION"]
if (-not $Destination) {
    Write-Error "BACKUP_DESTINATION not defined in .env"
    exit 1
}

if (-not (Test-Path $Source)) {
    Write-Error "Source directory not found: $Source"
    exit 1
}

if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}

$latest = Get-ChildItem -Path $Source -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not $latest) {
    Write-Error "No backup files found in $Source"
    exit 1
}

$destFile = Join-Path $Destination $latest.Name

if (Test-Path $destFile) {
    $destSize = (Get-Item $destFile).Length
    if ($destSize -eq $latest.Length) {
        Write-Host "Already copied (same size): $($latest.Name)"
        exit 0
    }
}

Copy-Item -Path $latest.FullName -Destination $destFile -Force
Write-Host "Copied: $($latest.Name) -> $Destination"
