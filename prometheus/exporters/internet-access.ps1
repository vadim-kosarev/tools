<#
.SYNOPSIS
��������� ��� ����� ���������� ��������-���������� �� 10 ������

.DESCRIPTION
���� ������ ��������� ����������� ��������� � ������� 10 ������ � ������������� ������� � ������� Prometheus
#>

# ��������� ��������
$TestHost = "8.8.8.8"  # Google DNS ��� �������� ����
$TestTimeout = 1000    # ������� � �������������
$Port = 53             # DNS ����
$Duration = 10         # ������������ ����� ���������� � ��������
$OutputFile = "C:\monitoring\internet_metrics.prom"  # ���� ��� ������ ������

# ������� ���������� ��� ������, ���� � ���
if (-not (Test-Path -Path (Split-Path -Path $OutputFile -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path -Path $OutputFile -Parent) -Force
}

# ������� �������� ��������-����������
function Test-InternetConnection {
    try {
        $tcpClient = New-Object System.Net.Sockets.TcpClient
        $asyncResult = $tcpClient.BeginConnect($TestHost, $Port, $null, $null)
        $waitResult = $asyncResult.AsyncWaitHandle.WaitOne($TestTimeout, $false)
        
        if ($waitResult -and $tcpClient.Connected) {
            $tcpClient.EndConnect($asyncResult)
            $tcpClient.Close()
            return $true
        } else {
            $tcpClient.Close()
            return $false
        }
    } catch {
        return $false
    }
}

# ������� ��������� ������� ������
function Measure-ConnectionTime {
    try {
        $tcpClient = New-Object System.Net.Sockets.TcpClient
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $asyncResult = $tcpClient.BeginConnect($TestHost, $Port, $null, $null)
        $waitResult = $asyncResult.AsyncWaitHandle.WaitOne($TestTimeout, $false)
        
        if ($waitResult -and $tcpClient.Connected) {
            $tcpClient.EndConnect($asyncResult)
            $elapsed = $stopwatch.ElapsedMilliseconds
            $tcpClient.Close()
            return $elapsed
        } else {
            $tcpClient.Close()
            return $null
        }
    } catch {
        return $null
    }
}

# ���� ���������� �� ��������� ������
$endTime = (Get-Date).AddSeconds($Duration)
$results = @()
$successCount = 0
$responseTimes = @()

while ((Get-Date) -lt $endTime) {
    $isOnline = Test-InternetConnection
    $responseTime = Measure-ConnectionTime
    
    if ($isOnline) {
        $successCount++
        if ($responseTime -ne $null) {
            $responseTimes += $responseTime
        }
    }
    
    $results += @{
        Timestamp = Get-Date
        Online = $isOnline
        ResponseTime = $responseTime
    }
    
    Start-Sleep -Milliseconds 500
}

# ���������� ����������
$totalTests = $results.Count
$availability = if ($totalTests -gt 0) { ($successCount / $totalTests) * 100 } else { 0 }
$avgResponseTime = if ($responseTimes.Count -gt 0) { ($responseTimes | Measure-Object -Average).Average } else { 0 }
$minResponseTime = if ($responseTimes.Count -gt 0) { ($responseTimes | Measure-Object -Minimum).Minimum } else { 0 }
$maxResponseTime = if ($responseTimes.Count -gt 0) { ($responseTimes | Measure-Object -Maximum).Maximum } else { 0 }
$packetLoss = 100 - $availability

# ��������� ����� � ������� Prometheus
$timestamp = [int][double]::Parse((Get-Date -UFormat %s))
$metrics = @"
# HELP internet_availability_percent ����������� ��������� �� ��������� ${Duration} ������ � ���������
# TYPE internet_availability_percent gauge
internet_availability_percent $availability
# HELP internet_avg_response_time_ms ������� ����� ������ �� ��������� ${Duration} ������
# TYPE internet_avg_response_time_ms gauge
internet_avg_response_time_ms $avgResponseTime
# HELP internet_min_response_time_ms ����������� ����� ������ �� ��������� ${Duration} ������
# TYPE internet_min_response_time_ms gauge
internet_min_response_time_ms $minResponseTime
# HELP internet_max_response_time_ms ������������ ����� ������ �� ��������� ${Duration} ������
# TYPE internet_max_response_time_ms gauge
internet_max_response_time_ms $maxResponseTime
# HELP internet_packet_loss_percent ������ ������� �� ��������� ${Duration} ������
# TYPE internet_packet_loss_percent gauge
internet_packet_loss_percent $packetLoss
# HELP internet_total_tests ����� ���������� ������ �� ��������� ${Duration} ������
# TYPE internet_total_tests gauge
internet_total_tests $totalTests
"@

# ���������� ������� � ����
$metrics | Out-File -FilePath $OutputFile -Encoding utf8 -Force
