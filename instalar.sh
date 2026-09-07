#!/bin/bash

echo "=================================================="
echo " Instalador de Telemetría Académica (Linux/macOS) "
echo "=================================================="

# 1. Detectar el usuario real y su carpeta Home (Evadiendo la trampa de sudo)
if [ -n "$SUDO_USER" ]; then
    USUARIO_REAL="$SUDO_USER"
    # Buscamos la ruta real del usuario en el sistema
    HOME_REAL=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    USUARIO_REAL="$USER"
    HOME_REAL="$HOME"
fi

DIR_GLOBAL="$HOME_REAL/.telemetria_global"
echo "[*] Creando directorio seguro en: $DIR_GLOBAL"
mkdir -p "$DIR_GLOBAL"
chmod -R 777 "$DIR_GLOBAL"

# 2. Inyectar el PYTHONPATH en los perfiles exactos del alumno
LINEA_EXPORT="export PYTHONPATH=\"$DIR_GLOBAL:\$PYTHONPATH\""

inyectar_perfil() {
    PERFIL="$1"
    # Si el archivo existe o estamos en Linux, intentamos inyectar
    if [ -f "$PERFIL" ] || [ -d "$HOME_REAL" ]; then
        touch "$PERFIL" 2>/dev/null
        if ! grep -q "$DIR_GLOBAL" "$PERFIL"; then
            echo "" >> "$PERFIL"
            echo "# --- INYECCION TELEMETRIA ACADEMICA ---" >> "$PERFIL"
            echo "$LINEA_EXPORT" >> "$PERFIL"
            # Nos aseguramos de que el archivo siga siendo del usuario y no de root
            chown $USUARIO_REAL:$USUARIO_REAL "$PERFIL"
            echo "[OK] Puente inyectado automáticamente en $PERFIL"
        else
            echo "[INFO] El puente ya estaba configurado en $PERFIL"
        fi
    fi
}

# Aplicamos la inyección para Bash y ZSH
inyectar_perfil "$HOME_REAL/.bashrc"
inyectar_perfil "$HOME_REAL/.zshrc"

# 3. Reparar permisos de la carpeta del Cliente (Solución al Error 13)
echo "[*] Reparando permisos de la carpeta del proyecto..."

# Detectamos al usuario real (incluso si ejecutó el script con sudo)
USUARIO_REAL=${SUDO_USER:-$USER}

# Apuntamos específicamente a la subcarpeta Client
CARPETA_CLIENTE="$(pwd)/Client"

if [ -d "$CARPETA_CLIENTE" ]; then
    # Devolvemos la propiedad de toda la carpeta Client al usuario actual
    sudo chown -R $USUARIO_REAL:$USUARIO_REAL "$CARPETA_CLIENTE"

    # Nos aseguramos de que el archivo log exista y le damos permisos
    LOG_AUDITORIA="$CARPETA_CLIENTE/modules/error_detection/auditoria_python.log"
    mkdir -p "$(dirname "$LOG_AUDITORIA")"
    touch "$LOG_AUDITORIA"
    sudo chmod 666 "$LOG_AUDITORIA"
    echo "[OK] Permisos restaurados en $CARPETA_CLIENTE."
else
    echo "[!] Error: No se encontró la carpeta $CARPETA_CLIENTE."
    echo "[!] Asegúrate de ejecutar este script desde la raíz del proyecto."
fi

# 4. Crear entorno virtual e instalar dependencias
echo "[*] Creando entorno virtual aislado (venv)..."
python3 -m venv venv

if [ -f "requirements.txt" ]; then
    echo "[*] Instalando dependencias desde requirements.txt..."
    ./venv/bin/pip install --upgrade pip > /dev/null 2>&1
    ./venv/bin/pip install -r requirements.txt > /dev/null 2>&1
    echo "[OK] Dependencias instaladas."
else
    echo "[AVISO] No se encontró 'requirements.txt'. Omitiendo instalación de librerías."
fi

# Asegurar herramientas X11 del sistema (xhost)
if ! command -v xhost >/dev/null 2>&1; then
    echo "[*] Instalando x11-xserver-utils para control de acceso X11..."
    sudo apt-get update -y > /dev/null 2>&1 && sudo apt-get install -y x11-xserver-utils > /dev/null 2>&1 || true
fi

# 5. Configurar inicio automático anclado a la interfaz gráfica (Persistencia X11)
echo "[*] Configurando persistencia avanzada anclada a la sesión del usuario..."
DIR_ACTUAL=$(pwd)

# A) Limpiar restos de cron si existían de pruebas anteriores
(sudo crontab -u root -l 2>/dev/null | grep -v "ejecutar") | sudo crontab -u root - 2>/dev/null

# B) Crear el pase VIP para evitar que pida contraseña al reiniciar y preservar entorno X11
echo "[*] Configurando privilegios de ejecución silenciosa (sudoers)..."
cat << EOF | sudo tee /etc/sudoers.d/integriti_agent > /dev/null
Defaults!$DIR_ACTUAL/ejecutar.sh env_keep += "DISPLAY XAUTHORITY"
Defaults!$DIR_ACTUAL/venv/bin/python env_keep += "DISPLAY XAUTHORITY"
Defaults!$DIR_ACTUAL/venv/bin/python3 env_keep += "DISPLAY XAUTHORITY"
$USUARIO_REAL ALL=(ALL) NOPASSWD: SETENV: $DIR_ACTUAL/ejecutar.sh
$USUARIO_REAL ALL=(ALL) NOPASSWD: SETENV: $DIR_ACTUAL/venv/bin/python
$USUARIO_REAL ALL=(ALL) NOPASSWD: SETENV: $DIR_ACTUAL/venv/bin/python3
EOF
sudo chmod 0440 /etc/sudoers.d/integriti_agent

# Inyectar xhost en .xsessionrc del usuario para autorizar permanentemente a root en X11
XSESSIONRC="$HOME_REAL/.xsessionrc"
if ! grep -q "xhost +SI:localuser:root" "$XSESSIONRC" 2>/dev/null; then
    echo "xhost +SI:localuser:root >/dev/null 2>&1" >> "$XSESSIONRC"
    chown $USUARIO_REAL:$USUARIO_REAL "$XSESSIONRC"
    chmod 644 "$XSESSIONRC"
fi

# C) Crear el lanzador gráfico (Autostart)
echo "[*] Creando lanzador en el inicio de sesión del sistema..."
AUTOSTART_DIR="$HOME_REAL/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat << EOF > "$AUTOSTART_DIR/agente_telemetria.desktop"
[Desktop Entry]
Type=Application
Exec=/bin/bash -c "sleep 3 && sudo -E $DIR_ACTUAL/ejecutar.sh"
Terminal=false
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=IntegriTI
EOF

chmod +x "$AUTOSTART_DIR/agente_telemetria.desktop"
chown $USUARIO_REAL:$USUARIO_REAL "$AUTOSTART_DIR/agente_telemetria.desktop"

# Asegurar permisos de ejecución en scripts
chmod +x "$DIR_ACTUAL/ejecutar.sh" "$DIR_ACTUAL/desinstalar.sh" "$DIR_ACTUAL/instalar.sh"

echo "[OK] Persistencia gráfica configurada correctamente."
echo "[OK] Agente programado para arrancar automáticamente al iniciar sesión gráfica."

# 6. Iniciar inmediatamente el agente de telemetría
echo "[*] Iniciando el agente de telemetría de inmediato..."
"$DIR_ACTUAL/ejecutar.sh"

echo "=================================================="
echo " INSTALACIÓN COMPLETADA CON ÉXITO."
echo " El agente ya se encuentra operando en segundo plano."
echo " Se reiniciará de forma automática en cada inicio de sesión."
echo "=================================================="