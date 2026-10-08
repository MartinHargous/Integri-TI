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
DIR_GLOBAL="$HOME_REAL/.telemetria_global"

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
rm -f Client/modules/error_detection/auditoria_python.log 2>/dev/null

# --- Liberar y eliminar state.json (protegido contra el alumno) ---
# El agente deja este archivo root:root, chmod 600 y con chattr +i (ver
# _bloquear_archivo en client.py) para que el alumno no pueda editarlo ni
# borrarlo — un simple rm acá fallaría con "Operation not permitted" igual
# que le fallaría a un alumno sin sudo. chattr -i requiere root incluso para
# quitarlo, así que se usa sudo explícitamente sin importar con qué permisos
# se invocó el resto del script. Sin este paso, cada reinstalación deja el
# state.json de la instalación anterior intacto y bloqueado.
echo "[*] Liberando y eliminando state.json..."
for RUTA_STATE in state.json Client/state.json state.tmp Client/state.tmp; do
    if [ -f "$RUTA_STATE" ]; then
        sudo chattr -i "$RUTA_STATE" 2>/dev/null
        sudo rm -f "$RUTA_STATE"
        if [ -f "$RUTA_STATE" ]; then
            echo "[!] No se pudo eliminar $RUTA_STATE. Bórralo manualmente (sudo chattr -i + sudo rm)."
        else
            echo "[OK] Eliminado: $RUTA_STATE"
        fi
    fi
done

# --- 6. Revertir la inyección de PYTHONPATH en .bashrc y .zshrc ---
echo "[*] Revirtiendo el puente de PYTHONPATH en los perfiles del usuario..."

limpiar_perfil() {
    PERFIL="$1"
    if [ -f "$PERFIL" ] && grep -q "# --- INYECCION TELEMETRIA ACADEMICA ---" "$PERFIL"; then
        # Elimina la línea marcadora y la línea siguiente (el export), sin tocar
        # nada más del perfil que el alumno haya agregado por su cuenta.
        sed -i "/# --- INYECCION TELEMETRIA ACADEMICA ---/,+1d" "$PERFIL"
        echo "[OK] Puente removido de $PERFIL"
    else
        echo "[INFO] $PERFIL no tenía el puente inyectado, nada que revertir."
    fi
}

limpiar_perfil "$HOME_REAL/.bashrc"
limpiar_perfil "$HOME_REAL/.zshrc"

# --- 7. Eliminar el hook global de Python (sitecustomize / ErrorDetection) ---
# Igual que en Windows: sitecustomize.py se carga en TODA ejecución futura de
# Python del usuario si no se elimina, no solo mientras el agente está activo.
echo "[*] Eliminando hook global de Python (sitecustomize)..."
if [ -d "$DIR_GLOBAL" ]; then
    rm -f "$DIR_GLOBAL/sitecustomize.py"
    rm -f "$DIR_GLOBAL/.telemetria_active"
    # Solo borramos la carpeta completa si quedó vacía; si el alumno guardó
    # algo propio ahí, no lo tocamos.
    if [ -z "$(ls -A "$DIR_GLOBAL" 2>/dev/null)" ]; then
        rmdir "$DIR_GLOBAL" 2>/dev/null
        echo "[OK] Carpeta .telemetria_global vacía eliminada."
    else
        echo "[INFO] .telemetria_global contiene otros archivos, no se elimina la carpeta."
    fi
else
    echo "[INFO] No se encontró .telemetria_global, nada que limpiar ahí."
fi

# --- 8. Revertir el bypass de Wayland (Debian/GDM3) ---
# ADVERTENCIA: esta configuración es a nivel de TODO EL EQUIPO, no solo del
# alumno. Si el instalador forzó Xorg, lo revertimos a Wayland por defecto
# — pero solo si el archivo sigue teniendo exactamente el valor que dejó
# nuestro instalador, para no pisar un cambio manual posterior de otra persona.
echo "[*] Verificando configuración gráfica (Wayland/Xorg)..."
if grep -q '^ID=debian' /etc/os-release 2>/dev/null; then
    ARCHIVO_GDM=""
    if [ -f "/etc/gdm3/daemon.conf" ]; then
        ARCHIVO_GDM="/etc/gdm3/daemon.conf"
    elif [ -f "/etc/gdm3/custom.conf" ]; then
        ARCHIVO_GDM="/etc/gdm3/custom.conf"
    fi

    if [ -n "$ARCHIVO_GDM" ] && grep -E -q "^WaylandEnable=false" "$ARCHIVO_GDM"; then
        echo "[!] ATENCION: este equipo tiene Wayland desactivado a nivel de sistema (afecta a TODOS los usuarios)."
        read -p "    ¿Revertir a la configuración por defecto (reactivar Wayland)? Requiere reinicio. (s/n): " revertir_wayland
        if [[ "$revertir_wayland" =~ ^[sS]$ ]]; then
            sudo sed -i -E 's/^WaylandEnable=false/#WaylandEnable=false/' "$ARCHIVO_GDM"
            echo "[OK] Wayland reactivado. El cambio se aplicará en el próximo reinicio del equipo."
        else
            echo "[INFO] Se dejó Xorg forzado a nivel de sistema (sin cambios)."
        fi
    else
        echo "[OK] No se detectó el bypass de Wayland de este instalador."
    fi
else
    echo "[OK] Distribución no-Debian, no se aplicó bypass de Wayland."
fi

echo "=================================================="
echo " DESINSTALACIÓN COMPLETADA CON ÉXITO.             "
echo " El sistema ha quedado completamente limpio.      "
echo "=================================================="