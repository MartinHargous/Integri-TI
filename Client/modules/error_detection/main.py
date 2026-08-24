import os
import site
import sys
import threading
import time
from pathlib import Path
try:
    from .mod_site_customize import MARKER_END, MARKER_START, build_payload
except ImportError:
    from mod_site_customize import MARKER_END, MARKER_START, build_payload


class ErrorDetection:
    DEFAULTS = {
        "enabled": "true",
        "log_file": "auditoria_python.log",
        "sitecustomize_path": "",
        "capture_errors": "true",
        "capture_input": "true",
        "capture_print": "true",
        "monitor_poll_seconds": "0.5",
        "excluded_scripts": "pip,pip.exe,error_detection.py,manager_telemetria.py",
    }

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
        self.config = self._read_config()
        self.monitoring = False
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

    def _path(self, value):
        path = Path(os.path.expandvars(os.path.expanduser(value)))
        if not path.is_absolute():
            path = self.config_path.parent / path
        return path.resolve()

    @property
    def log_path(self):
        return self._path(self.config["log_file"])

    @property
    def flag_path(self):
        # Archivo bandera que controla si la telemetría graba o no
        return self.log_path.parent / ".telemetry_active"

    @property
    def sitecustomize_path(self):
        configured = self.config["sitecustomize_path"]
        if configured:
            return self._path(configured)
        return Path(site.getusersitepackages()) / "sitecustomize.py"

    def is_installed(self):
        if not self.sitecustomize_path.exists():
            return False
        return MARKER_START in self.sitecustomize_path.read_text(encoding="utf-8") and MARKER_END in self.sitecustomize_path.read_text(encoding="utf-8")

    def status(self):
        return {
            "enabled": self._bool("enabled"),
            "installed": self.is_installed(),
            "sitecustomize_path": str(self.sitecustomize_path),
            "log_path": str(self.log_path),
        }

    def install(self):
        if not self._bool("enabled"):
            print("[AVISO] La telemetria esta desactivada en config.txt.")
            return False
        path = self.sitecustomize_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        if MARKER_START in content:
            print("[AVISO] La telemetria ya esta instalada.")
            return False
        
        # Le pasamos la ruta del archivo bandera al payload
        payload = build_payload(self.log_path, self.flag_path, self.config)
        path.write_text(content + "\n" + payload, encoding="utf-8")
        print(f"[OK] Telemetria instalada en {path}.")
        return True

    def uninstall(self):
        path = self.sitecustomize_path
        if not path.exists():
            print("[AVISO] La telemetria no esta instalada.")
            return False
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        filtered = []
        inside = False
        for line in lines:
            if line.strip() == MARKER_START:
                inside = True
            if not inside:
                filtered.append(line)
            if line.strip() == MARKER_END:
                inside = False
        new_content = "".join(filtered)
        if new_content.strip():
            path.write_text(new_content, encoding="utf-8")
        else:
            path.unlink()
            
        # Limpiamos la bandera si desinstalamos
        if self.flag_path.exists():
            self.flag_path.unlink()
            
        print("[OK] Telemetria desinstalada.")
        return True

    def _follow_log(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)
        with self.log_path.open("r", encoding="utf-8") as log_file:
            log_file.seek(0, os.SEEK_END)
            while self.monitoring:
                line = log_file.readline()
                if line:
                    print(f"\n[EN VIVO] {line.strip()}")
                else:
                    time.sleep(float(self.config["monitor_poll_seconds"]))

    def stop_monitor(self):
        if not self.monitoring:
            return False
        self.monitoring = False
        
        # ELIMINA el archivo bandera, deteniendo el registro en los scripts
        if self.flag_path.exists():
            self.flag_path.unlink()
            
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None
        print("[INFO] Telemetría y notificaciones DETENIDAS.")
        return True

    def start_monitor(self):
        if self.monitoring:
            return False
            
        # CREA el archivo bandera, permitiendo que los scripts graben
        self.flag_path.touch(exist_ok=True)
        
        self.monitoring = True
        self._monitor_thread = threading.Thread(target=self._follow_log, daemon=True)
        self._monitor_thread.start()
        print("[INFO] Telemetría y notificaciones ACTIVADAS.")
        return True

    def run(self):
        actions = {"1": self.install, "2": self.uninstall, "3": self.start_monitor, "4": self.stop_monitor}
        while True:
            state = "ENCENDIDO" if self.monitoring else "APAGADO"
            print("\n=== GESTOR DE TELEMETRIA DE CODIGO ===")
            print("1. Instalar telemetria")
            print("2. Desinstalar telemetria")
            print(f"3. Iniciar registro de logs (Estado: {state})")
            print("4. Detener registro de logs")
            print("5. Salir")
            option = input("Selecciona (1-5): ").strip()
            
            if option in actions:
                actions[option]()
            elif option == "5":
                self.monitoring = False
                if self.flag_path.exists():
                    self.flag_path.unlink() # Asegurar apagado al salir
                print("[INFO] Saliendo del gestor...")
                return
            else:
                print("[AVISO] Opcion invalida.")

if __name__ == "__main__":
    ErrorDetection().run()