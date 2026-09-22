import os
import sys
import time
import datetime
import threading
import re
import shutil
from pathlib import Path

# Lock global para escritura concurrente en archivos de log
_log_lock = threading.Lock()
_debounce_lock = threading.Lock()

def parse_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "si"}

def parse_extensions(ext_string):
    """Parsea una cadena de extensiones separada por comas y devuelve un conjunto normalizado."""
    exts = set()
    for ext in str(ext_string).split(","):
        clean = ext.strip().lower()
        if clean:
            if not clean.startswith("."):
                clean = f".{clean}"
            exts.add(clean)
    return exts

SMARTPHONE_KEYWORDS = {
    'phone', 'smartphone', 'celular', 'telefono', 'teléfono', 'móvil', 'movil',
    'android', 'iphone', 'ipad', 'ios',
    'xiaomi', 'redmi', 'poco',
    'samsung', 'galaxy',
    'huawei', 'honor',
    'motorola', 'moto ',
    'pixel', 'nexus',
    'oneplus', 'oppo', 'vivo', 'realme',
    'sony xperia', 'xperia',
    'zte', 'alcatel', 'tcl', 'infinix', 'tecno',
    'mtp'
}

def is_smartphone(name="", vendor="", device_info=None):
    """
    Detecta si un dispositivo corresponde a un teléfono móvil / smartphone basándose en su nombre,
    fabricante o descriptores de interfaz USB (ej. MTP, PTP, Still Image).
    """
    name_l = (name or "").lower()
    vendor_l = (vendor or "").lower()
    text = f"{name_l} {vendor_l}"

    if any(k in text for k in SMARTPHONE_KEYWORDS):
        return True

    if device_info:
        model_db = str(device_info.get('ID_MODEL_FROM_DATABASE', '')).lower()
        vendor_db = str(device_info.get('ID_VENDOR_FROM_DATABASE', '')).lower()
        full_text = f"{text} {model_db} {vendor_db}"
        if any(k in full_text for k in SMARTPHONE_KEYWORDS):
            return True

        interfaces = str(device_info.get('ID_USB_INTERFACES', '')).lower()
        if 'class_06' in interfaces or 'wpd' in interfaces or 'ptp' in interfaces or 'mtp' in interfaces:
            return True

    return False

def classify_usb_device(device_info, name=""):
    """
    Determina con precisión la categoría de un dispositivo USB a partir de sus clases e interfaces,
    evitando catalogar periféricos (mouse, teclado, etc.) como smartphones o unidades de almacenamiento.
    """
    vendor = (device_info.get('ID_VENDOR_FROM_DATABASE') or device_info.get('ID_VENDOR') or '') if device_info else ''
    if is_smartphone(name, vendor, device_info):
        return "Smartphone MTP"

    cls = (device_info.get('ID_USB_CLASS_FROM_DATABASE') or '').lower() if device_info else ''
    name_lower = (name or (device_info.get('ID_MODEL') if device_info else '') or '').lower()
    interfaces = str(device_info.get('ID_USB_INTERFACES', '')).lower() if device_info else ''

    if any(k in cls for k in ['massstorage', 'storage']) or 'class_08' in interfaces or any(k in name_lower for k in ['pendrive', 'almacenamiento', 'flash disk', 'usb drive', 'cruzer', 'datatraveler', 'sandisk']):
        return "Pendrive"
    if any(k in cls for k in ['hid', 'hidclass']) or 'class_03' in interfaces or any(k in name_lower for k in ['entrada', 'mouse', 'teclado', 'keyboard', 'raton', 'trackpad', 'touchpad', 'pointer']):
        return "Dispositivo de entrada USB (HID)"
    if any(k in cls for k in ['media', 'audio', 'sound']) or 'class_01' in interfaces or any(k in name_lower for k in ['headset', 'headphone', 'auricular', 'microfono', 'speaker', 'audio', 'virtuoso']):
        return "Dispositivo de Audio USB"
    if any(k in cls for k in ['video', 'camera', 'image']) or 'class_0e' in interfaces or any(k in name_lower for k in ['webcam', 'camera', 'lifecam', 'video']):
        return "Cámara/Video USB"
    if any(k in cls for k in ['wireless', 'bluetooth']) or 'class_e0' in interfaces or any(k in name_lower for k in ['bluetooth', 'wireless adapter']):
        return "Adaptador Inalámbrico/Bluetooth USB"
    if 'hub' in cls or 'concentrador' in name_lower or 'root_hub' in name_lower:
        return "Concentrador USB"

    return "Dispositivo USB"

def read_config(config_path, defaults):
    """Lee y parsea un archivo de configuración clave=valor estilo Integri-TI."""
    values = defaults.copy()
    c_path = Path(config_path)
    if c_path.exists():
        try:
            for raw_line in c_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", ";")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key_clean = key.strip().lower()
                if key_clean in values:
                    values[key_clean] = value.strip()
        except Exception as e:
            print(f"[ERROR] No se pudo leer {config_path}: {e}")
    return values

def resolve_path(base_path, value):
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = Path(base_path).parent / path
    return path.resolve()

def is_debounced(last_alert_times, file_path, action, cooldown_seconds):
    """Verifica de forma segura si la alerta para este archivo/acción está dentro del tiempo de enfriamiento."""
    key = (str(file_path).lower(), str(action).lower())
    now = time.time()
    with _debounce_lock:
        if key in last_alert_times and (now - last_alert_times[key]) < cooldown_seconds:
            return True
        last_alert_times[key] = now
        return False

def format_one_line_log(timestamp, device_type, device_name, file_path, action, process_info):
    """Formatea la alerta en una única línea estructurada para compatibilidad con auditoria_python y combine_logs."""
    clean_file = str(file_path).replace('\u200e', '').replace('\n', ' ').replace('\r', ' ').strip()
    clean_proc = str(process_info).replace('\u200e', '').replace('\n', ' ').replace('\r', ' ').strip()
    clean_action = str(action).replace('\n', ' ').replace('\r', ' ').strip()
    clean_dev_type = str(device_type).replace('\n', ' ').replace('\r', ' ').strip()
    clean_dev_name = str(device_name).replace('\n', ' ').replace('\r', ' ').strip()

    return f"[{timestamp}] ALERTA_USB Dispositivo={clean_dev_type} ({clean_dev_name}) | Archivo={clean_file} | Accion={clean_action} | Proceso={clean_proc}\n"

def format_event_log(timestamp, event_type, device_type, device_name, details=""):
    """Formatea un evento de actividad de hardware (conexión, desconexión, detección) en una única línea estructurada."""
    clean_event = str(event_type).replace('\n', ' ').replace('\r', ' ').strip()
    clean_type = str(device_type).replace('\n', ' ').replace('\r', ' ').strip()
    clean_name = str(device_name).replace('\n', ' ').replace('\r', ' ').strip()
    clean_det = str(details).replace('\n', ' ').replace('\r', ' ').strip()

    return f"[{timestamp}] ACTIVIDAD_USB Estado={clean_event} | Tipo={clean_type} | Dispositivo={clean_name} | Detalle={clean_det}\n"

def write_log_entry(log_path, device_type, device_name, file_path, action, process_info="Desconocido"):
    """Escribe la notificación de acceso a archivo en formato de una línea en el archivo de log."""
    log_file = Path(log_path)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = format_one_line_log(timestamp, device_type, device_name, file_path, action, process_info)

        with _log_lock:
            with log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        return timestamp
    except Exception as e:
        print(f"[ERROR] Error al escribir en log {log_path}: {e}")
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def write_event_entry(log_path, event_type, device_type, device_name, details=""):
    """Escribe un evento de actividad de hardware (conexión, desconexión, detección) en el archivo de log."""
    log_file = Path(log_path)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = format_event_log(timestamp, event_type, device_type, device_name, details)

        with _log_lock:
            with log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        return timestamp
    except Exception as e:
        print(f"[ERROR] Error al escribir evento en log {log_path}: {e}")
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def export_logs(log_path, export_path):
    """Exporta de forma segura el log actual hacia la ruta de exportación."""
    source = Path(log_path)
    dest = Path(export_path)
    if not source.exists():
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock:
            shutil.copyfile(str(source), str(dest))
        timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        print(f"[{timestamp}] [INFO] Logs de USB exportados exitosamente a: {dest}")
        return True
    except Exception as e:
        print(f"[ERROR] No se pudo exportar logs de USB a {dest}: {e}")
        return False

class PeriodicLogExporter(threading.Thread):
    """Hilo demonio que exporta automáticamente los logs tras un intervalo de tiempo configurable."""
    def __init__(self, log_path, export_path, interval_seconds, stop_event):
        super().__init__(daemon=True)
        self.log_path = log_path
        self.export_path = export_path
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            # Esperar el intervalo configurado o salir si se activa stop_event
            if self.stop_event.wait(self.interval_seconds):
                break
            export_logs(self.log_path, self.export_path)

