import datetime
import os
import sys
import threading
import time
from pathlib import Path

import psutil


class ProgramMonitor:
    DEFAULTS = {
        "enabled": "true",
        "log_file": "program_monitor.log",
        "poll_seconds": "1.0",
        "log_title_changes": "true",
    }

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
        self.config = self._read_config()
        self.log_path = self._resolve_path(self.config["log_file"])
        self.os_type = sys.platform
        self.last_app = None
        self.last_window_title = None
        self.monitoring = False
        self._stop_event = threading.Event()
        self._monitor_thread = None

    def _read_config(self):
        values = self.DEFAULTS.copy()
        if self.config_path.exists():
            for raw_line in self.config_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", ";")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().lower()
                if key in values:
                    values[key] = value.strip()
        return values

    def _bool(self, key):
        return self.config[key].lower() in {"1", "true", "yes", "on"}

    def _resolve_path(self, value):
        path = Path(os.path.expandvars(os.path.expanduser(value)))
        if not path.is_absolute():
            path = self.config_path.parent / path
        return path.resolve()

    def _write_log(self, app_name, window_title):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        with self.log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] CONTEXT_CHANGE app={app_name!r} title={window_title!r}\n")

    def _check_context(self):
        app_name, window_title = self.get_active_window_info()
        app_changed = app_name != self.last_app
        title_changed = window_title != self.last_window_title
        if app_changed or (self._bool("log_title_changes") and title_changed):
            timestamp = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"[{timestamp}] CAMBIO DE CONTEXTO: {app_name} -> {window_title}")
            self._write_log(app_name, window_title)
        self.last_app = app_name
        self.last_window_title = window_title

    def get_active_window_info(self):
        try:
            if self.os_type == "win32":
                return self._get_windows_active()
            elif self.os_type == "darwin":
                return self._get_mac_active()
            elif self.os_type.startswith("linux"):
                return self._get_linux_active()
            else:
                return ("OS no soportado", "Desconocido")
        except Exception as error:
            return ("Error", str(error))

    def _get_windows_active(self):
        import win32gui
        import win32process
        
        # 1. Obtener el ID de la ventana gráfica en primer plano
        hwnd = win32gui.GetForegroundWindow()
        
        # 2. Obtener el PID (Process ID) asociado a esa ventana
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        
        # 3. Usar psutil para sacar el nombre del ejecutable real
        app_name = psutil.Process(pid).name()
        window_title = win32gui.GetWindowText(hwnd)
        
        return app_name, window_title

    def _get_mac_active(self):
        from AppKit import NSWorkspace
        
        # macOS maneja aplicaciones completas en primer plano
        active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = active_app.localizedName()
        
        # Nota: Obtener el título exacto de la pestaña/ventana en Mac requiere 
        # permisos de Accesibilidad muy estrictos. El nombre de la app suele bastar.
        return app_name, "Ventana Activa"

    def _get_linux_active(self):
        import subprocess
        
        # Usamos xdotool (requiere X11) para consultar el gestor de ventanas
        window_id = subprocess.check_output(['xdotool', 'getactivewindow']).decode().strip()
        window_title = subprocess.check_output(['xdotool', 'getwindowname', window_id]).decode().strip()
        
        # Para el PID usamos xprop
        pid_str = subprocess.check_output(['xprop', '-id', window_id, '_NET_WM_PID']).decode().strip()
        pid = int(pid_str.split("=")[-1].strip())
        
        app_name = psutil.Process(pid).name()
        return app_name, window_title

    def _monitor(self):
        while not self._stop_event.is_set():
            self._check_context()
            self._stop_event.wait(float(self.config["poll_seconds"]))

    def start(self):
        if not self._bool("enabled"):
            print("[AVISO] El monitor de contexto esta desactivado en config.txt.")
            return False
        if self.monitoring:
            return False
        self._stop_event.clear()
        self.monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()
        print(f"[OK] Monitor de contexto iniciado en {self.os_type}.")
        return True

    def stop(self):
        if not self.monitoring:
            return False
        self._stop_event.set()
        self.monitoring = False
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None
        print("[OK] Monitor de contexto detenido.")
        return True

    def run(self):
        if not self.start():
            return
        try:
            while self.monitoring:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()

    def iniciar_auditoria_contexto(self):
        self.run()

if __name__ == "__main__":
    monitor = ProgramMonitor()
    monitor.iniciar_auditoria_contexto()