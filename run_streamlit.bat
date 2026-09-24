@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: double-click install_windows.bat. Daily startup never installs packages.
    pause
    exit /b 1
)
set "FINANCIAL_DEPLOYMENT=local"
set "FINANCIAL_WORKSPACE=main"
if not defined FINANCIAL_DATA_DIR set "FINANCIAL_DATA_DIR=%LOCALAPPDATA%\FinancialReportQA\data"
echo Data directory: %FINANCIAL_DATA_DIR%
".venv\Scripts\python.exe" -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501
pause
