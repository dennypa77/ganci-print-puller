@echo off
cd /d "%~dp0"
echo ========================================================
echo  Memeriksa update aplikasi...
echo ========================================================
python updater.py
python main.py
pause
