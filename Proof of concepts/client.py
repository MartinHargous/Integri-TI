import time
import threading
import requests

class TelemetryClient:
    def __init__(self, server_url, client_id):
        self.server_url = server_url
        self.client_id = client_id
        
        self.estado_local = "ESPERANDO"
        self.hilo_red = None
        self.conectado = False

    def iniciar_agente(self):
        print(f"Iniciando agente {self.client_id}. Conectando al servidor...")
        self.hilo_red = threading.Thread(target=self._loop_sincronizacion, daemon=True)
        self.hilo_red.start()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Apagando agente...")

    def _loop_sincronizacion(self):
        """Este bucle consulta el estado y avisa que estamos vivos (Heartbeat)"""
        while True:
            try:
                # 1. Enviar Heartbeat y pedir el estado actual
                payload = {"client_id": self.client_id, "estado_local": self.estado_local}
                respuesta = requests.post(f"{self.server_url}/heartbeat", json=payload, timeout=2)
                
                if respuesta.status_code == 200:
                    self.conectado = True
                    datos_servidor = respuesta.json()
                    estado_servidor = datos_servidor.get("comando_global")
                    
                    # 2. Procesar la orden del servidor
                    self._procesar_comando(estado_servidor)
                
            except requests.exceptions.RequestException:
                if self.conectado:
                    print("⚠️ Se perdió la conexión con el PC del profesor. Reintentando...")
                    self.conectado = False
            
            # Esperar 3 segundos antes del próximo latido
            time.sleep(3)

    def _procesar_comando(self, estado_servidor):
        # Si el servidor ordenó empezar y nosotros estábamos esperando
        if estado_servidor == "GRABANDO" and self.estado_local != "GRABANDO":
            print("Orden recibida: INICIAR TELEMETRÍA.")
            self.estado_local = "GRABANDO"
            # Aquí llamas a self.iniciar_monitoreo() de tu script SVM
            
        # Si el servidor ordenó detenerse
        elif estado_servidor == "FINALIZADO" and self.estado_local == "GRABANDO":
            print("Orden recibida: DETENER TELEMETRÍA.")
            self.estado_local = "FINALIZADO"
            # Aquí llamas a self.detener_todo() de tu script SVM

if __name__ == "__main__":
    SERVER_IP = "http://192.168.1.81:8000" # Reemplazar con la IP del profesor
    agente = TelemetryClient(SERVER_IP, "Alumno_Martin_PC")
    agente.iniciar_agente()