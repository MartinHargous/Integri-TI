"""
Estado compartido de la aplicación Integri-TI.

Todas las variables mutables que antes vivían como globales de main.py ahora
viven aquí, como atributos de este módulo. Los routers las acceden vía
`import state` y referencian `state.nombre_variable`.

Regla práctica para quien edite esto:
- Para MUTAR un valor inmutable (strings, bools, ints) desde un router, hay
  que reasignar el atributo del módulo: `state.comando_global = "GRABANDO"`.
  Un `global comando_global` dentro de un router NO sirve — eso solo afecta
  al namespace del propio archivo del router, no a este módulo.
- Para contenedores mutables (dict, list, set, deque) basta con llamar sus
  métodos normales (`.clear()`, `.append()`, `[clave] = valor`, etc.), sin
  reasignar nada — eso funciona igual desde cualquier archivo.
- Las únicas funciones que SÍ reasignan nombres con `global` por dentro
  (como `inicializar_alertas_desde_historial`) viven aquí mismo, en el mismo
  módulo que esas variables, precisamente para que ese `global` sea válido.
"""
import os
import json
import time
import threading
from datetime import datetime
from collections import deque
from typing import Dict, Any, List

import pyotp
from fastapi.templating import Jinja2Templates

from comparator import LogComparator
from correlator import LogCorrelator
import database

# --- Rutas y configuración de archivos ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_DATOS = os.path.join(BASE_DIR, "datos_alumnos")
os.makedirs(CARPETA_DATOS, exist_ok=True)

RUTA_REGLAS = os.path.join(BASE_DIR, "reglas.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Inicializar Base de Datos SQLite (integri_ti.db)
database.inicializar_db(RUTA_REGLAS)

templates = Jinja2Templates(directory=TEMPLATES_DIR) if os.path.exists(TEMPLATES_DIR) else None
if templates:
    templates.env.auto_reload = True

# --- Motores de análisis (instancias únicas, compartidas por todos los routers) ---
correlador = LogCorrelator(RUTA_REGLAS)
comparador_logs = LogComparator(CARPETA_DATOS)

# --- PERSISTENCIA DEL ESTADO DEL SERVIDOR (comando_global + secretos_por_cliente) ---
# Sin esto, un reinicio del PROCESO del servidor (no del agente — eso ya lo
# resuelve el watchdog del lado del agente) pierde ambas cosas: el examen
# vuelve a ESPERANDO sin que nadie lo pidiera, y cada secreto_sync emitido
# deja de ser válido aunque el agente conserve su copia en su propio
# state.json — su reconexión por caché fallaría con 401 y tendría que volver
# a pasar por /api/discovery entero. Se guarda con escritura atómica (mismo
# patrón que usa client.py) en los tres puntos donde este estado realmente
# cambia: al emitir un secreto nuevo (routers/agent.py), al cambiar el
# comando del examen (routers/agent.py) y al resetear (routers/admin.py).
#
# Contiene los secretos de sincronización de todos los alumnos en texto
# plano — agregar server_state.json al .gitignore del repo, igual que ya se
# hacía antes con .otp_secret.
RUTA_STATE_SERVIDOR = os.path.join(BASE_DIR, "server_state.json")


def guardar_estado_servidor():
    tmp = RUTA_STATE_SERVIDOR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "comando_global": comando_global,
            "secretos_por_cliente": secretos_por_cliente,
        }, f)
    os.replace(tmp, RUTA_STATE_SERVIDOR)  # escritura atómica


def _cargar_estado_servidor():
    if not os.path.exists(RUTA_STATE_SERVIDOR):
        return "ESPERANDO", {}
    try:
        with open(RUTA_STATE_SERVIDOR, "r", encoding="utf-8") as f:
            datos = json.load(f)
        cmd = datos.get("comando_global", "ESPERANDO")
        if cmd not in ("ESPERANDO", "GRABANDO", "FINALIZADO"):
            cmd = "ESPERANDO"
        secretos = datos.get("secretos_por_cliente", {})
        if not isinstance(secretos, dict):
            secretos = {}
        print(f"[*] Estado del servidor restaurado desde {RUTA_STATE_SERVIDOR}: "
              f"comando_global={cmd}, {len(secretos)} secreto(s) de sincronización.")
        return cmd, secretos
    except Exception as e:
        print(f"[!] No se pudo leer {RUTA_STATE_SERVIDOR}, arrancando en blanco: {e}")
        return "ESPERANDO", {}


# --- Estado mutable del examen en curso ---
comando_global, secretos_por_cliente = _cargar_estado_servidor()  # ESPERANDO, {} si no hay archivo
comparador_logs.establecer_estado_examen(comando_global)
comparador_logs.iniciar_scheduler()

clientes_conectados: Dict[str, Any] = {}
historial_alertas: List[Dict[str, Any]] = []
configuraciones_pendientes: Dict[str, Any] = {}
configuraciones_globales: Dict[str, Any] = {}
eventos_telemetria_recientes = deque(maxlen=20)
_clientes_inicializados_desde_disco = False

# Umbral de silencio sospechoso durante GRABANDO. Con sync cada 15s por defecto,
# y hasta 60s de downtime máximo teórico del watchdog de reinicio del agente,
# se define el umbral considerando ese peor caso.
UMBRAL_GAP_SEGUNDOS = 60

# Alumnos ya notificados por el watchdog en vivo mientras siguen en silencio,
# para no repetir la misma alerta en cada ciclo de chequeo. Se limpia cuando
# el alumno reconecta (ver routers/agent.py) o cuando su estado deja de ser
# GRABANDO.
_alumnos_alertados_por_silencio = set()

# --- AUTENTICACIÓN EN DOS NIVELES ---
#
# 1. CÓDIGO DE DISTRIBUCIÓN (compuerta de /api/discovery): un secreto único,
#    horneado en el repo igual que en client.py, porque el despliegue real es
#    "clonar el mismo repo en el servidor y en cada PC de alumno". Su único
#    trabajo es frenar el escaneo oportunista de /api/discovery por parte de
#    quien NUNCA clonó este proyecto. Si el repo es público, alguien que SÍ lo
#    clonó lo conoce igual — no es la barrera real contra un alumno inscrito,
#    solo contra tráfico externo al azar. INTEGRITI_CODIGO_DISTRIBUCION por
#    variable de entorno permite rotarlo sin tocar el código.
CODIGO_DISTRIBUCION = os.environ.get("INTEGRITI_CODIGO_DISTRIBUCION", "3R2M3HZA7ZTUXSN54EDTUFLSMKWZRMU5")

# 2. SECRETO PERSONAL POR ALUMNO (/sync): se emite al vuelo en /api/discovery,
#    uno distinto por client_id, y NUNCA vive en el repo ni en ningún archivo
#    versionado EN GIT (sí se persiste localmente en server_state.json, fuera
#    de git, para sobrevivir un reinicio del proceso — ver arriba). Es la
#    barrera real: filtrar uno solo compromete a ese alumno, no a todo el
#    curso. Se limpia en /reset (routers/admin.py), así que cada examen nuevo
#    reparte secretos frescos — el de un examen anterior deja de servir.
# Emitir el mismo client_id dos veces (reconexión, reintento) devuelve el
# MISMO secreto mientras no haya habido un /reset — ver routers/agent.py.
# (La variable en sí ya quedó asignada arriba, junto con comando_global, al
# cargar el estado persistido — o en blanco si no hay archivo todavía.)

CONFIGS_POR_DEFECTO = {
    "sniffer": {"enabled": "true", "log_file": "sniffer.log", "method": "regex", "cooldown_seconds": "1"},
    "keylogger": {"enabled": "True", "log_file": "keylogger.log", "poll_seconds": "10"},
    "keystrokes svm": {"enabled": "True", "show_interface": "false", "log_file": "alerts.log", "train_chars": "100", "time_window": "60", "alert_threshold": "0.25", "max_hold_time": "0.5", "max_flight_time": "1.5", "svm_nu": "0.05", "svm_kernel": "rbf", "svm_gamma": "scale"},
    "error_detection": {"enabled": "True", "log_file": "auditoria_python.log", "sitecustomize_path": "", "capture_errors": "true", "capture_input": "true", "capture_print": "true", "monitor_poll_seconds": "0.5", "excluded_scripts": "pip,pip.exe,error_detection.py,manager_telemetria.py"},
    "paperclip": {"enabled": "True", "log_file": "paperclip.log", "poll_seconds": "0.5", "log_content": "true", "max_content_length": "1000"},
    "program monitor": {"enabled": "True", "log_file": "program_monitor.log", "poll_seconds": "1.0", "log_title_changes": "true"},
    "usb_detection": {"enabled": "true", "log_file": "usb_detection.log", "export_interval_seconds": "60", "export_file": "usb_export.log", "cooldown_seconds": "3.0", "shell_poll_seconds": "3.0", "monitored_extensions": ".pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.txt,.rtf,.odt,.csv,.jpg,.jpeg,.png,.gif,.webp,.bmp,.heic,.raw,.mp4,.mkv,.avi,.mov,.mp3,.wav,.m4a,.aac,.zip,.rar,.7z,.tar,.gz,.py,.java,.c,.cpp,.cs,.html,.css,.js,.ts,.sql,.sh,.bat,.ps1"}
}

def inicializar_alertas_desde_historial():
    """Analiza logs existentes en disco para poblar alertas reales iniciales."""
    global historial_alertas
    if os.path.exists(CARPETA_DATOS):
        for arch in os.listdir(CARPETA_DATOS):
            if arch.endswith(".log"):
                cid = arch[:-4]
                ruta = os.path.join(CARPETA_DATOS, arch)
                alertas_detectadas = correlador.analizar_archivo_completo(cid, ruta)
                if alertas_detectadas:
                    historial_alertas.extend(alertas_detectadas)
                    print(f"[*] {len(alertas_detectadas)} alertas históricas detectadas por reglas para {cid}")

        historial_alertas.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        if len(historial_alertas) > 100:
            historial_alertas = historial_alertas[:100]

inicializar_alertas_desde_historial()

def _inicializar_clientes_desde_disco():
    """Inicializa clientes y eventos una sola vez desde disco."""
    global clientes_conectados, eventos_telemetria_recientes
    if not os.path.exists(CARPETA_DATOS):
        return
    for arch in os.listdir(CARPETA_DATOS):
        if arch.endswith(".log"):
            cid = arch[:-4]
            ruta = os.path.join(CARPETA_DATOS, arch)
            if cid not in clientes_conectados:
                clientes_conectados[cid] = {
                    "estado": "ESPERANDO",
                    "ultimo_visto": "",
                    "ip": "127.0.0.1",
                    "bytes_recibidos": os.path.getsize(ruta) if os.path.exists(ruta) else 0,
                    "configs": {}
                }
            if len(eventos_telemetria_recientes) < 15:
                try:
                    with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
                        lineas = [l.strip() for l in f.readlines() if l.strip() and "--- IGNORE ---" not in l]
                        for linea in lineas[-2:]:
                            eventos_telemetria_recientes.append({
                                "client_id": cid,
                                "timestamp": "",
                                "nivel": "Info",
                                "mensaje": linea
                            })
                except Exception:
                    pass

# --- WATCHDOG DE SILENCIO EN VIVO ---
# Complementa la detección retroactiva de routers/agent.py: esa solo dispara
# CUANDO el agente logra reconectarse. Si el alumno nunca vuelve a conectarse
# (WiFi cortado el resto del examen, equipo apagado, etc.), ningún /sync futuro
# llega para comparar el gap. Este watchdog revisa activamente, sin depender
# de que el alumno vuelva a hablar, así el profesor lo ve en el momento.
def _watchdog_silencio_en_vivo():
    FRECUENCIA_CHEQUEO_SEG = 10
    while True:
        time.sleep(FRECUENCIA_CHEQUEO_SEG)
        ahora = datetime.now()
        for cid, info in list(clientes_conectados.items()):
            if info.get("estado") != "GRABANDO":
                _alumnos_alertados_por_silencio.discard(cid)
                continue
            try:
                ultimo_dt = datetime.strptime(info.get("ultimo_visto", ""), "%Y-%m-%dT%H:%M:%S")
            except Exception:
                continue

            gap_seg = (ahora - ultimo_dt).total_seconds()
            if gap_seg > UMBRAL_GAP_SEGUNDOS and cid not in _alumnos_alertados_por_silencio:
                _alumnos_alertados_por_silencio.add(cid)
                gap_int = int(gap_seg)
                print(f"\n[ALERTA SISTEMA - {cid}] SIN_CONEXION (en vivo) gap={gap_int}s")
                historial_alertas.insert(0, {
                    "client_id": cid,
                    "timestamp": ahora.strftime("%Y-%m-%dT%H:%M:%S"),
                    "nivel": "Alta",
                    "regla_id": "SISTEMA",
                    "regla_nombre": "Silencio Prolongado (en vivo)",
                    "mensaje": f"El agente de {cid} no responde hace {gap_int}s. Puede seguir desconectado (WiFi cortado, proceso terminado sin reiniciar, batería agotada, etc.)."
                })
                if len(historial_alertas) > 100:
                    historial_alertas.pop()

threading.Thread(target=_watchdog_silencio_en_vivo, daemon=True).start()