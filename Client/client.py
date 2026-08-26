import os
import socket
import time
import requests
from pathlib import Path
import concurrent.futures
# Importamos tu Orquestador
from orchestrator import Orchestrator
import traceback

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

    def _obtener_ip_local(self):
        """Averigua la IP de este computador para saber en qué red estamos."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _probar_ip(self, ip_destino, puerto):
        """Intenta conectarse silenciosamente a una IP específica."""
        url = f"http://{ip_destino}:{puerto}"
        try:
            # Timeout cortísimo (0.5s) porque si está en la misma red, responde al instante
            respuesta = requests.get(f"{url}/api/status", timeout=0.5)
            if respuesta.status_code == 200:
                return url
        except Exception:
            pass
        return None

    def descubrir_servidor(self, puerto_api=8000):
        """Escanea la red local buscando la API del profesor."""
        mi_ip = self._obtener_ip_local()
        if mi_ip == "127.0.0.1":
            print("[ERROR] Estás desconectado del Wi-Fi/Red.")
            return None

        # Extraemos la base de la red (Ej: de "192.168.1.45" sacamos "192.168.1.")
        base_ip = ".".join(mi_ip.split(".")[:-1]) + "."
        print(f"\n[BÚSQUEDA] {self.client_id} escaneando la red {base_ip}x en busca del profesor...")

        # Generamos la lista de las 254 IPs posibles de la sala
        ips_a_probar = [f"{base_ip}{i}" for i in range(1, 255)]

        # Lanzamos 50 hilos al mismo tiempo para que revisen todas las IPs en un par de segundos
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            # Iniciamos todas las peticiones
            futuros = [executor.submit(self._probar_ip, ip, puerto_api) for ip in ips_a_probar]
            
            # A medida que van terminando, revisamos si alguna tuvo éxito
            for futuro in concurrent.futures.as_completed(futuros):
                resultado = futuro.result()
                if resultado:
                    print(f"[OK] Profesor encontrado automáticamente en: {resultado}")
                    return resultado # Encontramos al profe, detenemos la búsqueda
                    
        print("[ERROR] No se encontró ningún servidor activo en esta red.")
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
                    logs_pendientes = self.orchestrator.combine_logs()

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
                        self.orchestrator.combine_logs()
                        
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
            print("\nOrden recibida: INICIAR TELEMETRÍA.")
            self.estado_local = "GRABANDO"
            self.orchestrator.start_all()
            
        elif estado_servidor == "FINALIZADO" and self.estado_local == "GRABANDO":
            print("\nOrden recibida: DETENER TELEMETRÍA.")
            self.estado_local = "FINALIZADO"
            self.orchestrator.stop_all()


if __name__ == "__main__":
    print("=" * 60)
    print(" Agente de Telemetría Estudiantil ")
    print("=" * 60)
    
    try:
        agente = TelemetryClient()
        agente.client_id = agente._generar_client_id()
        url_profesor = agente.descubrir_servidor()
        
        if url_profesor:
            agente.server_url = url_profesor
            agente.iniciar_agente()
        else:
            input("\n[!] No se encontró el servidor. Presiona ENTER para salir...")
            
    except KeyboardInterrupt:
        pass # Salida limpia si el usuario presiona Ctrl+C
        
    except Exception as e:
        # Si algo explota, atrapamos el error aquí
        print("\n" + "!"*50)
        print("ERROR FATAL INESPERADO:")
        traceback.print_exc() # Imprime el error en la consola
        print("!"*50)
        
        # Guardamos el error en un archivo txt al lado del script
        with open("crash_log.txt", "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
            
        # Pausamos la consola para que no se cierre
        input("\nPresiona ENTER para cerrar esta ventana...")