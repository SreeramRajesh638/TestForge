@echo off
REM Starts the TestForge dashboard and opens it in your browser.
REM For the free Groq AI: put your Groq API key (gsk_...) alone in a file
REM named groq_key.txt next to this script. That file is never uploaded to GitHub.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
if exist groq_key.txt (
  set /p GROQ_API_KEY=<groq_key.txt
  echo Groq key loaded from groq_key.txt
) else (
  echo No groq_key.txt found - the Groq backend will not work, the offline Mock backend will.
)
call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8000
python -m testforge serve
