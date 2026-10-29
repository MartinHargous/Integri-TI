# 1. Comprobar si se está ejecutando como Administrador
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Error: No tienes permisos suficientes para desinstalar el agente." -ForegroundColor Red
    Write-Host "[*] Haz clic derecho en 'desinstalar.bat' y selecciona 'Ejecutar como administrador'." -ForegroundColor Yellow
    Exit
}

$UsuarioReal = $env:USERNAME
$TaskName = "Agente_ProyectoTitulo_$UsuarioReal"
# Encontrar la raíz del proyecto
$DirActual = (Get-Item $PSScriptRoot).Parent.FullName

Write-Host "[*] Deteniendo y eliminando el servicio..." -ForegroundColor Cyan
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Verificación explícita: con el trigger watchdog (repite cada minuto), si la tarea
# no se eliminó de verdad, el agente se relanzaría solo indefinidamente. No podemos
# confiar en que el Unregister anterior haya funcionado solo porque no lanzó error.
$TareaResidual = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($TareaResidual) {
    Write-Host "[!] La tarea programada no se pudo eliminar en el primer intento. Reintentando..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    $TareaResidual = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($TareaResidual) {
        Write-Host "[!] ADVERTENCIA: La tarea '$TaskName' sigue registrada." -ForegroundColor Red
        Write-Host "[!] El agente puede seguir reiniciandose solo (watchdog). Revisa manualmente en el Programador de tareas." -ForegroundColor Red
    } else {
        Write-Host "[OK] Tarea eliminada en el segundo intento." -ForegroundColor Green
    }
} else {
    Write-Host "[OK] Tarea programada eliminada correctamente." -ForegroundColor Green
}

# Kill de respaldo: por si quedó una instancia de pythonw.exe corriendo el client.py
# de este proyecto (ej. lanzada por un ciclo del watchdog justo antes de desinstalar),
# aunque la tarea ya no exista.
#
# OJO con el filtro: antes se armaba el patrón de -like con [regex]::Escape($RutaCliente).
# [regex]::Escape() duplica cada backslash ("\" -> "\\") porque está pensado para
# patrones de -match (regex), donde "\" es el propio caracter de escape. Pero -like
# NO es regex — ahí "\" no significa nada especial — así que ese patrón terminaba
# buscando DOS backslashes seguidos dentro de un CommandLine que solo tiene uno.
# Resultado: nunca matcheaba NADA, por más que el proceso real estuviera ahí mismo
# en la lista. Por eso "no quedaron procesos" salía siempre, incluso con el agente
# vivo y mandando syncs — y por eso el venv de arriba a veces no se podía borrar (el
# proceso real seguía corriendo y con sus DLLs cargadas). Usamos .Contains(), que
# compara el texto tal cual, sin ningún motor de patrones de por medio.
Write-Host "[*] Verificando procesos residuales del agente..." -ForegroundColor Cyan
$RutaCliente = "$DirActual\Client\client.py"

# Dos pasadas con una pequeña espera entre medio: si justo cuando se desinstaló
# había un tick del vigilante en vuelo (ver watchdog.ps1), puede que
# termine de lanzar una instancia nueva una fracción de segundo DESPUÉS de esta
# primera revisión. Una sola pasada instantánea puede no alcanzar a verla.
$totalEncontrados = 0
for ($pasada = 1; $pasada -le 2; $pasada++) {
    $ProcesosAgente = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($RutaCliente) }

    if ($ProcesosAgente) {
        foreach ($proc in $ProcesosAgente) {
            Write-Host "[!] Proceso residual encontrado (PID $($proc.ProcessId)). Terminando..." -ForegroundColor Yellow
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            $totalEncontrados++
        }
    }
    if ($pasada -eq 1) {
        Start-Sleep -Seconds 2
    }
}

if ($totalEncontrados -gt 0) {
    Write-Host "[OK] Procesos residuales eliminados ($totalEncontrados)." -ForegroundColor Green
} else {
    Write-Host "[OK] No quedaron procesos del agente corriendo." -ForegroundColor Green
}

# --- Eliminar el entorno virtual (venv) ---
# Va DESPUÉS del kill de procesos residuales de arriba y no antes: pythonw.exe
# vive adentro de este venv, así que si todavía estuviera corriendo, Windows
# tendría el .exe y sus DLLs bloqueados y el borrado fallaría a mitad de
# camino, dejando una carpeta "venv" fantasma que ni el instalador (que
# solo hace "py -3.14 -m venv venv" si no existe) ni una reinstalación
# normal notarían. Sin este paso, cada reinstalación deja entornos huérfanos
# acumulándose en cada equipo del curso.
Write-Host "[*] Eliminando entorno virtual (venv)..." -ForegroundColor Cyan
$RutaVenv = "$DirActual\venv"
if (Test-Path $RutaVenv) {
    # Aunque ya no quede ningún proceso del agente corriendo, Windows puede
    # tardar un instante en soltar los handles de las DLLs nativas que este
    # venv tenía cargadas (wpcap.dll/Packet.dll de Npcap para el sniffer,
    # los hooks de pynput, etc.) — el Stop-Process de arriba es inmediato,
    # pero la liberación real del archivo por el sistema a veces no lo es.
    # Por eso no alcanza un solo intento: reintentamos varias veces con una
    # espera creciente antes de rendirnos.
    $intentosVenv = 6
    for ($i = 1; $i -le $intentosVenv; $i++) {
        Remove-Item -Path $RutaVenv -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $RutaVenv)) {
            break
        }
        Start-Sleep -Seconds $i
    }
    if (Test-Path $RutaVenv) {
        Write-Host "[!] No se pudo eliminar por completo $RutaVenv tras $intentosVenv intentos (algo sigue usando un archivo adentro). Cierra cualquier terminal/editor abierto en esa carpeta y bórrala manualmente, o volvé a correr este script en unos segundos." -ForegroundColor Red
    } else {
        Write-Host "[OK] venv eliminado." -ForegroundColor Green
    }
} else {
    Write-Host "[INFO] No había un venv para eliminar." -ForegroundColor Yellow
}

Write-Host "[*] Limpiando archivos del instalador..." -ForegroundColor Cyan
$ArchivoVBS = "$DirActual\windows_scripts\lanzar_vigilante_oculto.vbs"
if (Test-Path $ArchivoVBS) {
    Remove-Item -Path $ArchivoVBS -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Lanzador VBS eliminado." -ForegroundColor Green
} else {
    Write-Host "[INFO] No había lanzador VBS para eliminar." -ForegroundColor Yellow
}
Write-Host "[*] Limpiando archivos residuales..." -ForegroundColor Cyan
# Usamos $DirActual para apuntar con precisión láser a los logs.
# OJO: config.txt queda EXCLUIDO a propósito del glob *.txt — no es un log
# ni un residuo, es la configuración que el instalador/profesor puso a mano
# en esta máquina (server_url del túnel de Cloudflare, server_ip, intervalos).
# Sin esta exclusión, cada reinstalación (ej. para aplicar un fix de
# client.py a mitad de semestre) borraba esa configuración junto con los
# logs, obligando a reconfigurar Cloudflare a mano en cada equipo del curso.
Remove-Item -Path "$DirActual\Client\*.log", "$DirActual\Client\*.txt", "$DirActual\Client\*.pid" -Exclude "config.txt" -Force -ErrorAction SilentlyContinue

# --- Liberar y eliminar state.json (protegido contra el alumno) ---
# El agente bloquea este archivo con icacls para que el alumno no pueda
# editarlo ni borrarlo (ver _bloquear_archivo en client.py) — un Remove-Item
# normal fallaría acá exactamente igual que le fallaría a un alumno. Este
# script corre como Administrador (se verificó al inicio), así que puede
# revertir el ACL con /reset antes de borrar, igual que _desbloquear_archivo
# hace dentro del propio agente. Sin este paso, cada reinstalación deja el
# state.json de la instalación anterior intacto y bloqueado.
Write-Host "[*] Liberando y eliminando state.json..." -ForegroundColor Cyan
foreach ($RutaState in @("$DirActual\Client\state.json", "$DirActual\Client\state.tmp")) {
    if (Test-Path $RutaState) {
        icacls $RutaState /reset | Out-Null
        Remove-Item -Path $RutaState -Force -ErrorAction SilentlyContinue
        if (Test-Path $RutaState) {
            Write-Host "[!] No se pudo eliminar $RutaState. Bórralo manualmente (puede requerir icacls /reset)." -ForegroundColor Red
        } else {
            Write-Host "[OK] Eliminado: $RutaState" -ForegroundColor Green
        }
    }
}

# --- Limpieza del hook global de detección de errores (ErrorDetection / sitecustomize) ---
# La instalación modifica PYTHONPATH a nivel de usuario y deja un sitecustomize.py
# que Python carga automáticamente en TODA ejecución futura del usuario, no solo
# mientras el agente está activo. Si no se revierte, el hook queda instalado
# permanentemente incluso después de "desinstalar" el agente.
Write-Host "[*] Revirtiendo hook global de Python (sitecustomize)..." -ForegroundColor Cyan
$DirGlobal = "$env:USERPROFILE\.telemetria_global"

# 1. Quitar SOLO la entrada de .telemetria_global de PYTHONPATH, preservando
#    cualquier otra ruta que el usuario haya agregado por su cuenta.
$CurrentPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "User")
if ($CurrentPythonPath -and $CurrentPythonPath -match [regex]::Escape($DirGlobal)) {
    $Partes = $CurrentPythonPath -split ";" | Where-Object { $_ -and ($_ -ne $DirGlobal) }
    $NuevoPythonPath = $Partes -join ";"
    if ([string]::IsNullOrWhiteSpace($NuevoPythonPath)) {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $null, "User")
    } else {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $NuevoPythonPath, "User")
    }
    Write-Host "[OK] PYTHONPATH revertido (se preservaron otras rutas si existían)." -ForegroundColor Green
} else {
    Write-Host "[INFO] PYTHONPATH no contenía el hook, nada que revertir." -ForegroundColor Yellow
}

# 2. Eliminar el archivo bandera y el hook en sí. Solo se borran estos dos
#    archivos conocidos, nunca la carpeta completa a la fuerza, por si el
#    usuario guardó algo propio ahí (poco probable, pero no lo asumimos).
$ArchivosHook = @(
    "$DirGlobal\sitecustomize.py",
    "$DirGlobal\.telemetria_active"
)
foreach ($archivo in $ArchivosHook) {
    if (Test-Path $archivo) {
        Remove-Item -Path $archivo -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Eliminado: $archivo" -ForegroundColor Green
    }
}

# 3. Si la carpeta quedó vacía tras lo anterior, sí es seguro borrarla completa.
if ((Test-Path $DirGlobal) -and ((Get-ChildItem -Path $DirGlobal -Force | Measure-Object).Count -eq 0)) {
    Remove-Item -Path $DirGlobal -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Carpeta .telemetria_global vacía eliminada." -ForegroundColor Green
} elseif (Test-Path $DirGlobal) {
    Write-Host "[INFO] .telemetria_global contiene otros archivos, no se elimina la carpeta." -ForegroundColor Yellow
}

Write-Host "[OK] Sistema Windows limpio. Tarea programada eliminada." -ForegroundColor Green