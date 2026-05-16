@echo off
REM Запуск MCP stdio сервера с активацией виртуального окружения
REM Continue.dev использует этот файл как command в config.yaml

set RAG_DIR=%~dp0
call "%RAG_DIR%..\venv\Scripts\activate.bat" 2>nul
if errorlevel 1 (
    call "%RAG_DIR%.venv\Scripts\activate.bat" 2>nul
)

python "%RAG_DIR%mcp_stdio.py"

