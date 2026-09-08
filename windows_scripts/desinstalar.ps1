# 1. Comprobar si se está ejecutando como Administrador
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Error: No tienes permisos suficientes para desinstalar el agente." -ForegroundColor Red
    Write-Host "[*] Haz clic derecho en 'desinstalar.bat' y selecciona 'Ejecutar como administrador'." -ForegroundColor Yellow
    Exit
}

$UsuarioReal = $env:USERNAME
$TaskName = "Agente_IntegriTI_$UsuarioReal"
# Encontrar la raíz del proyecto
$DirActual = (Get-Item $PSScriptRoot).Parent.FullName

Write-Host "[*] Deteniendo y eliminando el servicio..." -ForegroundColor Cyan
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "[*] Limpiando archivos residuales..." -ForegroundColor Cyan
# Usamos $DirActual para apuntar con precisión láser a los logs
Remove-Item -Path "$DirActual\Client\*.log", "$DirActual\Client\*.txt", "$DirActual\Client\*.pid" -Force -ErrorAction SilentlyContinue

Write-Host "[OK] Sistema Windows limpio. Tarea programada eliminada." -ForegroundColor Green