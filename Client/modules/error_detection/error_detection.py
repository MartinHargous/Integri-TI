import os
import site
import sys
import threading
import time
import subprocess
from pathlib import Path

try:
    from .mod_site_customize import MARKER_END, MARKER_START, build_payload
except ImportError:
    from mod_site_customize import MARKER_END, MARKER_START, build_payload

class ErrorDetection:
    DEFAULTS = {
        "enabled": "true",
        "log_file": "auditoria_python.log",
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
        return self.log_path.parent / ".telemetry_active"

    @property
    def sitecustomize_path(self):
        # 1. FORZAMOS una carpeta global oculta en el perfil del usuario para cualquier OS
        target_dir = Path.home() / ".telemetria_global"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / "sitecustomize.py"

    def _configurar_variables_entorno(self):
        """Inyecta el PYTHONPATH en el sistema operativo automáticamente."""
        target_dir = str(self.sitecustomize_path.parent)
        
        if sys.platform == "win32":
            # Automatización en WINDOWS
            current_pp = os.environ.get('PYTHONPATH', '')
            if target_dir not in current_pp:
                new_pp = f"{target_dir};{current_pp}" if current_pp else target_dir
                try:
                    subprocess.run(['setx', 'PYTHONPATH', new_pp], capture_output=True, check=True)
                    print(f"[OK] Variable de entorno inyectada en Windows mediante setx.")
                except Exception as e:
                    print(f"[ERROR] No se pudo fijar PYTHONPATH en Windows: {e}")
        else:
            # Automatización en LINUX / MAC
            linea_export = f'\nexport PYTHONPATH="{target_dir}:$PYTHONPATH"\n'
            for rc_file in [".bashrc", ".zshrc", ".bash_profile"]:
                rc_path = Path.home() / rc_file
                if rc_path.exists():
                    content = rc_path.read_text(encoding="utf-8")
                    if target_dir not in content:
                        with rc_path.open("a", encoding="utf-8") as f:
                            f.write(linea_export)
                        print(f"[OK] Archivo {rc_file} actualizado automáticamente.")

    def _limpiar_variables_entorno(self):
        """Limpia el PYTHONPATH en Linux/Mac automáticamente (Windows es mejor manual)."""
        target_dir = str(self.sitecustomize_path.parent)
        if sys.platform != "win32":
            for rc_file in [".bashrc", ".zshrc", ".bash_profile"]:
                rc_path = Path.home() / rc_file
                if rc_path.exists():
                    lines = rc_path.read_text(encoding="utf-8").splitlines(keepends=True)
                    new_lines = [line for line in lines if target_dir not in line]
                    if len(lines) != len(new_lines):
                        rc_path.write_text("".join(new_lines), encoding="utf-8")
                        print(f"[OK] Variable de entorno limpiada de {rc_file}.")

    def is_installed(self):
        if not self.sitecustomize_path.exists():
            return False
        return MARKER_START in self.sitecustomize_path.read_text(encoding="utf-8")

    def install(self):
        if not self._bool("enabled"):
            print("[AVISO] La telemetria esta desactivada en config.txt.")
            return False
            
        path = self.sitecustomize_path
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        
        if MARKER_START in content:
            print("[AVISO] La telemetria ya esta instalada.")
            return False
        
        payload = build_payload(self.log_path, self.flag_path, self.config)
        path.write_text(content + "\n" + payload, encoding="utf-8")
        
        # 2. ACTIVAMOS LA TRAMPA EN EL OS AL INSTALAR
        self._configurar_variables_entorno()
        
        print(f"[OK] Telemetria instalada globalmente en {path}.")
        if sys.platform != "win32":
            print("[!] IMPORTANTE: Ejecuta 'source ~/.bashrc' o reinicia tu terminal para aplicar.")
        else:
            print("[!] IMPORTANTE: Reinicia tu terminal (CMD/PowerShell) para aplicar.")
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
            
        if self.flag_path.exists():
            self.flag_path.unlink()
            
        # 3. LIMPIAMOS LA TRAMPA AL DESINSTALAR
        self._limpiar_variables_entorno()
            
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
                    sys.__stdout__.write(f"\n[EN VIVO] {line.strip()}\n")
                    sys.__stdout__.flush()
                else:
                    time.sleep(float(self.config["monitor_poll_seconds"]))

    def stop_monitor(self):
        if not self.monitoring:
            return False
        self.monitoring = False
        
        if self.flag_path.exists():
            self.flag_path.unlink()
            
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None
        print("[INFO] Telemetría y notificaciones DETENIDAS.")
        return True

    def start_monitor(self):
        if not self._bool("enabled"):
            print("[AVISO] La telemetria esta desactivada en config.txt.")
            if self.flag_path.exists():
                self.flag_path.unlink()
            return False
        if self.monitoring:
            return False
            
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
            print("1. Instalar telemetria (Modo Global Automático)")
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
                    self.flag_path.unlink()
                print("[INFO] Saliendo del gestor...")
                return
            else:
                print("[AVISO] Opcion invalida.")

if __name__ == "__main__":
    ErrorDetection().run()