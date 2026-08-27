import os
import socket
import json # NUEVO IMPORT
from fastapi import FastAPI, UploadFile, File, Form # NUEVOS IMPORTS
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Panel del Profesor - Telemetría")

CARPETA_DATOS = "datos_alumnos"
os.makedirs(CARPETA_DATOS, exist_ok=True)

comando_global = "ESPERANDO" 
clientes_conectados = {}

# --- UTILIDADES DE RED ---
def obtener_ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Ya no necesitamos el modelo SyncPayload porque recibiremos un Formulario Multipart
class Alerta(BaseModel):
    timestamp: str
    nivel: str
    mensaje: str

# --- RUTAS DE LA API ---

@app.post("/sync")
async def recibir_telemetria(
    # Usamos Form() y File() en lugar de un Pydantic BaseModel
    client_id: str = Form(...),
    estado_local: str = Form(...),
    timestamp: str = Form(...),
    alertas: str = Form("[]"),
    archivo_log: UploadFile = File(None) # El archivo es opcional
):
    clientes_conectados[client_id] = {
        "estado": estado_local,
        "ultimo_visto": timestamp
    }

    # 1. Guardar el archivo si el alumno envió uno
    if archivo_log and archivo_log.filename:
        ruta_destino = os.path.join(CARPETA_DATOS, f"{client_id}.log")
        
        # Leemos el archivo que viene en la red
        contenido = await archivo_log.read()
        
        # Lo guardamos en modo "ab" (Append Binary) para sumarlo al historial
        with open(ruta_destino, "ab") as f:
            f.write(contenido)

    # 2. Procesar alertas decodificando el JSON string
    lista_alertas = json.loads(alertas)
    for alerta in lista_alertas:
        print(f"\n[🚨 ALERTA - {client_id}] {alerta['nivel']}: {alerta['mensaje']}")

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
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    ruta_html = os.path.join(ruta_base, "index.html")
    return FileResponse(ruta_html)

# --- INICIO DEL SERVIDOR ---

if __name__ == "__main__":
    mi_ip = obtener_ip_local()
    PUERTO = 8000
    
    print("\n" + "="*50)
    print(f"[*] Iniciando Servidor del Profesor...")
    print(f"[*] IP Local Detectada: {mi_ip}")
    print(f"[*] Abre tu navegador en: http://localhost:{PUERTO} o http://{mi_ip}:{PUERTO}")
    print("="*50 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=PUERTO)