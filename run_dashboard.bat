@echo off
REM Starts the TestForge dashboard and opens it in your browser.
REM Port: 8080 by default; another one with:  run_dashboard.bat 9000
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
set PORT=%~1
if "%PORT%"=="" set PORT=8080
call .venv\Scripts\activate.bat
python -c "import testforge" 2>nul || (
  echo TestForge is not installed in this folder. Run setup_windows.bat first.
  pause
  exit /b 1
)
echo Starting TestForge from %CD% (port %PORT%; the next free port is used if it is busy)
python -m testforge serve --port %PORT% --open
if errorlevel 1 (
  echo.
  echo TestForge stopped with an error - see the messages above.
  pause
)
