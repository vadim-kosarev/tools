:loop
powershell -ExecutionPolicy Bypass -File .\iperf_exporter.ps1
timeout /t 300 >nul
goto loop

