@echo off
rem Most Unitree L2 -> OSC - uruchom dwuklikiem. Zamknij okno, zeby zatrzymac.
rem Ustawienia (zmien wartosci po znaku =):
set OSC_HOST=127.0.0.1
set OSC_PORT=9000
set POINTS=400
set FRAMES=12
set CLOUD_RATE=10
set IMU_RATE=60
set GRID=32

title Unitree L2 -^> OSC %OSC_HOST%:%OSC_PORT%
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\python.exe" (
    echo Brak srodowiska Pythona - najpierw uruchom INSTALUJ.bat
    pause
    exit /b 1
)
"%~dp0venv\Scripts\python.exe" "%~dp0app\osc_bridge.py" --host %OSC_HOST% --port %OSC_PORT% --points %POINTS% --frames %FRAMES% --cloud-rate %CLOUD_RATE% --imu-rate %IMU_RATE% --grid %GRID%
echo.
echo Most OSC zakonczyl dzialanie.
pause
