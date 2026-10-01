import os
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
 
# --- Estado mutable del examen en curso ---
comando_global = "ESPERANDO"  # El servidor DEBE iniciar en ESPERANDO
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
 
# --- AUTENTICACIÓN DE /sync (OTP compartido) ---
SECRETO_OTP = os.environ.get("INTEGRITI_OTP_SECRET", "IARB4YQKBW5NXX2BKJKK3XMHGT3SCXIK")
totp = pyotp.TOTP(SECRETO_OTP, interval=30)
 
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