# Параметры
$iperfServer = "vkosarev.name"   # IP или хост iperf3-сервера
$port = 5201
$outputFile = "C:\monitoring\iperf3.prom"

# Убедиться, что папка есть
if (-not (Test-Path "C:\monitoring")) {
    New-Item -Path "C:\monitoring" -ItemType Directory | Out-Null
}

# Выполнение теста (10 секунд)
$iperfOutput = & .\iperf3.exe -c $iperfServer -p $port -J 2>&1

# Преобразуем в JSON-объект
try {
    $json = $iperfOutput | ConvertFrom-Json
} catch {
    Write-Host "Не удалось распарсить вывод iperf3"
    exit 1
}

# Выгружаем скорость в байтах/сек
$upload_bps = $json.end.sum_sent.bytes_per_second
$download_bps = $json.end.sum_received.bytes_per_second

$timestamp = [int][double]::Parse((Get-Date -UFormat %s))

# Извлекаем метрики
$metrics = @()

# === Базовая инфа ===
$metrics += "# HELP iperf3_start_time_utc_seconds Test start timestamp (UTC)"
$metrics += "# TYPE iperf3_start_time_utc_seconds gauge"
$metrics += "iperf3_start_time_utc_seconds $($json.start.timestamp.timesecs)"

# === CPU ===
$cpu = $json.end.cpu_utilization_percent
foreach ($field in $cpu.PSObject.Properties) {
    $metrics += "# HELP iperf3_cpu_$($field.Name) CPU usage percent"
    $metrics += "# TYPE iperf3_cpu_$($field.Name) gauge"
    $metrics += "iperf3_cpu_$($field.Name) $($field.Value)"
}

# === Суммарная передача ===
foreach ($dir in @("sum_sent", "sum_received")) {
    $obj = $json.end.$dir
    $metrics += "# HELP iperf3_${dir}_bytes Bytes transferred"
    $metrics += "# TYPE iperf3_${dir}_bytes gauge"
    $metrics += "iperf3_${dir}_bytes $($obj.bytes)"

    $metrics += "# HELP iperf3_${dir}_bps Bits per second"
    $metrics += "# TYPE iperf3_${dir}_bps gauge"
    $metrics += "iperf3_${dir}_bps $($obj.bits_per_second)"
}

# === Протокол и параметры теста ===
$start = $json.start.test_start
foreach ($param in $start.PSObject.Properties) {
    $name = "iperf3_param_$($param.Name)"
    $metrics += "# HELP $name Test parameter $($param.Name)"
    $metrics += "# TYPE $name gauge"
    $metrics += "$name $($param.Value)"
}

# === TCP congestion control ===
$cc = $json.end.receiver_tcp_congestion
$metrics += "# HELP iperf3_tcp_congestion_control TCP congestion control algorithm"
$metrics += "# TYPE iperf3_tcp_congestion_control gauge"
$metrics += "iperf3_tcp_congestion_control{algo=`"$cc`"} 1"

# === Последний интервал ===
$lastSum = $json.intervals[-1].sum
$metrics += "# HELP iperf3_last_interval_bytes Bytes in last interval"
$metrics += "# TYPE iperf3_last_interval_bytes gauge"
$metrics += "iperf3_last_interval_bytes $($lastSum.bytes)"

$metrics += "# HELP iperf3_last_interval_bps Bits per second in last interval"
$metrics += "# TYPE iperf3_last_interval_bps gauge"
$metrics += "iperf3_last_interval_bps $($lastSum.bits_per_second)"

# === Сохраняем ===
#Set-Content -Path $outputFile -Value ($metrics -join "`n") -Encoding UTF8
$metrics -join "`n" | Out-File -FilePath $outputFile -Encoding utf8 -Force
