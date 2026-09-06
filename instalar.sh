#!/bin/bash

echo "=================================================="
echo " Instalador de Telemetría Académica (Linux/macOS) "
echo "=================================================="

# 1. Crear el directorio oculto global con permisos totales
DIR_GLOBAL="$HOME/.telemetria_global"
echo "[*] Creando directorio seguro en: $DIR_GLOBAL"
mkdir -p "$DIR_GLOBAL"
chmod -R 777 "$DIR_GLOBAL"

# 2. Inyectar el PYTHONPATH en los perfiles de la terminal
LINEA_EXPORT="export PYTHONPATH=\"$DIR_GLOBAL:\$PYTHONPATH\""

inyectar_perfil() {
    PERFIL="$1"
    if [ -f "$PERFIL" ]; then
        if ! grep -q "$DIR_GLOBAL" "$PERFIL"; then
            echo "" >> "$PERFIL"
            echo "# --- INYECCION TELEMETRIA ACADEMICA ---" >> "$PERFIL"
            echo "$LINEA_EXPORT" >> "$PERFIL"
            echo "[OK] Puente inyectado en $PERFIL"
        else
            echo "[INFO] El puente ya estaba configurado en $PERFIL"
        fi
    fi
}

inyectar_perfil "$HOME/.bashrc"
inyectar_perfil "$HOME/.zshrc"

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

echo "=================================================="
echo " INSTALACIÓN COMPLETADA CON ÉXITO."
echo " IMPORTANTE: Cierra esta terminal por completo y"
echo " abre una nueva para recargar las variables."
echo " Luego, ejecuta './ejecutar.sh' para iniciar."
echo "=================================================="