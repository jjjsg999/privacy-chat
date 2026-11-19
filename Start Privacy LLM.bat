@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The project virtual environment was not found.
    echo Follow the Setup instructions in README.md, then double-click this file again.
    pause
    exit /b 1
)

echo Starting Privacy LLM at http://127.0.0.1:8000
echo Keep this window open while using the app. Close it to stop the server.
echo.

start "" /min powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='http://127.0.0.1:8000'; $deadline=(Get-Date).AddSeconds(120); while ((Get-Date) -lt $deadline) { try { Invoke-WebRequest -Uri ($url + '/api/settings') -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Milliseconds 750 } }; exit 1"

"%~dp0.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000

echo.
echo The Privacy LLM server has stopped.
pause
