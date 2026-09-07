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

# 5. Configurar persistencia pura con Systemd (User Service)
echo "[*] Configurando persistencia profesional con Systemd..."

DIR_ACTUAL=$(pwd)
# Usamos USUARIO_REAL y HOME_REAL detectados en el Paso 1
PYTHON_VENV="$DIR_ACTUAL/venv/bin/python"
SCRIPT_PYTHON="$DIR_ACTUAL/Client/client.py"

# A) Crear el pase VIP apuntando directamente a Python y tu script
echo "[*] Configurando privilegios de ejecución silenciosa (sudoers)..."
echo "$USUARIO_REAL ALL=(ALL) SETENV: NOPASSWD: $PYTHON_VENV $SCRIPT_PYTHON" | sudo tee /etc/sudoers.d/integriti_agent > /dev/null
sudo chmod 0440 /etc/sudoers.d/integriti_agent

# B) Crear el directorio de servicios de usuario asegurando que le pertenezca a kali
DIR_SYSTEMD_USER="$HOME_REAL/.config/systemd/user"
sudo -u $USUARIO_REAL mkdir -p "$DIR_SYSTEMD_USER"

# C) Crear el servicio puro de systemd
echo "[*] Creando servicio systemd..."
cat << EOF | sudo -u $USUARIO_REAL tee "$DIR_SYSTEMD_USER/integriti.service" > /dev/null
[Unit]
Description=Agente de Telemetria Integri-TI
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$DIR_ACTUAL/Client
# Inyectamos la variable para engañar a la telemetría de Python
Environment="SUDO_USER=$USUARIO_REAL"
# Lanzamos python con sudo conservando la pantalla gráfica (-E)
ExecStart=/usr/bin/sudo -E $PYTHON_VENV $SCRIPT_PYTHON
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

# D) Recargar systemd e iniciar el agente (Adaptado para sudo)
echo "[*] Iniciando el servicio en la sesión del usuario..."
USER_UID=$(id -u $USUARIO_REAL)

sudo -u $USUARIO_REAL XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user daemon-reload
sudo -u $USUARIO_REAL XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user enable integriti.service
sudo -u $USUARIO_REAL XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user start integriti.service

echo "[OK] Instalación completada. Agente corriendo en segundo plano."

echo "=================================================="
echo " INSTALACIÓN COMPLETADA CON ÉXITO."
echo " El agente ya se encuentra operando en segundo plano."
echo " Se reiniciará de forma automática en cada inicio de sesión."
echo "=================================================="