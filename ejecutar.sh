#!/bin/bash

echo "=================================================="
echo " Lanzador del Agente de Telemetría (Daemon)       "
echo "=================================================="

# Asegurar directorio de trabajo del proyecto
DIR_RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR_RAIZ"

PID_FILE="agente.pid"
LOG_SALIDA="daemon_salida.log"

# 1. Asegurar entorno gráfico X11 (para pynput, xdotool y pyperclip)
if [ -z "$DISPLAY" ]; then
    export DISPLAY=":0"
fi

if [ -n "$SUDO_USER" ]; then
    USUARIO_GRAFICO="$SUDO_USER"
    HOME_GRAFICO=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    USUARIO_GRAFICO="$USER"
    HOME_GRAFICO="$HOME"
fi

if [ -z "$XAUTHORITY" ] || [ ! -f "$XAUTHORITY" ]; then
    if [ -f "$HOME_GRAFICO/.Xauthority" ]; then
        export XAUTHORITY="$HOME_GRAFICO/.Xauthority"
    else
        UID_GRAFICO=$(id -u "$USUARIO_GRAFICO" 2>/dev/null || echo "1000")
        POSIBLE_XAUTH=$(find "/run/user/$UID_GRAFICO" -name "*Xauthority*" -o -name "xauth_*" 2>/dev/null | head -n 1)
        if [ -n "$POSIBLE_XAUTH" ]; then
            export XAUTHORITY="$POSIBLE_XAUTH"
        fi
    fi
fi

# Copiar la cookie a /root/.Xauthority para que Xlib la encuentre de forma nativa
if [ -n "$XAUTHORITY" ] && [ -f "$XAUTHORITY" ]; then
    cp -f "$XAUTHORITY" /root/.Xauthority 2>/dev/null || true
    chmod 600 /root/.Xauthority 2>/dev/null || true
fi

if command -v xhost >/dev/null 2>&1; then
    su - "$USUARIO_GRAFICO" -c "xhost +SI:localuser:root" >/dev/null 2>&1 || xhost +SI:localuser:root >/dev/null 2>&1 || true
    su - "$USUARIO_GRAFICO" -c "xhost +SI:localuser:root" >/dev/null 2>&1 || xhost +SI:localuser:root >/dev/null 2>&1 || xhost +local:root >/dev/null 2>&1 || true
fi

# 2. Verificar si el agente ya está corriendo
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