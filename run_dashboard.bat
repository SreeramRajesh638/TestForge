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
netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" /C:"0.0.0.0:8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo Port 8000 is already in use: an older TestForge window is probably still open.
  echo Close that window ^(or restart the computer^) and run this script again.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
echo Starting TestForge from %CD%
start "" http://127.0.0.1:8000
python -m testforge serve
