#!/bin/bash

echo "=================================================="
echo " Desinstalador de Telemetría Académica (Linux)    "
echo "=================================================="

# Asegurar directorio de trabajo del proyecto
DIR_ACTUAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR_ACTUAL"

# 1. Detectar el usuario real
if [ -n "$SUDO_USER" ]; then
    USUARIO_REAL="$SUDO_USER"
    HOME_REAL=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    USUARIO_REAL="$USER"
    HOME_REAL="$HOME"
fi

DIR_GLOBAL="$HOME_REAL/.telemetria_global"

# 2. Detener el agente en segundo plano
echo "[*] Buscando y deteniendo procesos del agente..."
if [ -f "$DIR_ACTUAL/agente.pid" ]; then
    PID=$(cat "$DIR_ACTUAL/agente.pid" 2>/dev/null)
    if [ -n "$PID" ]; then
        sudo kill -9 $PID 2>/dev/null
    fi
    rm -f "$DIR_ACTUAL/agente.pid"
    echo "[OK] Proceso (PID: $PID) eliminado mediante archivo."
fi
# Medida de seguridad adicional: matar cualquier client.py corriendo
sudo pkill -f "client.py" 2>/dev/null
echo "[OK] Procesos de Python asociados detenidos."

# 3. Eliminar la persistencia gráfica y permisos especiales
echo "[*] Limpiando registros de arranque y pases VIP..."

# A) Limpiar Autostart
AUTOSTART_FILE="$HOME_REAL/.config/autostart/agente_telemetria.desktop"
if [ -f "$AUTOSTART_FILE" ]; then
    sudo rm -f "$AUTOSTART_FILE"
    echo "[OK] Lanzador gráfico eliminado."
fi

# B) Limpiar sudoers
if [ -f "/etc/sudoers.d/integriti_agent" ]; then
    sudo rm -f "/etc/sudoers.d/integriti_agent"
    echo "[OK] Permisos silenciosos revocados."
fi

# C) Limpiar cron (por si quedó alguna versión antigua)
(sudo crontab -u root -l 2>/dev/null | grep -v "ejecutar") | sudo crontab -u root - 2>/dev/null

# D) Revocar permisos xhost de root y limpiar .xsessionrc
su - "$USUARIO_REAL" -c "xhost -SI:localuser:root" 2>/dev/null || xhost -SI:localuser:root 2>/dev/null || true
if [ -f "$HOME_REAL/.xsessionrc" ]; then
    sed -i '/xhost +SI:localuser:root/d' "$HOME_REAL/.xsessionrc"
fi

# 4. Eliminar el inyector global y la bandera
echo "[*] Eliminando archivos inyectados..."
if [ -d "$DIR_GLOBAL" ]; then
    sudo rm -rf "$DIR_GLOBAL"
    echo "[OK] Carpeta $DIR_GLOBAL y sitecustomize eliminados." 
fi

# 5. Limpiar los perfiles de terminal del alumno
echo "[*] Limpiando variables de entorno en la terminal..."
limpiar_perfil() {
    PERFIL="$1"
    if [ -f "$PERFIL" ]; then
        # Eliminamos el comentario y la línea del PYTHONPATH
        sudo sed -i '/--- INYECCION TELEMETRIA ACADEMICA ---/d' "$PERFIL"
        sudo sed -i '\|.telemetria_global|d' "$PERFIL"
        echo "[OK] Terminal restaurada: $PERFIL"
    fi
}

limpiar_perfil "$HOME_REAL/.bashrc"
limpiar_perfil "$HOME_REAL/.zshrc"

# 6. Limpiar archivos residuales generados durante la ejecución
rm -f daemon_salida.log combined_log.log crash_log.txt agente.pid

echo "=================================================="
echo " DESINSTALACIÓN COMPLETADA CON ÉXITO.             "
echo " El sistema ha quedado completamente limpio.      "
echo "=================================================="