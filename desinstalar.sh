#!/bin/bash

echo "=================================================="
echo " Desinstalador de Telemetría Académica (Linux)    "
echo "=================================================="

systemctl --user stop integriti.service 2>/dev/null
systemctl --user disable integriti.service 2>/dev/null
rm -f "$HOME/.config/systemd/user/integriti.service"
sudo rm -f "/etc/sudoers.d/integriti_agent"
systemctl --user daemon-reload
# 6. Limpiar archivos residuales generados durante la ejecución
rm -f daemon_salida.log combined_log.log crash_log.txt agente.pid

echo "=================================================="
echo " DESINSTALACIÓN COMPLETADA CON ÉXITO.             "
echo " El sistema ha quedado completamente limpio.      "
echo "=================================================="