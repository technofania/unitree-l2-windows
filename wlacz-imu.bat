@echo off
rem Wlacza IMU w lidarze (jesli ktos je wylaczyl). Zamknij najpierw podglad i OSC.
title Wlaczanie IMU - Unitree L2
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0app\l2_receiver.py" --enable-imu
echo.
pause
