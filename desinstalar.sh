#!/bin/bash
echo "=================================================="
echo " Desinstalador de Telemetría Académica (Linux)    "
echo "=================================================="

# 1. Detectar el usuario real y su UID (Evadiendo la trampa de sudo)
if [ -n "$SUDO_USER" ]; then
    USUARIO_REAL="$SUDO_USER"
    HOME_REAL=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    USUARIO_REAL="$USER"
    HOME_REAL="$HOME"
fi
USER_UID=$(id -u "$USUARIO_REAL")

echo "[*] Deteniendo el servicio en la sesión de $USUARIO_REAL..."
# 2. Detener y deshabilitar el servicio inyectando la sesión correcta
sudo -u "$USUARIO_REAL" XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user stop integriti.service 2>/dev/null
sudo -u "$USUARIO_REAL" XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user disable integriti.service 2>/dev/null

echo "[*] Limpiando configuraciones y permisos del sistema..."
# 3. Eliminar archivos usando la ruta real del usuario
rm -f "$HOME_REAL/.config/systemd/user/integriti.service"
sudo rm -f "/etc/sudoers.d/integriti_agent"

# 4. Recargar systemd para que olvide el servicio
sudo -u "$USUARIO_REAL" XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user daemon-reload 2>/dev/null

echo "[*] Limpiando archivos residuales..."
# 5. Limpiar archivos residuales generados durante la ejecución
# (Buscamos tanto en la raíz como en la carpeta Client por si acaso)
rm -f daemon_salida.log Client/daemon_salida.log 2>/dev/null
rm -f combined_log.log Client/combined_log.log 2>/dev/null
rm -f crash_log.txt Client/crash_log.txt 2>/dev/null
rm -f agente.pid Client/agente.pid 2>/dev/null
rm -f cron_error.log 2>/dev/null

echo "=================================================="
echo " DESINSTALACIÓN COMPLETADA CON ÉXITO.             "
echo " El sistema ha quedado completamente limpio.      "
echo "=================================================="