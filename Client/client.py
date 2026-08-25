import os
import socket
import time
import requests
from pathlib import Path

# Importamos tu Orquestador
from orchestrator import Orchestrator

class TelemetryClient:
    DEFAULTS = {
        "sync_interval_seconds": "15" # Tiempo entre envíos al servidor
    }

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
        self.config = self._read_config()
        
        self.interval = float(self.config.get("sync_interval_seconds", self.DEFAULTS["sync_interval_seconds"]))
        
        # Estos valores se llenarán automáticamente al arrancar
        self.client_id = "Desconocido"
        self.server_url = ""
        
        self.estado_local = "ESPERANDO"
        self.conectado = False
        self.alertas_pendientes = []
        
        self.orchestrator = Orchestrator()

    def _read_config(self):
        values = self.DEFAULTS.copy()
        if self.config_path.exists():
            for raw_line in self.config_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", ";")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip().lower() in values:
                    values[key.strip().lower()] = value.strip()
        return values

    def _generar_client_id(self):
        """Extrae el usuario de Windows y el nombre del PC automáticamente."""
        try:
            usuario = os.getlogin()
            equipo = socket.gethostname()
            return f"{usuario}@{equipo}"
        except Exception:
            return "Alumno_Desconocido"

    def descubrir_servidor(self, puerto_escucha=9999):
        """Escucha en la red local hasta recibir el broadcast UDP del profesor."""
        print(f"\n[BÚSQUEDA] {self.client_id} está buscando al profesor en la red local...")
        
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # SO_REUSEADDR permite que varios scripts/alumnos escuchen en el mismo puerto sin chocar
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", puerto_escucha))
            s.settimeout(120) # Esperará hasta 2 minutos a que el profe encienda el server
            
            try:
                while True:
                    data, _ = s.recvfrom(1024)
                    mensaje = data.decode("utf-8")
                    
                    if mensaje.startswith("PROFESOR_API:"):
                        server_url = mensaje.split("PROFESOR_API:")[1].strip()
                        print(f"[OK] Profesor encontrado automáticamente en: {server_url}")
                        return server_url
                        
            except socket.timeout:
                print("[ERROR] Tiempo de espera agotado. No se encontró al profesor.")
                return None

    def iniciar_agente(self):
        print(f"\n[INFO] Iniciando agente. Sincronizando con {self.server_url} cada {self.interval}s...")
        
        try:
            self._loop_sincronizacion()
        except KeyboardInterrupt:
            print("\n[!] Interrupción detectada. Apagando agente cliente...")
            if self.estado_local == "GRABANDO":
                self.orchestrator.stop_all()

    def registrar_alerta(self, nivel, mensaje):
        alerta = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "nivel": nivel,
            "mensaje": mensaje
        }
        self.alertas_pendientes.append(alerta)

    def _loop_sincronizacion(self):
        while True:
            try:
                logs_pendientes = []
                if self.estado_local == "GRABANDO":
                    logs_pendientes = self.orchestrator.get_all_logs()

                payload = {
                    "client_id": self.client_id,
                    "estado_local": self.estado_local,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "logs": logs_pendientes,
                    "alertas": self.alertas_pendientes
                }
                
                respuesta = requests.post(f"{self.server_url}/sync", json=payload, timeout=5)
                
                if respuesta.status_code == 200:
                    if not self.conectado:
                        print("[OK] Conexión establecida y enviando telemetría.")
                        self.conectado = True
                        
                    datos_servidor = respuesta.json()
                    
                    if logs_pendientes:
                        print(f"[*] {len(logs_pendientes)} registros enviados al servidor correctamente.")
                        self.orchestrator.clear_all_logs()
                        
                    if self.alertas_pendientes:
                        print(f"[*] {len(self.alertas_pendientes)} alertas enviadas al servidor.")
                        self.alertas_pendientes.clear()
                        
                    comando_global = datos_servidor.get("comando_global")
                    self._procesar_comando(comando_global)
                    
                else:
                    print(f"[AVISO] El servidor respondió con error {respuesta.status_code}.")
                    
            except requests.exceptions.RequestException:
                if self.conectado:
                    print("[AVISO] Se perdió la conexión con el servidor. Reteniendo datos localmente...")
                    self.conectado = False
            
            time.sleep(self.interval)

    def _procesar_comando(self, estado_servidor):
        if not estado_servidor: 
            return
            
        if estado_servidor == "GRABANDO" and self.estado_local != "GRABANDO":
            print("\n🟢 Orden recibida: INICIAR TELEMETRÍA.")
            self.estado_local = "GRABANDO"
            self.orchestrator.start_all()
            
        elif estado_servidor == "FINALIZADO" and self.estado_local == "GRABANDO":
            print("\n🛑 Orden recibida: DETENER TELEMETRÍA.")
            self.estado_local = "FINALIZADO"
            self.orchestrator.stop_all()


if __name__ == "__main__":
    print("=" * 60)
    print(" Agente de Telemetría Estudiantil ")
    print("=" * 60)
    
    agente = TelemetryClient()
    
    # 1. Asignar ID automático (Ej: Martin@DESKTOP-UANDES)
    agente.client_id = agente._generar_client_id()
    
    # 2. Buscar al profesor en la red local
    url_profesor = agente.descubrir_servidor()
    
    if url_profesor:
        agente.server_url = url_profesor
        agente.iniciar_agente()
    else:
        print("\n[!] Saliendo. Inicia el servidor del profesor e intenta abrir este cliente de nuevo.")