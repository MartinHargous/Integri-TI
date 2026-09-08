@echo off
echo ==================================================
echo  Desinstalador de Telemetria Academica (Windows)
echo ==================================================
echo [*] Solicitando ejecucion de limpieza profunda...

REM Ejecuta el script de PowerShell saltandose la politica de bloqueo (Bypass)
PowerShell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0windows_scripts\desinstalar.ps1'"

echo.
pause