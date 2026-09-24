@echo off
setlocal
cd /d "%~dp0"
py -3.11 --version >nul 2>&1
if errorlevel 1 goto use_python
py -3.11 scripts\install_local.py
goto done
:use_python
python scripts\install_local.py
:done
if errorlevel 1 (
    echo Installation failed. Install Python 3.11 and check network access.
    pause
    exit /b 1
)
echo Ready. Daily startup: run_streamlit.bat
pause
