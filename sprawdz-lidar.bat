@echo off
rem Diagnostyka: ping, 5 s odbioru, wersja i tryb pracy lidaru.
title Test lidaru Unitree L2
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\python.exe" (
    echo Brak srodowiska Pythona - najpierw uruchom INSTALUJ.bat
    pause
    exit /b 1
)
echo === Ping 192.168.1.62 ===
ping -n 2 -w 1000 192.168.1.62
echo.
echo === Wersja lidaru ===
"%~dp0venv\Scripts\python.exe" "%~dp0app\l2_receiver.py" --seconds 1 --cmd version | findstr /i "Wersja UWAGA"
echo === Tryb pracy ===
"%~dp0venv\Scripts\python.exe" "%~dp0app\l2_receiver.py" --seconds 1 --cmd config | findstr /i "Tryb UWAGA"
echo.
echo === Odbior danych przez 5 s ===
"%~dp0venv\Scripts\python.exe" "%~dp0app\l2_receiver.py" --seconds 5
echo.
pause
