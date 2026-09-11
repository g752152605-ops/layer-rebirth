@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing local environment. Run setup_windows.cmd first.
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
set "MPLCONFIGDIR=%~dp0.matplotlib-cache"
".venv\Scripts\python.exe" run_app.py

