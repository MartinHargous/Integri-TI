import os
import socket
import json
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from correlator import LogCorrelator
import database
import ai_insight

app = FastAPI(title="Panel del Profesor - Telemetría")

# Habilitar CORS para soportar conexiones desde cualquier PC/dispositivo en la red
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_DATOS = os.path.join(BASE_DIR, "datos_alumnos")
os.makedirs(CARPETA_DATOS, exist_ok=True)

# Inicializar Base de Datos SQLite (integri_ti.db)
RUTA_REGLAS = os.path.join(BASE_DIR, "reglas.json")
database.inicializar_db(RUTA_REGLAS)

# Montar archivos estáticos (CSS y JS separados) y plantillas HTML modulares
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR) if os.path.exists(TEMPLATES_DIR) else None
if templates:
    templates.env.auto_reload = True

# Motor de correlación secuencial de logs
correlador = LogCorrelator(RUTA_REGLAS)

# El servidor DEBE iniciar en estado ESPERANDO
comando_global = "ESPERANDO" 
clientes_conectados = {}
historial_alertas = []
configuraciones_pendientes = {}
configuraciones_globales = {}

CONFIGS_POR_DEFECTO = {
    "sniffer": {"enabled": "true", "log_file": "sniffer.log", "method": "regex", "cooldown_seconds": "1"},
    "keylogger": {"enabled": "True", "log_file": "keylogger.log", "poll_seconds": "10"},
    "keystrokes svm": {"enabled": "True", "show_interface": "false", "log_file": "alerts.log", "train_chars": "100", "time_window": "60", "alert_threshold": "0.25", "max_hold_time": "0.5", "max_flight_time": "1.5", "svm_nu": "0.05", "svm_kernel": "rbf", "svm_gamma": "scale"},
    "error_detection": {"enabled": "True", "log_file": "auditoria_python.log", "sitecustomize_path": "", "capture_errors": "true", "capture_input": "true", "capture_print": "true", "monitor_poll_seconds": "0.5", "excluded_scripts": "pip,pip.exe,error_detection.py,manager_telemetria.py"},
    "paperclip": {"enabled": "True", "log_file": "paperclip.log", "poll_seconds": "0.5", "log_content": "true", "max_content_length": "1000"},
    "program monitor": {"enabled": "True", "log_file": "program_monitor.log", "poll_seconds": "1.0", "log_title_changes": "true"}
}

def inicializar_alertas_desde_historial():
    """Analiza logs existentes en disco para poblar alertas reales iniciales"""
    global historial_alertas
    if os.path.exists(CARPETA_DATOS):
        for arch in os.listdir(CARPETA_DATOS):
            if arch.endswith(".log"):
                cid = arch[:-4]
                ruta = os.path.join(CARPETA_DATOS, arch)
                alertas_detectadas = correlador.analizar_archivo_completo(cid, ruta)
                if alertas_detectadas:
                    historial_alertas.extend(alertas_detectadas)
                    print(f"[*] {len(alertas_detectadas)} alertas históricas detectadas por reglas para {cid}")
        
        historial_alertas.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        if len(historial_alertas) > 100:
            historial_alertas = historial_alertas[:100]

inicializar_alertas_desde_historial()

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

# --- RUTAS DE LA API ---

@app.post("/sync")
async def recibir_telemetria(
    request: Request,
    client_id: str = Form(...),
    estado_local: str = Form(...),
    timestamp: str = Form(...),
    alertas: str = Form("[]"),
    configs: str = Form("{}"),
    archivo_log: UploadFile = File(None)
):
    ip_cliente = request.client.host if request.client else "127.0.0.1"

    # 1. Guardar el archivo si el alumno envió uno y correlacionar secuencias de logs
    bytes_log = 0
    if archivo_log and archivo_log.filename:
        ruta_destino = os.path.join(CARPETA_DATOS, f"{client_id}.log")
        contenido = await archivo_log.read()
        bytes_log = len(contenido)
        with open(ruta_destino, "ab") as f:
            f.write(contenido)

        # Analizar las nuevas líneas a través del motor de correlación secuencial
        texto_lineas = contenido.decode("utf-8", errors="ignore").splitlines()
        alertas_correlacion = correlador.procesar_nuevos_eventos(client_id, texto_lineas)
        for a in alertas_correlacion:
            print(f"\n[ALERTA SECUENCIAL DISPARADA - {client_id}] {a['nivel']}: {a['mensaje']}")
            historial_alertas.insert(0, a)
            if len(historial_alertas) > 100:
                historial_alertas.pop()

    # 2. Parsear configuraciones reportadas por HTTP desde el cliente remoto
    configs_recibidas = {}
    try:
        if configs and configs.strip():
            configs_recibidas = json.loads(configs)
    except Exception as e:
        print(f"Error decodificando configs de {client_id}: {e}")

    # 3. Registrar o actualizar cliente en clientes_conectados
    prev_configs = clientes_conectados.get(client_id, {}).get("configs", {})
    clientes_conectados[client_id] = {
        "estado": estado_local,
        "ultimo_visto": timestamp,
        "ip": ip_cliente,
        "bytes_recibidos": bytes_log,
        "configs": configs_recibidas if configs_recibidas else prev_configs
    }

    # 4. Procesar alertas explícitas reportadas directamente por el cliente (ej. SVM o Crash)
    try:
        if alertas and alertas.strip():
            lista_alertas = json.loads(alertas)
            for alerta in lista_alertas:
                print(f"\n[ALERTA CLIENTE - {client_id}] {alerta.get('nivel', 'Alerta')}: {alerta.get('mensaje', '')}")
                historial_alertas.insert(0, {
                    "client_id": client_id,
                    "timestamp": alerta.get("timestamp", timestamp),
                    "nivel": alerta.get("nivel", "Media"),
                    "regla_id": "CLIENTE",
                    "regla_nombre": "Alerta Local de Agente",
                    "mensaje": alerta.get("mensaje", "")
                })
                if len(historial_alertas) > 100:
                    historial_alertas.pop()
    except Exception as e:
        print(f"Error procesando alertas: {e}")

    # 5. Preparar configuraciones pendientes para enviar por HTTP al cliente
    configs_a_enviar = {}
    if client_id in configuraciones_pendientes:
        configs_a_enviar.update(configuraciones_pendientes.pop(client_id))

    if configuraciones_globales:
        for mod, cambios in configuraciones_globales.items():
            if mod not in configs_a_enviar:
                configs_a_enviar[mod] = {}
            configs_a_enviar[mod].update(cambios)

    return {
        "comando_global": comando_global,
        "configuraciones": configs_a_enviar
    }

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
    # Recopilar últimos eventos de telemetría y asegurar presencia de clientes conocidos
    eventos_logs = []
    if os.path.exists(CARPETA_DATOS):
        for arch in os.listdir(CARPETA_DATOS):
            if arch.endswith(".log"):
                cid = arch[:-4]
                ruta = os.path.join(CARPETA_DATOS, arch)
                
                # Si el cliente ya tiene archivo de log pero no ha hecho ping en esta sesión, registrarlo en ESPERANDO
                if cid not in clientes_conectados:
                    clientes_conectados[cid] = {
                        "estado": "ESPERANDO",
                        "ultimo_visto": "",
                        "ip": "127.0.0.1",
                        "bytes_recibidos": os.path.getsize(ruta) if os.path.exists(ruta) else 0,
                        "configs": {}
                    }

                try:
                    with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
                        lineas = [l.strip() for l in f.readlines() if l.strip() and "--- IGNORE ---" not in l]
                        for linea in lineas[-5:]:
                            eventos_logs.append({
                                "client_id": cid,
                                "timestamp": "",
                                "nivel": "Info",
                                "mensaje": linea
                            })
                except Exception:
                    pass

    return {
        "comando_global": comando_global,
        "clientes": clientes_conectados,
        "alertas": historial_alertas, # ÚNICAMENTE ALERTAS REALES GENERADAS POR REGLAS O AGENTES
        "eventos_logs": eventos_logs[-12:]
    }

@app.post("/api/alertas/limpiar")
def limpiar_alertas():
    historial_alertas.clear()
    return {"status": "ok"}

# --- REGLAS DE CORRELACIÓN SECUENCIAL ---

@app.get("/api/reglas")
def obtener_reglas():
    return correlador.obtener_reglas()

@app.post("/api/reglas")
async def guardar_regla(request: Request):
    datos = await request.json()
    regla = correlador.agregar_o_actualizar_regla(datos)
    # Reanalizar archivos para que la nueva regla busque matches de inmediato
    inicializar_alertas_desde_historial()
    return {"status": "ok", "regla": regla, "total_alertas": len(historial_alertas)}

@app.delete("/api/reglas/{regla_id}")
def eliminar_regla(regla_id: str):
    ok = correlador.eliminar_regla(regla_id)
    return {"status": "ok" if ok else "error"}

@app.post("/api/reglas/reanalizar")
def reanalizar_todo():
    global historial_alertas
    historial_alertas.clear()
    inicializar_alertas_desde_historial()
    return {"status": "ok", "total_alertas": len(historial_alertas)}

# --- CONFIGURACIÓN DE MÓDULOS ---

@app.get("/api/modulos")
def obtener_todos_los_modulos(destino: str = "global"):
    if destino not in ("global", "alertas") and destino in clientes_conectados and "configs" in clientes_conectados[destino]:
        return clientes_conectados[destino]["configs"]

    for cid, info in clientes_conectados.items():
        if "configs" in info and info["configs"]:
            base = {}
            for m, conf in info["configs"].items():
                base[m] = dict(conf)
            for m, cambios in configuraciones_globales.items():
                if m in base:
                    base[m].update(cambios)
                else:
                    base[m] = dict(cambios)
            return base

    base = {}
    for m, conf in CONFIGS_POR_DEFECTO.items():
        base[m] = dict(conf)
    for m, cambios in configuraciones_globales.items():
        if m in base:
            base[m].update(cambios)
    return base

@app.get("/api/modulos/{nombre}")
def obtener_modulo(nombre: str, destino: str = "global"):
    modulos = obtener_todos_los_modulos(destino)
    for k, v in modulos.items():
        if k.lower() == nombre.lower() or k.lower().replace(" ", "_") == nombre.lower().replace(" ", "_"):
            return {"status": "ok", "modulo": k, "config": v}
    return {"status": "error", "mensaje": f"Módulo '{nombre}' no encontrado"}

@app.post("/api/modulos/{nombre}")
async def actualizar_modulo(nombre: str, request: Request):
    datos = await request.json()
    destino = datos.get("destino", "global")
    valores = datos.get("valores", datos)
    if isinstance(valores, dict) and "destino" in valores:
        valores = {k: v for k, v in valores.items() if k != "destino"}

    nombre_normalizado = nombre.strip().lower()

    if destino in ("global", "alertas"):
        if nombre_normalizado not in configuraciones_globales:
            configuraciones_globales[nombre_normalizado] = {}
        configuraciones_globales[nombre_normalizado].update(valores)

        for cid in clientes_conectados.keys():
            if cid not in configuraciones_pendientes:
                configuraciones_pendientes[cid] = {}
            if nombre_normalizado not in configuraciones_pendientes[cid]:
                configuraciones_pendientes[cid][nombre_normalizado] = {}
            configuraciones_pendientes[cid][nombre_normalizado].update(valores)

            if "configs" in clientes_conectados[cid] and nombre_normalizado in clientes_conectados[cid]["configs"]:
                clientes_conectados[cid]["configs"][nombre_normalizado].update(valores)

        print(f"\n[HTTP] Configuración global encolada para '{nombre_normalizado}': {valores}")
        return {
            "status": "ok",
            "mensaje": f"Configuración encolada por HTTP para todos los clientes",
            "modulo": nombre,
            "config": valores
        }
    else:
        if destino not in configuraciones_pendientes:
            configuraciones_pendientes[destino] = {}
        if nombre_normalizado not in configuraciones_pendientes[destino]:
            configuraciones_pendientes[destino][nombre_normalizado] = {}
        configuraciones_pendientes[destino][nombre_normalizado].update(valores)

        if destino in clientes_conectados and "configs" in clientes_conectados[destino]:
            if nombre_normalizado in clientes_conectados[destino]["configs"]:
                clientes_conectados[destino]["configs"][nombre_normalizado].update(valores)

        print(f"\n[HTTP] Configuración encolada para {destino} en '{nombre_normalizado}': {valores}")
        return {
            "status": "ok",
            "mensaje": f"Configuración encolada por HTTP para {destino}",
            "modulo": nombre,
            "config": valores
        }

@app.get("/api/logs/{client_id}")
def ver_log_cliente(client_id: str):
    ruta = os.path.join(CARPETA_DATOS, f"{client_id}.log")
    if os.path.exists(ruta):
        return FileResponse(ruta, media_type="text/plain; charset=utf-8")
    return {"status": "error", "mensaje": f"No hay logs guardados para {client_id}"}

@app.get("/api/auditoria/{client_id}")
def obtener_auditoria_cliente(client_id: str):
    ruta = os.path.join(CARPETA_DATOS, f"{client_id}.log")
    if not os.path.exists(ruta):
        return {"status": "error", "mensaje": f"No hay logs registrados para {client_id}"}

    alertas_cliente = [a for a in historial_alertas if a.get("client_id") == client_id]
    
    lineas_con_alerta = {}
    for a in alertas_cliente:
        r_id = a.get("regla_id", "ALERTA")
        for num_l in a.get("lineas_afectadas", []):
            if num_l not in lineas_con_alerta:
                lineas_con_alerta[num_l] = []
            if r_id not in lineas_con_alerta[num_l]:
                lineas_con_alerta[num_l].append(r_id)

    lineas_parseadas = []
    modulos_encontrados = set()
    try:
        with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
            for idx, raw_line in enumerate(f, start=1):
                raw_clean = raw_line.rstrip("\r\n")
                if not raw_clean.strip():
                    continue
                ev = correlador.parsear_linea(raw_clean, numero_linea=idx)
                alerta_tags = lineas_con_alerta.get(idx, [])
                if ev:
                    modulos_encontrados.add(ev["modulo_orig"])
                    lineas_parseadas.append({
                        "numero": idx,
                        "raw": raw_clean,
                        "timestamp": ev["ts_str"],
                        "modulo": ev["modulo_orig"],
                        "modulo_key": ev["modulo"],
                        "contenido": ev["contenido"],
                        "es_alerta": len(alerta_tags) > 0,
                        "alerta_tags": alerta_tags
                    })
                else:
                    lineas_parseadas.append({
                        "numero": idx,
                        "raw": raw_clean,
                        "timestamp": "",
                        "modulo": "General",
                        "modulo_key": "general",
                        "contenido": raw_clean,
                        "es_alerta": len(alerta_tags) > 0,
                        "alerta_tags": alerta_tags
                    })
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}

    info_cliente = clientes_conectados.get(client_id, {
        "estado": "HISTÓRICO",
        "ultimo_visto": "",
        "ip": "Local/Histórico",
        "bytes_recibidos": os.path.getsize(ruta) if os.path.exists(ruta) else 0
    })

    return {
        "status": "ok",
        "client_id": client_id,
        "info": info_cliente,
        "total_lineas": len(lineas_parseadas),
        "total_alertas": len(alertas_cliente),
        "alertas": alertas_cliente,
        "modulos": sorted(list(modulos_encontrados)),
        "lineas": lineas_parseadas
    }

# --- ENDPOINTS DE INSIGHT PEDAGÓGICO CON IA (CHATGPT - MODELO LUNA) ---

@app.get("/api/insight/{client_id}")
def obtener_insight_cliente(client_id: str):
    """Consulta si ya existe un insight para este cliente en la base de datos SQLite."""
    insight = database.obtener_ultimo_insight(client_id)
    if not insight:
        return {"status": "ok", "existe": False}

    secciones = ai_insight.parsear_secciones_respuesta(insight["response"])
    return {
        "status": "ok",
        "existe": True,
        "insight": insight,
        "secciones": secciones
    }

@app.post("/api/insight/{client_id}")
async def generar_insight_cliente(client_id: str, request: Request):
    """
    Genera un insight con ChatGPT (modelo luna) basado en los logs del alumno.
    Si ya existe un insight previo y no se especificó forzar=True, retorna el almacenado
    en la base de datos para evitar doble generación.
    """
    forzar = False
    try:
        body = await request.json()
        forzar = bool(body.get("forzar", False))
    except Exception:
        pass

    # 1. Evitar doble generación si ya existe registro en SQLite
    if not forzar:
        insight_existente = database.obtener_ultimo_insight(client_id)
        if insight_existente:
            secciones = ai_insight.parsear_secciones_respuesta(insight_existente["response"])
            return {
                "status": "ok",
                "origen": "cache_db",
                "mensaje": "Insight cargado desde la base de datos (evita doble generación).",
                "insight": insight_existente,
                "secciones": secciones
            }

    # 2. Verificar existencia del archivo de logs
    ruta_log = os.path.join(CARPETA_DATOS, f"{client_id}.log")
    if not os.path.exists(ruta_log):
        return {
            "status": "error",
            "mensaje": f"No hay archivo de telemetría registrado para {client_id}."
        }

    # 3. Solicitar el insight al modelo Luna / ChatGPT
    resultado = await ai_insight.solicitar_insight_ia(client_id, ruta_log)
    if resultado.get("status") != "ok":
        return resultado

    # 4. Guardar el prompt y la respuesta en SQLite vinculados al cliente
    raw_str = json.dumps(resultado.get("raw_api", {}), ensure_ascii=False)
    guardado = database.guardar_insight(
        client_id=client_id,
        prompt=resultado["prompt"],
        response=resultado["response"],
        model=resultado["model"],
        raw_response=raw_str
    )

    return {
        "status": "ok",
        "origen": "generado",
        "mensaje": "Insight pedagógico generado exitosamente con IA.",
        "insight": guardado,
        "secciones": resultado["secciones"]
    }

@app.get("/auditoria/{client_id}")
def ver_auditoria_html(request: Request, client_id: str):
    ruta_root = os.path.join(BASE_DIR, "audit.html")
    ruta_tpl = os.path.join(TEMPLATES_DIR, "audit.html")

    # Si se editó Server/audit.html en la raíz, sincronizarlo automáticamente a templates/
    if os.path.exists(ruta_root) and os.path.exists(ruta_tpl):
        if os.path.getmtime(ruta_root) > os.path.getmtime(ruta_tpl):
            try:
                import shutil
                shutil.copy2(ruta_root, ruta_tpl)
            except Exception:
                pass

    if templates and os.path.exists(ruta_tpl):
        response = templates.TemplateResponse(request, "audit.html", context={"client_id": client_id})
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    return FileResponse(ruta_root, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

# --- DASHBOARD WEB HTML ---

@app.get("/")
def ver_dashboard(request: Request):
    ruta_root = os.path.join(BASE_DIR, "index.html")
    ruta_tpl = os.path.join(TEMPLATES_DIR, "index.html")

    if os.path.exists(ruta_root) and os.path.exists(ruta_tpl):
        if os.path.getmtime(ruta_root) > os.path.getmtime(ruta_tpl):
            try:
                import shutil
                shutil.copy2(ruta_root, ruta_tpl)
            except Exception:
                pass

    if templates and os.path.exists(ruta_tpl):
        response = templates.TemplateResponse(request, "index.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    return FileResponse(ruta_root, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

# --- INICIO DEL SERVIDOR ---

if __name__ == "__main__":
    mi_ip = obtener_ip_local()
    PUERTO = 8000
    
    print("\n" + "="*50)
    print(f"[*] Iniciando Servidor del Profesor (Integri-TI)...")
    print(f"[*] IP Local Detectada: {mi_ip}")
    print(f"[*] Abre tu navegador en: http://localhost:{PUERTO} o http://{mi_ip}:{PUERTO}")
    print("="*50 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=PUERTO)