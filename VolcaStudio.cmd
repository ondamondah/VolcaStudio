@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 goto setup_failed
)
fc /b requirements.txt .venv\volcastudio-requirements.txt >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_failed
    copy /y requirements.txt .venv\volcastudio-requirements.txt >nul
)
".venv\Scripts\python.exe" -m app.main
if not errorlevel 1 exit /b 0
pause
exit /b 1

:setup_failed
echo Could not set up VolcaStudio. Install Python 3.11 and see the error above.
pause
exit /b 1
