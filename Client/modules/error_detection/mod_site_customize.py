import textwrap

MARKER_START = "# --- INICIO TELEMETRIA ---"
MARKER_END = "# --- FIN TELEMETRIA ---"

def _enabled(value):
    return value.lower() in {"1", "true", "yes", "on"}

def build_payload(log_path, flag_path, config):
    excluded = repr(tuple(
        item.strip() for item in config["excluded_scripts"].split(",") if item.strip()
    ))
    hook_code = []
    
    if _enabled(config["capture_errors"]):
        hook_code.append('''def _telemetry_excepthook(exc_type, exc_value, exc_tb):
    _log_telemetry(f"[CRASH DETECTADO] Excepcion: {exc_type.__name__} | Mensaje: {exc_value}")
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _telemetry_excepthook''')

    if _enabled(config["capture_input"]):
        hook_code.append('''_orig_input = builtins.input
def _telemetry_input(prompt=""):
    response = _orig_input(prompt)
    _log_telemetry(f"[INPUT] Prompt: {prompt!r} -> Respuesta: {response!r}")
    return response

builtins.input = _telemetry_input''')

    if _enabled(config["capture_print"]):
        hook_code.append('''_orig_print = builtins.print
def _telemetry_print(*args, **kwargs):
    mensaje = kwargs.get("sep", " ").join(str(arg) for arg in args)
    _log_telemetry(f"[PRINT] {mensaje}")
    _orig_print(*args, **kwargs)

builtins.print = _telemetry_print''')

    # Aumentamos la indentación a 12 espacios para que entre dentro del "if" del archivo final
    hooks = textwrap.indent("\n\n".join(hook_code), "            ")
    
    return f'''{MARKER_START}
import atexit
import builtins
import datetime
import os
import sys

LOG_FILE = {str(log_path)!r}
FLAG_FILE = {str(flag_path)!r}

# Sólo secuestra los eventos si el gestor encendió la bandera
if os.path.exists(FLAG_FILE):
    def _log_telemetry(msg):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{{timestamp}}] {{msg}}\\n")
        except Exception:
            pass

    if os.environ.get("TELEMETRIA_ACTIVA") != "1":
        os.environ["TELEMETRIA_ACTIVA"] = "1"
        script_path = sys.argv[0] if sys.argv else "Consola Interactiva"
        script_name = os.path.basename(script_path)
        
        if not script_name.endswith({excluded}):
            _log_telemetry(f"[INICIO] {{script_path}}")
{hooks}

            def _on_exit():
                _log_telemetry(f"[FIN] {{script_name}}")

            atexit.register(_on_exit)
{MARKER_END}
'''