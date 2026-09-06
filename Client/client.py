import os
import socket
import time
import requests
import json
from pathlib import Path
import concurrent.futures
import traceback

# Importamos tu Orquestador
from orchestrator import Orchestrator

class TelemetryClient:
    DEFAULTS = {
        "sync_interval_seconds": "15",
        "discovery_timeout_seconds": "0", # 0 = Modo Daemon (búsqueda infinita)
        "server_ip": ""
    }

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
        self.config = self._read_config()
        
        self.interval = float(self.config.get("sync_interval_seconds", self.DEFAULTS["sync_interval_seconds"]))
        self.discovery_timeout = float(self.config.get("discovery_timeout_seconds", self.DEFAULTS["discovery_timeout_seconds"]))
        
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
        try:
            usuario = os.getlogin()
            equipo = socket.gethostname()
            return f"{usuario}@{equipo}"
        except Exception:
            return "Alumno_Desconocido"

    def _obtener_ip_local(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _probar_ip(self, ip_destino, puerto):
        url = f"http://{ip_destino}:{puerto}"
        try:
            # 1. Probar endpoint de descubrimiento: solo responde 200 si el servidor está en ESPERANDO
            respuesta = requests.get(f"{url}/api/discovery", timeout=0.5)
            if respuesta.status_code == 200:
                return url
            elif respuesta.status_code == 403:
                # Servidor en FINALIZADO u otro estado que no acepta conexiones nuevas
                return None
            
            # 2. Compatibilidad con /api/status si /api/discovery no estuviese disponible
            resp_status = requests.get(f"{url}/api/status", timeout=0.5)
            if resp_status.status_code == 200:
                data = resp_status.json()
                if data.get("comando_global") == "ESPERANDO":
                    return url
        except Exception:
            pass
        return None

    def descubrir_servidor(self, puerto_api=8000):
        """Escanea la red buscando la API, cruzando barreras NAT si es necesario."""
        
        # 1. Bypass manual: Si se configuró una IP explícita en config.txt
        ip_forzada = self.config.get("server_ip", "")
        if ip_forzada:
            print(f"[BÚSQUEDA] Probando IP forzada desde configuración: {ip_forzada}...")
            url = self._probar_ip(ip_forzada, puerto_api)
            if url:
                print(f"[OK] Profesor encontrado en IP configurada: {url}")
                return url
            print("[AVISO] La IP forzada no respondió. Pasando a búsqueda automática...")

        # 2. Escaneo automático masivo
        start_time = time.time()
        intento = 1
        
        while True:
            mi_ip = self._obtener_ip_local()
            
            if mi_ip == "127.0.0.1":
                print(f"[BÚSQUEDA - Intento {intento}] Sin red detectada. Esperando...")
            else:
                base_local = ".".join(mi_ip.split(".")[:-1]) + "."
                
                # Armamos las subredes a escanear (La local de la VM + Las físicas más comunes)
                subredes = [base_local, "192.168.0.", "192.168.1.", "192.168.100."]
                # Eliminamos duplicados por si la VM ya está en una de esas
                subredes = list(set(subredes)) 
                
                ips_a_probar = []
                for subred in subredes:
                    ips_a_probar.extend([f"{subred}{i}" for i in range(1, 255)])
                    
                print(f"\n[BÚSQUEDA - Intento {intento}] {self.client_id} escaneando {len(ips_a_probar)} IPs (Cruzando NAT)...")

                with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
                    futuros = [executor.submit(self._probar_ip, ip, puerto_api) for ip in ips_a_probar]
                    
                    for futuro in concurrent.futures.as_completed(futuros):
                        resultado = futuro.result()
                        if resultado:
                            print(f"[OK] Profesor encontrado automáticamente en: {resultado}")
                            return resultado
                            
            if self.discovery_timeout > 0:
                tiempo_transcurrido = time.time() - start_time
                if tiempo_transcurrido >= self.discovery_timeout:
                    print(f"[TIMEOUT] No se encontró ningún servidor activo tras {self.discovery_timeout}s.")
                    return None
            
            time.sleep(5)
            intento += 1

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
        fallos_consecutivos = 0
        while True:
            try:
                logs_pendientes = []
                ruta_log = Path("combined_log.log")
                
                if self.estado_local == "GRABANDO":
                    logs_pendientes = self.orchestrator.combine_logs()

                data_payload = {
                    "client_id": self.client_id,
                    "estado_local": self.estado_local,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "alertas": json.dumps(self.alertas_pendientes)
                }
                
                archivo_abierto = None
                
                if self.estado_local == "GRABANDO" and ruta_log.exists() and ruta_log.stat().st_size > 0:
                    archivo_abierto = open(ruta_log, "rb")
                    archivos = {"archivo_log": archivo_abierto}
                else:
                    archivos = {"archivo_log": ("", "")}
                
                respuesta = requests.post(f"{self.server_url}/sync", data=data_payload, files=archivos, timeout=10)
                
                if archivo_abierto:
                    archivo_abierto.close()
                
                if respuesta.status_code == 200:
                    fallos_consecutivos = 0
                    if not self.conectado:
                        print(f"[OK] Conexión establecida con el profesor ({self.server_url}). Estado: {self.estado_local}")
                        self.conectado = True
                        
                    datos_servidor = respuesta.json()
                    
                    if logs_pendientes:
                        print(f"[*] {len(logs_pendientes)} registros enviados al servidor correctamente.")
                        self.orchestrator.clear_logs() 
                        
                    if self.alertas_pendientes:
                        print(f"[*] {len(self.alertas_pendientes)} alertas enviadas al servidor.")
                        self.alertas_pendientes.clear()
                        
                    comando_global = datos_servidor.get("comando_global")
                    accion = self._procesar_comando(comando_global)
                    
                    # Si la orden fue FINALIZAR, enviar reporte final, limpiar y regresar a modo de búsqueda
                    if accion == "FINALIZADO":
                        print("[*] Empaquetando y enviando telemetría final al servidor...")
                        self.orchestrator.combine_logs()
                        
                        archivo_final = None
                        if ruta_log.exists() and ruta_log.stat().st_size > 0:
                            archivo_final = open(ruta_log, "rb")
                            archivos_final = {"archivo_log": archivo_final}
                        else:
                            archivos_final = {"archivo_log": ("", "")}
                            
                        payload_final = {
                            "client_id": self.client_id,
                            "estado_local": "FINALIZADO",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "alertas": json.dumps(self.alertas_pendientes)
                        }
                        
                        try:
                            requests.post(f"{self.server_url}/sync", data=payload_final, files=archivos_final, timeout=10)
                            print("[OK] Telemetría final confirmada por el servidor.")
                        except Exception as e:
                            print(f"[AVISO] No se pudo confirmar reporte final: {e}")
                        finally:
                            if archivo_final:
                                archivo_final.close()
                                
                        self.orchestrator.clear_logs()
                        self.alertas_pendientes.clear()
                        self.orchestrator.reset()
                        
                        print("[INFO] Sesión de examen cerrada.")
                        print("[INFO] El cliente queda buscando el servidor del profesor (esperando que esté en ESPERANDO)...\n")
                        self.conectado = False
                        self.server_url = ""
                        self.estado_local = "ESPERANDO"
                        break
                        
                else:
                    print(f"[AVISO] El servidor respondió con error {respuesta.status_code}.")
                    
            except requests.exceptions.RequestException:
                fallos_consecutivos += 1
                if self.conectado:
                    print(f"[AVISO] Se perdió la conexión con el servidor (intento fallido {fallos_consecutivos}/3)...")
                    self.conectado = False
                else:
                    print(f"[AVISO] Reintentando conexión con {self.server_url} ({fallos_consecutivos}/3)...")
                
                if fallos_consecutivos >= 3:
                    print("\n[AVISO] El servidor no responde tras 3 intentos. Volviendo a búsqueda automática...")
                    if self.estado_local == "GRABANDO":
                        self.orchestrator.stop_all()
                    self.orchestrator.reset()
                    self.conectado = False
                    self.server_url = ""
                    self.estado_local = "ESPERANDO"
                    break
            
            time.sleep(self.interval)

    def _procesar_comando(self, estado_servidor):
        if not estado_servidor: 
            return None
            
        if estado_servidor == "GRABANDO" and self.estado_local != "GRABANDO":
            print("\n[+] Orden recibida: INICIAR TELEMETRÍA.")
            self.estado_local = "GRABANDO"
            self.orchestrator.start_all()
            return "GRABANDO"

        elif estado_servidor == "ESPERANDO" and self.estado_local != "ESPERANDO":
            print("\n[*] Orden recibida: EN ESPERA (Telemetría pausada).")
            if self.estado_local == "GRABANDO":
                self.orchestrator.stop_all()
            self.estado_local = "ESPERANDO"
            return "ESPERANDO"
            
        elif estado_servidor == "FINALIZADO":
            print("\n[!] Orden recibida: DETENER Y FINALIZAR EXAMEN.")
            if self.estado_local == "GRABANDO":
                self.orchestrator.stop_all()
            self.estado_local = "FINALIZADO"
            return "FINALIZADO"

        return None


if __name__ == "__main__":
    print("=" * 60)
    print(" Agente de Telemetría Estudiantil ")
    print("=" * 60)
    
    agente = TelemetryClient()
    agente.client_id = agente._generar_client_id()
    
    try:
        while True:
            # El agente buscará dependiendo de su configuración en config.txt
            url_profesor = agente.descubrir_servidor()
            
            if url_profesor:
                agente.server_url = url_profesor
                agente.iniciar_agente()
            else:
                # Si el timeout configurado expiró (solo cuando discovery_timeout_seconds > 0)
                print("\n[!] No se encontró el servidor en el tiempo estipulado.")
                break
                
            # Pausa breve antes de reiniciar la búsqueda si se finalizó una sesión
            time.sleep(3)
            
    except KeyboardInterrupt:
        print("\n[!] Proceso detenido por el usuario.")
        if agente.estado_local == "GRABANDO":
            agente.orchestrator.stop_all()
        
    except Exception as e:
        print("\n" + "!"*50)
        print("ERROR FATAL INESPERADO:")
        traceback.print_exc()
        print("!"*50)
        
        with open("crash_log.txt", "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)