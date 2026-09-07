#!/bin/bash

echo "=================================================="
echo " Lanzador del Agente de Telemetría (Daemon)       "
echo "=================================================="

PID_FILE="agente.pid"
LOG_SALIDA="daemon_salida.log"

# 1. Verificar si el agente ya está corriendo
if [ -f "$PID_FILE" ]; then
    if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "[!] El agente ya está en ejecución (PID: $(cat $PID_FILE))."
        echo "[!] Para detenerlo, ejecuta: kill \$(cat $PID_FILE)"
        exit 1
    else
        # El archivo existe pero el proceso murió, lo limpiamos
        rm "$PID_FILE"
    fi
fi

# 2. Verificar que la instalación se hizo correctamente
if [ ! -d "venv" ]; then
    echo "[X] Error: No se encontró el entorno virtual 'venv'."
    echo "    Por favor, ejecuta './instalar.sh' primero."
    exit 1
fi

echo "[*] Levantando client.py en segundo plano..."

# 3. Lanzar el cliente usando el Python del venv y aislarlo de la terminal
nohup ./venv/bin/python Client/client.py > "$LOG_SALIDA" 2>&1 &

# 4. Guardar el ID del proceso
PID=$!
echo $PID > "$PID_FILE"

echo "[OK] Agente operando silenciosamente en el fondo (PID: $PID)."
echo "[INFO] Puedes cerrar esta terminal con seguridad."
echo "[INFO] Revisa '$LOG_SALIDA' si necesitas ver la consola del cliente."
echo "=================================================="