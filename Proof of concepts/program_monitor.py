import sys
import time
import psutil

class ForegroundMonitor:
    def __init__(self):
        self.os_type = sys.platform
        self.last_app = None

    def get_active_window_info(self):
        """Devuelve una tupla: (Nombre_del_Proceso, Titulo_de_la_Ventana)"""
        try:
            if self.os_type == "win32":
                return self._get_windows_active()
            elif self.os_type == "darwin":
                return self._get_mac_active()
            elif self.os_type.startswith("linux"):
                return self._get_linux_active()
            else:
                return ("OS no soportado", "Desconocido")
        except Exception as e:
            return ("Error", str(e))

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

    def iniciar_auditoria_contexto(self):
        print(f"Iniciando monitor de contexto en {self.os_type}...")
        print("Presiona Ctrl+C para detener.\n")
        
        try:
            while True:
                app_name, window_title = self.get_active_window_info()
                
                # Solo registramos cuando el usuario cambia de ventana
                # para no llenar la bitácora de datos repetidos
                if app_name != self.last_app:
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"[{timestamp}] CAMBIO DE CONTEXTO: {app_name} -> {window_title}")
                    
                    self.last_app = app_name
                
                # El daemon duerme 1 segundo para no consumir CPU
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nMonitor de contexto detenido.")

if __name__ == "__main__":
    monitor = ForegroundMonitor()
    monitor.iniciar_auditoria_contexto()