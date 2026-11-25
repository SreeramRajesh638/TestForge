@echo off
REM One-time setup for TestForge on Windows. Double-click this file.
REM Needs Python 3.10+ from https://www.python.org/downloads/ ("Add Python to PATH" ticked).
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" on the first screen of the installer.
  pause
  exit /b 1
)
echo Creating virtual environment...
python -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
echo Installing TestForge and its dependencies...
python -m pip install --upgrade pip >nul
pip install -e ".[all]" || goto :fail
echo Installing the browser for UI automation (about 150 MB, first time only)...
python -m playwright install chromium || echo WARNING: the browser download failed - everything works except UI automation. Run setup again later.
echo Running the framework's own tests...
python -m pytest -q || goto :fail
echo.
echo Setup complete. Double-click run_dashboard.bat to start the web dashboard.
pause
exit /b 0
:fail
echo Setup failed - see the messages above.
pause
exit /b 1
