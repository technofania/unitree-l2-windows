@echo off
rem Cofa zmiany sieciowe zrobione przez instalator (DHCP / poprzednie adresy, regula zapory).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\network-revert.ps1"
