@echo off
chcp 65001 >nul
echo 正在以局域网访问模式启动财报智问 V2.0...
echo 同一 Wi-Fi 下的其他设备可通过 http://本机IP:8501 访问。
python -m streamlit run app/main.py --server.address 0.0.0.0 --server.port 8501
pause
