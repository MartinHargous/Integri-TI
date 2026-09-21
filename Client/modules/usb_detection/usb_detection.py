import os
import sys
import time
import threading
from threading import Timer
from pathlib import Path
from usbmonitor import USBMonitor

# Configuración de consola UTF-8 en Windows
if sys.platform.startswith('win'):
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Importar utilidades y vigilantes de plataforma
try:
    from .utils import (
        read_config, resolve_path, parse_bool, parse_extensions, classify_usb_device, is_smartphone,
        is_debounced, write_log_entry, write_event_entry, export_logs, PeriodicLogExporter
    )
    from . import windows
    from . import linux
except ImportError:
    from utils import (
        read_config, resolve_path, parse_bool, parse_extensions, classify_usb_device, is_smartphone,
        is_debounced, write_log_entry, write_event_entry, export_logs, PeriodicLogExporter
    )
    import windows
    import linux


class USBDetection:
    DEFAULTS = {
        "enabled": "true",
        "log_file": "usb_detection.log",
        "export_interval_seconds": "60",
        "export_file": "usb_export.log",
        "cooldown_seconds": "3.0",
        "shell_poll_seconds": "3.0",
        "monitored_extensions": ".pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.txt,.rtf,.odt,.csv,.jpg,.jpeg,.png,.gif,.webp,.bmp,.heic,.raw,.mp4,.mkv,.avi,.mov,.mp3,.wav,.m4a,.aac,.zip,.rar,.7z,.tar,.gz,.py,.java,.c,.cpp,.cs,.html,.css,.js,.ts,.sql,.sh,.bat,.ps1"
    }

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
        self.config = read_config(self.config_path, self.DEFAULTS)
        self.log_path = resolve_path(self.config_path, self.config["log_file"])
        self.export_path = resolve_path(self.config_path, self.config["export_file"])
        self.export_interval = float(self.config.get("export_interval_seconds", "60"))
        self.cooldown = float(self.config.get("cooldown_seconds", "3.0"))
        self.shell_poll_seconds = float(self.config.get("shell_poll_seconds", "3.0"))
        self.user_extensions = parse_extensions(self.config.get("monitored_extensions", self.DEFAULTS["monitored_extensions"]))

        self.last_alert_times = {}

        self.active_watchers = {}
        self.watchers_lock = threading.Lock()
        self.active_events = {}
        self.connected_devices = {}

        self.monitoring = False
        self._stop_event = threading.Event()
        self._exporter = None
        self._monitor = None

    def _bool(self, key):
        return parse_bool(self.config.get(key, self.DEFAULTS.get(key, "false")))

    def log_activity(self, event_type, device_type, device_name, details=""):
        """Registra un evento de actividad de hardware (conexión, desconexión, detección) en el log."""
        timestamp = write_event_entry(
            self.log_path,
            event_type=event_type,
            device_type=device_type,
            device_name=device_name,
            details=details
        )
        return timestamp

    def on_integrity_alert(self, device_type, device_name, file_path, action, process_info="Desconocido"):
        """Manejador central de alertas: filtra por debounce, escribe en el log de una línea y muestra en consola."""
        if not self.monitoring:
            return

        if is_debounced(self.last_alert_times, file_path, action, self.cooldown):
            return

        # 1. Registrar en el archivo de log en formato estricto de una línea
        timestamp = write_log_entry(
            self.log_path,
            device_type=device_type,
            device_name=device_name,
            file_path=file_path,
            action=action,
            process_info=process_info
        )

        # 2. Imprimir salida limpia por consola (sin emojis)
        clean_file = str(file_path).replace('\u200e', '').strip()
        clean_proc = str(process_info).replace('\u200e', '').strip()

        print("\n" + "=" * 70, flush=True)
        print(f"[ALERTA INTEGRIDAD] Acceso a archivo en dispositivo externo", flush=True)
        print(f"  - Dispositivo: {device_type} ({device_name})", flush=True)
        print(f"  - Archivo:     {clean_file}", flush=True)
        print(f"  - Acción:      {action}", flush=True)
        print(f"  - Proceso:     {clean_proc}", flush=True)
        print(f"  - Fecha/Hora:  {timestamp}", flush=True)
        print("=" * 70 + "\n", flush=True)

    def start_watchers_for_device(self, dev_key, dev_type, dev_name, target_path=None):
        """Asigna e inicia los vigilantes según la plataforma actual."""
        with self.watchers_lock:
            if not self.monitoring:
                return
            if dev_key in self.active_watchers:
                return
            if dev_type not in ('pendrive', 'smartphone'):
                return

            watchers = []
            if sys.platform.startswith('win'):
                watchers = windows.start_windows_watchers(
                    dev_type=dev_type,
                    dev_name=dev_name,
                    target_path=target_path,
                    on_alert_callback=self.on_integrity_alert,
                    user_extensions=self.user_extensions,
                    shell_poll_seconds=self.shell_poll_seconds
                )
            elif sys.platform.startswith('linux'):
                watchers = linux.start_linux_watchers(
                    dev_type=dev_type,
                    dev_name=dev_name,
                    target_path=target_path,
                    on_alert_callback=self.on_integrity_alert
                )

            self.active_watchers[dev_key] = watchers

    def stop_watchers_for_device(self, dev_key):
        """Detiene de forma segura los vigilantes asociados a un dispositivo."""
        with self.watchers_lock:
            if dev_key in self.active_watchers:
                print(f"[-] Deteniendo vigilancia para dispositivo: {dev_key}", flush=True)
                for w in self.active_watchers[dev_key]:
                    try:
                        w.stop()
                    except Exception:
                        pass
                del self.active_watchers[dev_key]

    def _process_debounced_event(self, hardware_signature, event_type, device_info):
        """Procesa conexiones y desconexiones físicas de hardware."""
        if not self.monitoring:
            return

        vid, pid = hardware_signature
        name = device_info.get('ID_MODEL', 'Dispositivo USB')
        dev_key = f"{vid}:{pid}"

        if event_type == 'connect':
            print(f"\n[+] USB FÍSICO CONECTADO -> VID: {vid} | PID: {pid} ({name})", flush=True)
            time.sleep(1.0)
            if not self.monitoring:
                return

            dev_type = "Dispositivo USB"
            mount_point = None
            details_list = [f"VID={vid}", f"PID={pid}"]

            vendor = device_info.get('ID_VENDOR_FROM_DATABASE') or device_info.get('ID_VENDOR')
            if vendor and vendor != name:
                details_list.append(f"Fabricante={vendor}")

            is_phone = is_smartphone(name, vendor, device_info)

            if sys.platform.startswith('win'):
                wpd_name = windows.get_windows_wpd_device_for_vid_pid(vid, pid)
                if is_phone or wpd_name:
                    dev_type = "Smartphone MTP"
                    final_dev_name = wpd_name or name
                    details_list.append(f"DispositivoMTP={final_dev_name}")
                    self.start_watchers_for_device(dev_key, 'smartphone', final_dev_name)
                else:
                    # Comprobar si es un Pendrive / Almacenamiento extraíble específico
                    mount_point = windows.get_drive_letter_for_usb(vid, pid)
                    if not mount_point and classify_usb_device(device_info, name) == "Pendrive":
                        mount_point = windows.get_windows_removable_drive()

                    if mount_point:
                        dev_type = "Pendrive"
                        details_list.append(f"Montaje={mount_point}")
                        self.start_watchers_for_device(dev_key, 'pendrive', name, mount_point)
                    else:
                        dev_type = classify_usb_device(device_info, name)
                        details_list.append("Sin unidad de almacenamiento montada")

            elif sys.platform.startswith('linux'):
                gvfs_mtp = linux.find_linux_mtp_mount()
                if is_phone or gvfs_mtp:
                    dev_type = "Smartphone MTP"
                    details_list.append(f"MontajeGVFS={gvfs_mtp or 'Detectado por hardware'}")
                    self.start_watchers_for_device(dev_key, 'smartphone', name, gvfs_mtp)
                else:
                    initial_m = device_info.get('initial_mounts')
                    mount_point = linux.find_linux_usb_mount(initial_mounts=initial_m, timeout=2.0)

                    if mount_point:
                        dev_type = "Pendrive"
                        details_list.append(f"Montaje={mount_point}")
                        self.start_watchers_for_device(dev_key, 'pendrive', name, mount_point)
                    else:
                        dev_type = classify_usb_device(device_info, name)
                        if dev_type == "Pendrive":
                            details_list.append("Esperando montaje en sistema...")
                            print(f"[AVISO] USB conectado ({name}), esperando montaje en el sistema...", flush=True)
                            def _wait_late_mount():
                                late_m = linux.find_linux_usb_mount(timeout=10.0)
                                if late_m and self.monitoring:
                                    self.log_activity(
                                        event_type="MONTAJE",
                                        device_type="Pendrive",
                                        device_name=name,
                                        details=f"VID={vid} | PID={pid} | Montaje={late_m}"
                                    )
                                    self.start_watchers_for_device(dev_key, 'pendrive', name, late_m)
                            threading.Thread(target=_wait_late_mount, daemon=True).start()
                        else:
                            details_list.append("Sin unidad de almacenamiento montada")

            details_str = " | ".join(details_list)
            self.connected_devices[dev_key] = {
                "type": dev_type,
                "name": name,
                "vid": vid,
                "pid": pid,
                "mount": mount_point,
                "details": details_str
            }
            # Registrar conexión física y tipo en el log
            self.log_activity(
                event_type="CONECTADO",
                device_type=dev_type,
                device_name=name,
                details=details_str
            )

        elif event_type == 'disconnect':
            print(f"\n[-] USB FÍSICO DESCONECTADO -> VID: {vid} | PID: {pid}", flush=True)
            prev_info = self.connected_devices.pop(dev_key, None)
            vendor = device_info.get('ID_VENDOR_FROM_DATABASE') or device_info.get('ID_VENDOR')
            
            if prev_info:
                dev_type = prev_info.get("type", "Dispositivo USB")
                dev_name = prev_info.get("name", name)
                if is_smartphone(dev_name, vendor, device_info):
                    dev_type = "Smartphone MTP"
            else:
                if is_smartphone(name, vendor, device_info):
                    dev_type = "Smartphone MTP"
                else:
                    dev_type = classify_usb_device(device_info, name)
                dev_name = name

            details = f"VID={vid} | PID={pid} | Dispositivo desconectado"

            # Registrar desconexión física y tipo en el log
            self.log_activity(
                event_type="DESCONECTADO",
                device_type=dev_type,
                device_name=dev_name,
                details=details
            )
            self.stop_watchers_for_device(dev_key)

        if hardware_signature in self.active_events:
            del self.active_events[hardware_signature]

    def _route_event(self, device_info, event_type):
        """Filtra y agrupa las señales lógicas recibidas de USBMonitor."""
        if not self.monitoring:
            return

        vid = device_info.get('ID_VENDOR_ID')
        pid = device_info.get('ID_MODEL_ID')
        if not vid or not pid:
            return

        if sys.platform.startswith('linux') and event_type == 'connect':
            device_info['initial_mounts'] = linux.get_linux_mount_points()

        hardware_signature = (vid, pid)
        if hardware_signature in self.active_events:
            try:
                self.active_events[hardware_signature]['timer'].cancel()
            except Exception:
                pass

        t = Timer(1.5, self._process_debounced_event, args=[hardware_signature, event_type, device_info])
        t.daemon = True
        self.active_events[hardware_signature] = {
            'type': event_type,
            'device_info': device_info,
            'timer': t
        }
        t.start()

    def check_existing_devices(self):
        """Comprueba e inicia vigilancia sobre dispositivos que ya estén conectados al momento de iniciar."""
        if not self.monitoring:
            return

        if sys.platform.startswith('win'):
            removable = windows.get_windows_removable_drive()
            if removable:
                dev_name = "Pendrive Extraíble"
                print(f"[*] Pendrive ya conectado detectado en: {removable}", flush=True)
                dev_key = "initial_pendrive"
                self.connected_devices[dev_key] = {
                    "type": "Pendrive",
                    "name": dev_name,
                    "mount": removable
                }
                self.log_activity(
                    event_type="CONECTADO (PREEXISTENTE)",
                    device_type="Pendrive",
                    device_name=dev_name,
                    details=f"Punto de montaje={removable}"
                )
                self.start_watchers_for_device(dev_key, 'pendrive', dev_name, removable)

            mtp_device = windows.get_windows_mtp_device()
            if mtp_device:
                print(f"[*] Smartphone MTP ya conectado detectado: {mtp_device}", flush=True)
                dev_key = "initial_smartphone"
                self.connected_devices[dev_key] = {
                    "type": "Smartphone MTP",
                    "name": mtp_device,
                    "mount": None
                }
                self.log_activity(
                    event_type="CONECTADO (PREEXISTENTE)",
                    device_type="Smartphone MTP",
                    device_name=mtp_device,
                    details="Detectado en Este Equipo (MTP)"
                )
                self.start_watchers_for_device(dev_key, 'smartphone', mtp_device)

        elif sys.platform.startswith('linux'):
            gvfs_path = linux.find_linux_mtp_mount()
            if gvfs_path:
                print(f"[*] Smartphone MTP ya conectado en: {gvfs_path}", flush=True)
                dev_key = "initial_smartphone"
                self.connected_devices[dev_key] = {
                    "type": "Smartphone MTP",
                    "name": "Smartphone MTP",
                    "mount": gvfs_path
                }
                self.log_activity(
                    event_type="CONECTADO (PREEXISTENTE)",
                    device_type="Smartphone MTP",
                    device_name="Smartphone MTP",
                    details=f"Montaje GVFS={gvfs_path}"
                )
                self.start_watchers_for_device(dev_key, 'smartphone', "Smartphone MTP", gvfs_path)

            current_mounts = linux.get_linux_mount_points()
            for m in current_mounts:
                if m.startswith(('/media/', '/run/media/', '/mnt/')):
                    parts = [p for p in m.split('/') if p]
                    if len(parts) >= 3 or m.startswith('/mnt/'):
                        label = os.path.basename(m)
                        dev_name = f"Pendrive ({label})"
                        print(f"[*] Pendrive ya conectado detectado en: {m} ({label})", flush=True)
                        dev_key = f"initial_pendrive_{m}"
                        self.connected_devices[dev_key] = {
                            "type": "Pendrive",
                            "name": dev_name,
                            "mount": m
                        }
                        self.log_activity(
                            event_type="CONECTADO (PREEXISTENTE)",
                            device_type="Pendrive",
                            device_name=dev_name,
                            details=f"Punto de montaje={m}"
                        )
                        self.start_watchers_for_device(dev_key, 'pendrive', dev_name, m)

    def start(self):
        """Inicia el módulo de detección USB y el exportador periódico."""
        self.config = read_config(self.config_path, self.DEFAULTS)
        if not self._bool("enabled"):
            print("[AVISO] Módulo de detección USB desactivado en config.txt.")
            return False

        if self.monitoring:
            return False

        self._stop_event.clear()
        self.monitoring = True

        print("=" * 70, flush=True)
        print("--- Módulo de Detección USB y Celulares Iniciado ---", flush=True)
        print("Modo: On-Access (Alertas en tiempo real al abrir, leer o copiar archivos)", flush=True)
        print(f"Log: {self.log_path}", flush=True)
        if self.export_interval > 0:
            print(f"Exportación automática: Cada {self.export_interval}s -> {self.export_path}", flush=True)
        print("=" * 70, flush=True)

        # Registrar inicio de monitoreo en el log
        self.log_activity("INICIO", "Modulo", "usb_detection", "Monitoreo de actividad USB iniciado")

        # 1. Iniciar exportador periódico si está configurado
        if self.export_interval > 0:
            self._exporter = PeriodicLogExporter(
                log_path=self.log_path,
                export_path=self.export_path,
                interval_seconds=self.export_interval,
                stop_event=self._stop_event
            )
            self._exporter.start()

        # 2. Revisar dispositivos preexistentes
        try:
            self.check_existing_devices()
        except Exception as e:
            print(f"[ERROR] Error al revisar dispositivos preexistentes: {e}", flush=True)

        # 3. Iniciar USBMonitor
        try:
            self._monitor = USBMonitor()
            self._monitor.start_monitoring(
                on_connect=lambda dev_id, dev_info: self._route_event(dev_info, 'connect'),
                on_disconnect=lambda dev_id, dev_info: self._route_event(dev_info, 'disconnect')
            )
            print("[OK] Monitoreo de conexiones USB físicas activo.", flush=True)
        except Exception as e:
            print(f"[ERROR] No se pudo inicializar USBMonitor: {e}", flush=True)

        return True

    def stop(self):
        """Detiene limpiamente el módulo y exporta un último volcado de logs."""
        if not self.monitoring:
            if self._monitor:
                try:
                    self._monitor.stop_monitoring()
                except Exception:
                    pass
                self._monitor = None
            return False

        print("\nApagando módulo de detección USB...", flush=True)
        self.monitoring = False
        self._stop_event.set()

        # Registrar detención en el log
        self.log_activity("DETENIDO", "Modulo", "usb_detection", "Monitoreo de actividad USB detenido")

        # 1. Cancelar cualquier temporizador pendiente de debounce
        for event in list(self.active_events.values()):
            try:
                event['timer'].cancel()
            except Exception:
                pass
        self.active_events.clear()

        # 2. Detener USBMonitor de forma limpia
        if self._monitor:
            try:
                self._monitor.stop_monitoring()
            except Exception as e:
                print(f"[AVISO] Error al detener USBMonitor: {e}", flush=True)
            self._monitor = None

        # 3. Detener todos los vigilantes activos asociados a dispositivos
        with self.watchers_lock:
            for dev_key, watchers in list(self.active_watchers.items()):
                print(f"[-] Deteniendo vigilancia para dispositivo: {dev_key}", flush=True)
                for w in watchers:
                    try:
                        w.stop()
                    except Exception:
                        pass
            self.active_watchers.clear()
            self.connected_devices.clear()

        # 4. Volcado final de logs si la exportación está activada
        if self.export_interval > 0:
            export_logs(self.log_path, self.export_path)

        print("[OK] Módulo de detección USB detenido.", flush=True)
        return True

    def export_logs(self, destination=None):
        """Exporta manualmente el log actual a la ruta especificada o a la configurada."""
        dest = destination or self.export_path
        return export_logs(self.log_path, dest)

    def clear_logs(self):
        """Vacía el archivo de logs del módulo."""
        try:
            if self.log_path.exists():
                with open(self.log_path, "w", encoding="utf-8"):
                    pass
                print(f"[OK] Archivo de log vaciado: {self.log_path}")
        except Exception as e:
            print(f"[ERROR] No se pudo vaciar {self.log_path}: {e}")


if __name__ == "__main__":
    detector = USBDetection()
    detector.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        detector.stop()

