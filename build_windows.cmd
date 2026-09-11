@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "MPLCONFIGDIR=%~dp0.matplotlib-cache"
".venv\Scripts\python.exe" -m pytest
if errorlevel 1 exit /b 1
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed --name LayerRebirth --add-data "web;web" --add-data "license_public_key.pem;." --collect-all rapidocr --collect-all webview run_app.py
if errorlevel 1 exit /b 1
echo Build complete: dist\LayerRebirth\LayerRebirth.exe
pause
