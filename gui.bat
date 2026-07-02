@echo off
cd /d "%~dp0"
echo ========================================================
echo  Memeriksa update aplikasi...
echo ========================================================
python updater.py
python gui.py
if errorlevel 1 pause
