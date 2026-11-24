@echo off
REM Generates tests for examples\calculator.py with a free local model.
REM Needs: setup_windows.bat run once, and Ollama installed from https://ollama.com
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama was not found. Install it from https://ollama.com/download and run this again.
  pause
  exit /b 1
)
set MODEL=qwen2.5-coder:3b
if not "%~1"=="" set MODEL=%~1
echo Downloading %MODEL% (first time only, about 2 GB)...
ollama pull %MODEL% || goto :fail
call .venv\Scripts\activate.bat
python -m testforge generate examples\calculator.py --llm ollama --model %MODEL% || goto :fail
echo.
echo Done. Generated tests and the report are in the generated_tests folder.
pause
exit /b 0
:fail
echo Something failed - see the messages above.
pause
exit /b 1
