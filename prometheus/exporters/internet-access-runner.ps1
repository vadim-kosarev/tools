while ($true) {
    # Вызов другого скрипта
    & "C:\Tools\prometheus\exporters\internet-access.ps1"
    
    # Пауза 1 минута (60 секунд)
    Start-Sleep -Seconds 60
}