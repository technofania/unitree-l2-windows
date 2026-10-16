@echo off
rem Podglad chmury Unitree L2 - uruchom dwuklikiem.
rem Ustawienia (zmien wartosci po znaku =):
set FRAMES=12
set COLOR=intensity
set POINT_SIZE=2

cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo Brak srodowiska Pythona - najpierw uruchom INSTALUJ.bat
    pause
    exit /b 1
)
rem pythonw = bez okna konsoli; bledy pokaza sie w okienku i w viewer-error.log
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0app\viewer.py" --frames %FRAMES% --color %COLOR% --point-size %POINT_SIZE%
