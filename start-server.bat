@echo off
rem Serwer Unitree L2 -> pamiec wspoldzielona (dla TouchDesignera i wlasnych skryptow).
rem Zwykle NIE trzeba go uruchamiac recznie - komponent lidar_live w TD robi to sam.
rem Recznie przydaje sie do diagnostyki (widac status) albo gdy w TD wylaczysz Autostart.
title Unitree L2 - serwer
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\python.exe" (
    echo Brak srodowiska Pythona - najpierw uruchom INSTALUJ.bat
    pause
    exit /b 1
)
"%~dp0venv\Scripts\python.exe" "%~dp0app\l2_server.py"
echo.
echo Serwer zakonczyl dzialanie.
pause
