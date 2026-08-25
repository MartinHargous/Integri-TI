import os
import sys
from pathlib import Path
from modules.error_detection.main import ErrorDetection
from modules.keylogger.main import Keylogger
from modules.paperclip.main import Paperclip
from modules.program_monitor.main import ProgramMonitor
from modules.sniffer.main import Sniffer
from modules.svm_keystroke_dym.main import KeystrokeSVM
import threading
import ctypes
import re
class Orchestrator:
    def __init__(self):
        self.error_detection = ErrorDetection()
        self.keylogger = Keylogger()
        self.paperclip = Paperclip()
        self.program_monitor = ProgramMonitor()
        self.sniffer = Sniffer()
        self.keystroke_svm = KeystrokeSVM()
        self.os_type = sys.platform
        self.request_admin_if_needed()
        self.is_admin = True
        

    def request_admin_if_needed(self):      
        if self.os_type == "win32":
            try:
                is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                is_admin = False
                
            if not is_admin:
                print("[-] Solicitando permisos de administrador en Windows (UAC)...")
                script_path = os.path.abspath(sys.argv[0])
                
                ctypes.windll.shell32.ShellExecuteW(
                    None, 
                    "runas", 
                    sys.executable, 
                    f'"{script_path}"', 
                    None, 
                    1
                )
                sys.exit(0) 
                
        elif self.os_type in ("linux", "linux2", "darwin"):
            is_admin = os.geteuid() == 0
            
            if not is_admin:
                print("[-] Solicitando permisos de administrador (sudo)...")
                args = ["sudo", sys.executable] + sys.argv
                os.execvp("sudo", args)   
    def start_error_detection(self):
        if self.error_detection.is_installed():
            error_thread = threading.Thread(target=self.error_detection.start_monitor, daemon=True)
            error_thread.start()
        else:
            try:
                self.error_detection.install()
                error_thread = threading.Thread(target=self.error_detection.start_monitor, daemon=True)
                error_thread.start()
            except Exception as e:
                print(f"Error occurred while installing error detection: {e}")

    def start_keylogger(self):
        keylogger_thread = threading.Thread(target=self.keylogger.start, daemon=True)
        keylogger_thread.start()

    def start_paperclip(self):
        paperclip_thread = threading.Thread(target=self.paperclip.start, daemon=True)
        paperclip_thread.start()

    def start_program_monitor(self):
        program_monitor_thread = threading.Thread(target=self.program_monitor.start, daemon=True)
        program_monitor_thread.start()

    def start_sniffer(self):
        sniffer_thread = threading.Thread(target=self.sniffer.start_sniffing, daemon=True)
        sniffer_thread.start()

    def start_keystroke_svm(self):
        keystroke_svm_thread = threading.Thread(target=self.keystroke_svm.start, daemon=True)
        keystroke_svm_thread.start()

    def start_all(self):
        self.start_error_detection()
        self.start_keylogger()
        self.start_paperclip()
        self.start_program_monitor()
        self.start_sniffer()
        self.start_keystroke_svm()

    def stop_all(self):
        self.error_detection.stop_monitor()
        self.keylogger.stop()
        self.paperclip.stop()
        self.program_monitor.stop()
        self.sniffer.stop_sniffing()
        self.keystroke_svm.stop()
    def restart_module(self, module_name):
            import time
            print(f"\n[*] Reiniciando módulo '{module_name}' para aplicar cambios...")

            if module_name == "error_detection":
                self.error_detection.stop_monitor()
                time.sleep(1)  # Damos 1 segundo para que el hilo muera limpiamente
                self.error_detection = ErrorDetection()  # Crea una instancia fresca leyendo el config.txt
                self.start_error_detection()  # Lanza el nuevo hilo

            elif module_name == "sniffer":
                self.sniffer.stop_sniffing()
                time.sleep(1)
                self.sniffer = Sniffer()
                self.start_sniffer()

            elif module_name == "keystroke_svm":
                self.keystroke_svm.stop()
                time.sleep(1)
                self.keystroke_svm = KeystrokeSVM()
                self.start_keystroke_svm()

            elif module_name == "keylogger":
                self.keylogger.stop()
                time.sleep(1)
                self.keylogger = Keylogger()
                self.start_keylogger()
                
            elif module_name == "paperclip":
                self.paperclip.stop()
                time.sleep(1)
                self.paperclip = Paperclip()
                self.start_paperclip()

            elif module_name == "program_monitor":
                self.program_monitor.stop()
                time.sleep(1)
                self.program_monitor = ProgramMonitor()
                self.start_program_monitor()

            else:
                print(f"[ERROR] Módulo '{module_name}' no válido.")
                return

            print(f"[OK] Módulo '{module_name}' reiniciado y operando en segundo plano.\n")

    def change_config(self, module_name, key, value):
        module_map = {
            "error_detection": self.error_detection,
            "sniffer": self.sniffer,
            "keystroke_svm": self.keystroke_svm,
            "keylogger": self.keylogger,
            "paperclip": self.paperclip,
            "program_monitor": self.program_monitor
        }
        
        if module_name not in module_map:
            print(f"[ERROR] El módulo '{module_name}' no es válido.")
            return

        module = module_map[module_name]
        key_lower = key.lower()
        
        if key_lower not in module.config:
            print(f"[ERROR] La clave '{key}' no existe en {module_name}.")
            return

        module.config[key_lower] = str(value)

        try:
            content = module.config_path.read_text(encoding="utf-8-sig")
            new_lines = []
            
            for line in content.splitlines():
                if line.strip() and not line.startswith(("#", ";")) and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == key_lower:
                        new_lines.append(f"{k.strip()}={value}")
                        continue
                new_lines.append(line)
                
            module.config_path.write_text("\n".join(new_lines), encoding="utf-8")
            print(f"[OK] Archivo guardado: {module_name}.{key} = {value}")
            
        except Exception as e:
            print(f"[ERROR] No se pudo guardar en el archivo: {e}")

        self.restart_module(module_name)

    def get_config(self, module_name):
        module_map = {
            "error_detection": self.error_detection,
            "sniffer": self.sniffer,
            "keystroke_svm": self.keystroke_svm,
            "keylogger": self.keylogger,
            "paperclip": self.paperclip,
            "program_monitor": self.program_monitor
        }
        
        if module_name not in module_map:
            print(f"[ERROR] El módulo '{module_name}' no es válido.")
            return None

        module = module_map[module_name]
        return module.config

    def combine_logs(self):
        log_files = [
            self.error_detection.log_path,
            self.keylogger.log_path,
            self.paperclip.log_path,
            self.program_monitor.log_path,
            self.sniffer.log_path,
            self.keystroke_svm.log_path
        ]

        timestamp_re = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})\]\s*(.*)$")

        logs = []
        for log_file in log_files:
            if log_file.exists():
                modulo_nombre = log_file.stem.replace("_", " ").title()
                
                with log_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        match = timestamp_re.match(line)
                        if match:
                            logs.append((match.group(1), modulo_nombre, match.group(2)))
            else:
                print(f"[AVISO] El archivo {log_file} no existe. Omitiendo...")

        logs.sort()

        combined_log_path = Path("combined_log.log")
        with combined_log_path.open("w", encoding="utf-8") as combined_log:
            for timestamp, modulo, text in logs:
                combined_log.write(f"[{timestamp}][{modulo}] {text}\n")

        print(f"[OK] Logs combinados y ordenados en {combined_log_path}")

if __name__ == "__main__":
    try:
        orchestrator = Orchestrator()
        
        orchestrator.start_all()
        while True:
            comando = input("\nEscribe 'exit' para salir y detener todo: ").strip().lower()
            if comando == "exit":
                orchestrator.stop_all()
                break 

    except KeyboardInterrupt:
        print("\n[!] Interrupción de teclado detectada. Cerrando orquestador...")
        if 'orchestrator' in locals():
            orchestrator.stop_all()
        sys.exit(0)

    except Exception as e:
        print(f"\n[ERROR FATAL] Ocurrió una excepción inesperada: {e}")
        if 'orchestrator' in locals():
            orchestrator.stop_all()
        sys.exit(1) 




    