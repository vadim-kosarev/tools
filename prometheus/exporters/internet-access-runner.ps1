while ($true) {
    # Вызов другого скрипта
    & ".\internet-access.ps1"
    
    # Пауза 1 минута (60 секунд)
    Start-Sleep -Seconds 60
}