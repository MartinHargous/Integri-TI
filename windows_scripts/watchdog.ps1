# ==================================================================
#  Vigilante del agente de telemetría (Windows)
# ==================================================================
# Por qué existe este script, separado del instalador:
#
# El watchdog original (el trigger "cada 1 minuto" del Programador de
# Tareas) relanzaba DIRECTAMENTE pythonw.exe client.py, confiando en que
# MultipleInstances=IgnoreNew evitara duplicados mientras el agente
# estuviera vivo. Eso funciona si mataron el proceso (Administrador de
# Tareas, kill -9, lo que sea): el proceso termina, el PID deja de
# existir, y en el siguiente disparo el Programador de Tareas lo nota y
# lanza una instancia nueva.
#
# Pero si en vez de MATAR el proceso lo SUSPENDEN (p.ej. con Process
# Hacker/Process Explorer -> "Suspend", o con NtSuspendProcess/
# SuspendThread por código) el proceso sigue existiendo para Windows: el
# PID está vivo, el handle está vivo, "MultipleInstances=IgnoreNew" ve
# que ya hay una instancia corriendo y NO hace nada. El resultado es un
# agente completamente congelado -sin capturar nada- que ni el
# Programador de Tareas ni el propio proceso (que por definición no
# puede ejecutar código estando suspendido) pueden notar ni arreglar
# solos. Hace falta un tercero vivo que lo revise desde afuera.
#
# Este script es ese tercero. Se ejecuta cada minuto (mismo trigger que
# antes), y en vez de lanzar el agente a ciegas:
#   1. Busca el proceso real del agente (por ruta de ejecutable Y por la
#      línea de comando que apunta a client.py, para no confundirlo con
#      cualquier otro pythonw.exe del sistema).
#   2. Si lo encuentra SUSPENDIDO (todos sus hilos en estado de espera
#      por WaitReason "Suspended" -la marca que deja específicamente
#      SuspendThread/NtSuspendProcess, no una espera normal de I/O-), lo
#      mata. Un proceso suspendido no se puede "reanudar hacia el
#      funcionamiento correcto" de forma confiable (no sabemos en qué
#      instrucción quedó congelado ni si el que lo suspendió va a
#      reanudarlo él mismo más tarde para seguir ocultándose), así que
#      la única reacción segura es matarlo y arrancar uno limpio.
#   3. Si no encuentra ninguna instancia viva Y sana (porque la mató en
#      el paso 2, o porque ya no existía), lanza una nueva.
#
# Pensado para ejecutarse oculto, sin ventana, lanzado por
# lanzar_vigilante_oculto.vbs desde el Programador de Tareas.
#
# DIAGNÓSTICO: este script corre sin consola (lo lanza wscript.exe con
# ventana oculta), así que cualquier Write-Output/Write-Host se pierde
# en la nada — exactamente el mismo problema de "pythonw.exe no tiene
# dónde mostrar nada" que ya resolvimos en client.py con
# _redirigir_salida_a_log(). Por eso todo lo que antes era Write-Output
# ahora se escribe a Client\vigilante_log.txt (al lado de agente_log.txt,
# así el desinstalador ya lo limpia solo con el glob *.log existente).
# Si el agente no arranca después de instalar, ESTE archivo es el primer
# lugar donde mirar — dice exactamente en qué paso se detuvo.

param(
    [string]$PythonExe,
    [string]$ScriptPath,
    [string]$WorkDir
)

# No usamos [Parameter(Mandatory=$true)] a propósito: si por algún motivo
# el binding de un parámetro llegara a fallar, Mandatory=$true hace que
# PowerShell se quede esperando a que alguien lo tipee por teclado — y
# como este proceso no tiene consola ni nadie mirando, eso es un cuelgue
# silencioso para siempre, no un error que se pueda diagnosticar. Mejor
# validar a mano y dejar constancia en el log.

$LogPath = if ($WorkDir -and (Test-Path $WorkDir)) {
    Join-Path $WorkDir "vigilante_log.txt"
} else {
    Join-Path $PSScriptRoot "vigilante_log.txt"
}

function Escribir {
    param([string]$Mensaje)
    try {
        $linea = "[$(Get-Date -Format 's')] $Mensaje"
        Add-Content -Path $LogPath -Value $linea -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {
        # Si ni el log se puede escribir, no hay nada más que hacer.
    }
}

function Test-ProcesoSuspendido {
    param($Proceso)
    try {
        $hilos = $Proceso.Threads
        if (-not $hilos -or $hilos.Count -eq 0) {
            return $false
        }
        $suspendidos = 0
        foreach ($hilo in $hilos) {
            try {
                if ($hilo.ThreadState -eq [System.Diagnostics.ThreadState]::Wait -and
                    $hilo.WaitReason -eq [System.Diagnostics.ThreadWaitReason]::Suspended) {
                    $suspendidos++
                }
            } catch {
                # Un hilo puede terminar entre que lo listamos y lo consultamos; se ignora.
            }
        }
        # Si TODOS los hilos están en WaitReason=Suspended, el proceso fue
        # congelado desde afuera. Una espera normal de I/O o de red (que es
        # lo que hace un agente sano la mayor parte del tiempo) nunca deja
        # ESTE WaitReason puntual en todos los hilos a la vez.
        return ($suspendidos -gt 0 -and $suspendidos -eq $hilos.Count)
    } catch {
        return $false
    }
}

try {
    Escribir "--- Vigilante disparado ---"
    Escribir "PythonExe=[$PythonExe] ScriptPath=[$ScriptPath] WorkDir=[$WorkDir]"

    if ([string]::IsNullOrWhiteSpace($PythonExe) -or
        [string]::IsNullOrWhiteSpace($ScriptPath) -or
        [string]::IsNullOrWhiteSpace($WorkDir)) {
        Escribir "[ERROR] Faltó algún parámetro al invocar el script. Revisa el comando armado en lanzar_vigilante_oculto.vbs."
        exit 1
    }
    if (-not (Test-Path $PythonExe)) {
        Escribir "[ERROR] No existe el ejecutable de Python en: $PythonExe (¿falló 'py -3.14 -m venv venv' durante la instalación? revisa install_log.txt)"
        exit 1
    }
    if (-not (Test-Path $ScriptPath)) {
        Escribir "[ERROR] No existe client.py en: $ScriptPath"
        exit 1
    }

    $rutaEscapadaWql = $PythonExe.Replace('\', '\\').Replace("'", "''")
    $nombreScript = [System.IO.Path]::GetFileName($ScriptPath)

    $candidatos = Get-CimInstance Win32_Process -Filter "ExecutablePath = '$rutaEscapadaWql'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$nombreScript*" }

    Escribir "Candidatos encontrados: $(@($candidatos).Count)"

    $instanciaViva = $false

    foreach ($candidato in $candidatos) {
        $proceso = Get-Process -Id $candidato.ProcessId -ErrorAction SilentlyContinue
        if (-not $proceso) {
            continue
        }
        if (Test-ProcesoSuspendido $proceso) {
            Escribir "PID $($proceso.Id) suspendido externamente. Matando para forzar reinicio limpio."
            Stop-Process -Id $proceso.Id -Force -ErrorAction SilentlyContinue
        } else {
            Escribir "PID $($proceso.Id) vivo y sano. No se hace nada."
            $instanciaViva = $true
        }
    }

    if (-not $instanciaViva) {
        Escribir "Sin instancia viva y sana. Relanzando agente: `"$PythonExe`" `"$ScriptPath`" (cwd=$WorkDir)"
        $nuevoProceso = Start-Process -FilePath $PythonExe -ArgumentList "`"$ScriptPath`"" -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru
        Escribir "Start-Process devolvió PID $($nuevoProceso.Id)"
    }

    Escribir "--- Vigilante terminado sin errores ---"
} catch {
    Escribir "[ERROR] Excepción no controlada: $($_.Exception.GetType().FullName): $($_.Exception.Message)"
    Escribir "[ERROR] StackTrace: $($_.ScriptStackTrace)"
}