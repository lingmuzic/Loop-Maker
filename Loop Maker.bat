@echo off
rem Double-click launcher for the Loop Maker GUI (Windows).
rem Uses the project's virtual environment so all dependencies are found.

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run these once in this folder:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem pythonw = no console window; "start" returns immediately so this window closes.
start "" ".venv\Scripts\pythonw.exe" -m loopmaker.gui
