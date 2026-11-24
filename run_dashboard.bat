@echo off
REM Starts the TestForge dashboard and opens it in your browser.
REM Optional: set ANTHROPIC_API_KEY first to use the Claude backend.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8000
python -m testforge serve
