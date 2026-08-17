@echo off
setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
set "PORT=8000"

if not exist venv (
    echo Virtual environment not found. Please run setup.bat first.
    exit /b 1
)

call "%PROJECT_DIR%venv\Scripts\activate.bat"

if not exist temp mkdir temp

REM --- Free up the port automatically if a previous server instance is
REM --- still bound to it, so we never have to manually check/close it. ---
set "EXISTING_PID="
for /f "tokens=5" %%p in ('netstat -aon ^| findstr "LISTENING" ^| findstr ":%PORT% "') do (
    set "EXISTING_PID=%%p"
)
if defined EXISTING_PID (
    echo Port %PORT% is already in use by PID !EXISTING_PID! - terminating it...
    taskkill /F /PID !EXISTING_PID! >nul 2>nul
    timeout /t 1 /nobreak >nul
)

REM --- Open the browser in the background (no extra terminal window) a
REM --- few seconds after launch, once the server has had time to start. ---
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'"

echo Starting FastAPI server on http://localhost:%PORT% ...
echo Press Ctrl+C to stop the server.

REM --- Run the server in this same window instead of spawning a second
REM --- one, so start.bat only ever opens a single terminal. ---
uvicorn main:app --host 127.0.0.1 --port %PORT%

endlocal
