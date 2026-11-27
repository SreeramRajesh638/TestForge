@echo off
REM One-time setup for TestForge on Windows. Double-click this file.
REM Needs Python 3.10+ from https://www.python.org/downloads/ ("Add Python to PATH" ticked).
cd /d "%~dp0"
REM Find a Python that really runs. "where python" is not enough: Windows ships a
REM Microsoft Store placeholder python.exe that only prints "Python was not found".
set "PYEXE="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" on the first screen of the installer.
  pause
  exit /b 1
)
echo Creating virtual environment...
%PYEXE% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
echo Installing TestForge and its dependencies...
python -m pip install --upgrade pip >nul
pip install -e . || goto :fail
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
