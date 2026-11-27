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
  REM TestForge reads the file itself (tolerates a BOM and stray spaces, which
  REM "set /p" does not); clear any old key so the file is the one used.
  set "GROQ_API_KEY="
  echo Groq key will be read from groq_key.txt
) else (
  echo No groq_key.txt found - the Groq backend will not work, the offline Mock backend will.
)
if exist anthropic_key.txt (
  set "ANTHROPIC_API_KEY="
  echo Claude key will be read from anthropic_key.txt
)
call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8000
python -m testforge serve
