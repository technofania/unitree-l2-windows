@echo off
rem Przywraca siec, usuwa venv i skroty z pulpitu.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1"
