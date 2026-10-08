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
Write-Host "[*] Verificando procesos residuales del agente..." -ForegroundColor Cyan
$RutaCliente = "$DirActual\Client\client.py"
$ProcesosAgente = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$([regex]::Escape($RutaCliente))*" }

if ($ProcesosAgente) {
    foreach ($proc in $ProcesosAgente) {
        Write-Host "[!] Proceso residual encontrado (PID $($proc.ProcessId)). Terminando..." -ForegroundColor Yellow
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[OK] Procesos residuales eliminados." -ForegroundColor Green
} else {
    Write-Host "[OK] No quedaron procesos del agente corriendo." -ForegroundColor Green
}

Write-Host "[*] Limpiando archivos residuales..." -ForegroundColor Cyan
# Usamos $DirActual para apuntar con precisión láser a los logs
Remove-Item -Path "$DirActual\Client\*.log", "$DirActual\Client\*.txt", "$DirActual\Client\*.pid" -Force -ErrorAction SilentlyContinue

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