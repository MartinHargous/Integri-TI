@echo off
echo [*] Iniciando instalador del Agente Integri-TI...
REM Ejecuta PowerShell saltándose la política de bloqueo (Bypass)
PowerShell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0windows_scripts\instalar.ps1'"
pause