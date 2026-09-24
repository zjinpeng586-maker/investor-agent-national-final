@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    echo First run: install_windows.bat
    pause
    exit /b 1
)
set "FINANCIAL_DEPLOYMENT=public"
set "FINANCIAL_WORKSPACE=main"
if not defined FINANCIAL_DATA_DIR set "FINANCIAL_DATA_DIR=%LOCALAPPDATA%\FinancialReportQA\data"
echo 正在启动局域网只读模式；上传、修改、删除入口关闭。
echo 同一 Wi-Fi 下的其他设备可通过 http://本机IP:8501 访问。
".venv\Scripts\python.exe" -m streamlit run app/main.py --server.address 0.0.0.0 --server.port 8501
pause
