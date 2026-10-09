@echo off
chcp 65001 >nul
title GBT小土豆V9 总控台
cd /d "%~dp0"
echo ============================================
echo   GBT小土豆V9 总控台  http://127.0.0.1:8765
echo   桌面 APP 会自动优先显示这个界面
echo   关闭本窗口 = 停止总控台
echo ============================================
python -m panel.server
pause
