@echo off
rem One-click setup: installs what the bot needs, asks for a notification topic,
rem sends a test alert, and schedules the daily check. Just double-click this file.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer, then double-click setup.bat again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the bot's environment...
    python -m venv .venv || goto :failed
)
echo Installing requirements (first time takes a minute)...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :failed

".venv\Scripts\python.exe" watcher.py --setup || goto :failed

echo.
echo Scheduling the daily check at 09:00...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_task.ps1" || goto :failed

echo.
echo All done! The bot will now check every morning and notify your phone when tickets open.
pause
exit /b 0

:failed
echo.
echo Something went wrong - see the message above.
pause
exit /b 1
