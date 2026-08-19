import os
import site
import sys
import time
import threading

# Rutas del sistema
USER_SITE = site.getusersitepackages()
SITECUSTOMIZE_PATH = os.path.join(USER_SITE, "sitecustomize.py")
LOG_PATH = os.path.join(os.path.expanduser("~"), "auditoria_python.log")

MARKER_START = "# --- INICIO TELEMETRIA ---"
MARKER_END = "# --- FIN TELEMETRIA ---"

PAYLOAD = f"""
{MARKER_START}
import sys
import builtins
import atexit
import os
import datetime

# Ruta del archivo log
LOG_FILE = r"{LOG_PATH}"

def _log_telemetry(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{{timestamp}}] {{msg}}\\n")
    except Exception:
        pass

if os.environ.get("TELEMETRIA_ACTIVA") != "1":
    os.environ["TELEMETRIA_ACTIVA"] = "1"
    
    script_path = sys.argv[0] if sys.argv else "Consola Interactiva"
    script_name = os.path.basename(script_path)

    if not script_name.endswith(("pip", "pip.exe", "error_detection.py", "manager_telemetria.py")):
        _log_telemetry(f"[INICIO] {{script_path}}")

        # 1. HOOK DE ERRORES (CRASHES)
        def _telemetry_excepthook(exc_type, exc_value, exc_tb):
            _log_telemetry(f"[CRASH DETECTADO] Excepcion: {{exc_type.__name__}} | Mensaje: {{exc_value}}")
            sys.__excepthook__(exc_type, exc_value, exc_tb)

        sys.excepthook = _telemetry_excepthook

        # 2. HOOK DE INPUTS
        _orig_input = builtins.input
        def _telemetry_input(prompt=""):
            response = _orig_input(prompt)
            _log_telemetry(f"[INPUT] Prompt: {{prompt!r}} -> Respuesta: {{response!r}}")
            return response

        builtins.input = _telemetry_input

        # 3. HOOK DE PRINTS (NUEVO)
        _orig_print = builtins.print
        def _telemetry_print(*args, **kwargs):
            # Extraemos el separador que el usuario haya usado (por defecto es un espacio)
            sep = kwargs.get("sep", " ")
            # Convertimos todos los argumentos a texto y los unimos
            mensaje = sep.join(str(arg) for arg in args)
            
            _log_telemetry(f"[PRINT] {{mensaje}}")
            
            # Ejecutamos el print original para que se vea en la consola
            _orig_print(*args, **kwargs)

        builtins.print = _telemetry_print

        # 4. HOOK DE FINALIZACION
        def _on_exit():
            _log_telemetry(f"[FIN] {{script_name}}\\n")

        atexit.register(_on_exit)
{MARKER_END}
"""

# --- VARIABLES GLOBALES PARA EL HILO ASINCRONO ---
hilo_monitoreo = None
monitoreo_activo = False

def install():
    if not os.path.exists(USER_SITE):
        os.makedirs(USER_SITE)
        
    content = ""
    if os.path.exists(SITECUSTOMIZE_PATH):
        with open(SITECUSTOMIZE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            
    if MARKER_START in content:
        print("[AVISO] La telemetria ya esta instalada.")
        return

    with open(SITECUSTOMIZE_PATH, "a", encoding="utf-8") as f:
        f.write("\n" + PAYLOAD)
        
    print("[OK] Telemetria instalada.")

def uninstall():
    if not os.path.exists(SITECUSTOMIZE_PATH):
        print("[AVISO] La telemetria no esta instalada.")
        return

    with open(SITECUSTOMIZE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    with open(SITECUSTOMIZE_PATH, "w", encoding="utf-8") as f:
        in_marker = False
        for line in lines:
            if line.strip() == MARKER_START:
                in_marker = True
            if not in_marker:
                f.write(line)
            if line.strip() == MARKER_END:
                in_marker = False
                
    if os.path.getsize(SITECUSTOMIZE_PATH) == 0:
        os.remove(SITECUSTOMIZE_PATH)
        
    print("[OK] Telemetria DESINSTALADA.")

# --- LOGICA DEL HILO EN SEGUNDO PLANO ---
def _leer_log_en_fondo():
    global monitoreo_activo
    if not os.path.exists(LOG_PATH):
        open(LOG_PATH, 'a').close()

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        
        while monitoreo_activo:
            linea = f.readline()
            if linea:
                print(f"\n[EN VIVO] {linea.strip()}")
            else:
                time.sleep(0.5)

def toggle_monitor():
    global hilo_monitoreo, monitoreo_activo
    
    if monitoreo_activo:
        monitoreo_activo = False
        if hilo_monitoreo is not None:
            hilo_monitoreo.join(timeout=1.0)
        print("[INFO] Notificaciones en vivo DESACTIVADAS.")
    else:
        monitoreo_activo = True
        hilo_monitoreo = threading.Thread(target=_leer_log_en_fondo, daemon=True)
        hilo_monitoreo.start()
        print("[INFO] Notificaciones en vivo ACTIVADAS. Volviendo al menu...")

def main():
    global monitoreo_activo
    while True:
        estado_noti = "ENCENDIDO" if monitoreo_activo else "APAGADO"
        print("\n=== GESTOR DE TELEMETRIA DE CODIGO ===")
        print("1. Instalar telemetria silenciosa")
        print("2. Desinstalar telemetria")
        print(f"3. Toggle notificaciones en vivo (Estado actual: {estado_noti})")
        print("4. Salir")
        
        opcion = input("Selecciona (1-4): ")
        
        if opcion == "1": 
            install()
        elif opcion == "2": 
            uninstall()
        elif opcion == "3": 
            toggle_monitor()
        elif opcion == "4": 
            monitoreo_activo = False
            print("[INFO] Saliendo del gestor...")
            sys.exit(0)
        else:
            print("[AVISO] Opcion invalida.")

if __name__ == "__main__":
    main()