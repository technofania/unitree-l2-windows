@echo off
rem Ponowna konfiguracja karty sieciowej pod lidar (np. po zmianie adaptera USB-Ethernet).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\network-setup.ps1"
