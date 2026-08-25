import os
import socket
import time
import threading
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Panel del Profesor - Telemetría")

CARPETA_DATOS = "datos_alumnos"
os.makedirs(CARPETA_DATOS, exist_ok=True)

comando_global = "ESPERANDO" 
clientes_conectados = {}

# --- UTILIDADES DE RED (NUEVO) ---

def obtener_ip_local():
    """Obtiene la IP real del profesor en la red Wi-Fi o LAN actual."""
    try:
        # Nos conectamos a un servidor externo (no envía datos reales) solo para ver qué IP de salida usamos
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def emitir_presencia(ip_local, puerto_api=8000, puerto_broadcast=9999):
    """Grita a toda la red local la dirección de esta API cada 2 segundos."""
    mensaje = f"PROFESOR_API:http://{ip_local}:{puerto_api}".encode('utf-8')
    
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
        # Habilitar el modo Broadcast para que el router lo replique a todos
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        while True:
            try:
                # El string "<broadcast>" envía el paquete a toda la subred
                s.sendto(mensaje, ("<broadcast>", puerto_broadcast))
            except Exception:
                pass
            time.sleep(2) # Repetir cada 2 segundos

# --- MODELOS DE DATOS ---
class Alerta(BaseModel):
    timestamp: str
    nivel: str
    mensaje: str

class SyncPayload(BaseModel):
    client_id: str
    estado_local: str
    timestamp: str
    logs: list[str] = []
    alertas: list[Alerta] = []

# --- RUTAS DE LA API ---

@app.post("/sync")
def recibir_telemetria(payload: SyncPayload):
    clientes_conectados[payload.client_id] = {
        "estado": payload.estado_local,
        "ultimo_visto": payload.timestamp
    }

    if payload.logs:
        ruta_archivo = os.path.join(CARPETA_DATOS, f"{payload.client_id}.log")
        with open(ruta_archivo, "a", encoding="utf-8") as f:
            for log in payload.logs:
                f.write(f"{log}\n")

    for alerta in payload.alertas:
        print(f"\n[🚨 ALERTA - {payload.client_id}] {alerta.nivel}: {alerta.mensaje}")

    return {"comando_global": comando_global}

@app.get("/profesor/comando/{nuevo_comando}")
def cambiar_estado_clase(nuevo_comando: str):
    global comando_global
    comandos_validos = ["ESPERANDO", "GRABANDO", "FINALIZADO"]
    comando_upper = nuevo_comando.upper()
    
    if comando_upper in comandos_validos:
        comando_global = comando_upper
        print(f"\n[+] COMANDO GLOBAL CAMBIADO A: {comando_global}")
        return {"status": "OK", "comando_actual": comando_global}
    return {"status": "ERROR"}

@app.get("/api/status")
def obtener_estado_actual():
    return {
        "comando_global": comando_global,
        "clientes": clientes_conectados
    }

# --- DASHBOARD WEB HTML ---

@app.get("/", response_class=FileResponse)
def ver_dashboard():
    return FileResponse("index.html")

# --- INICIO DEL SERVIDOR ---

if __name__ == "__main__":
    # 1. Obtener nuestra IP dinámica
    mi_ip = obtener_ip_local()
    PUERTO = 8000
    
    print("\n" + "="*50)
    print(f"[*] Iniciando Servidor del Profesor...")
    print(f"[*] IP Local Detectada: {mi_ip}")
    print(f"[*] Abre tu navegador en: http://localhost:{PUERTO} o http://{mi_ip}:{PUERTO}")
    print("="*50 + "\n")
    
    # 2. Iniciar el grito UDP en segundo plano (Faro/Beacon)
    hilo_beacon = threading.Thread(target=emitir_presencia, args=(mi_ip, PUERTO), daemon=True)
    hilo_beacon.start()
    print("[*] Autodescubrimiento UDP activado. Esperando alumnos...")
    
    # 3. Lanzar FastAPI
    uvicorn.run(app, host="0.0.0.0", port=PUERTO)