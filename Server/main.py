import os
import socket
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
 
import state  # Importar primero: inicializa DB, correlator, comparator y el watchdog de silencio.
from routers import agent, dashboard, rules, modules, logs, insights, comparator_router, admin
 
app = FastAPI(title="Panel del Profesor - Telemetría")
 
# Habilitar CORS para soportar conexiones desde cualquier PC/dispositivo en la red
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# Montar archivos estáticos (CSS y JS separados)
if os.path.exists(state.STATIC_DIR):
    app.mount("/static", StaticFiles(directory=state.STATIC_DIR), name="static")
 
# --- Registro de routers, por categoría REST (ver informe, sección 5.1) ---
app.include_router(agent.router)              # 5.1.1 — Agente: /api/discovery, /sync, /profesor/comando
app.include_router(dashboard.router)          # 5.1.2 — Dashboard: /, /auditoria/{id}, /api/status, /api/alertas/limpiar
app.include_router(rules.router)              # 5.1.3 — Reglas: /api/reglas*
app.include_router(modules.router)            # 5.1.4 — Módulos: /api/modulos*
app.include_router(logs.router)               # 5.1.5 — Logs y Auditoría: /api/logs/{id}, /api/auditoria/{id}
app.include_router(insights.router)           # 5.1.6 — AI Insights: /api/insight/{id}
app.include_router(comparator_router.router)  # 5.1.7 — Comparador: /api/comparador/*
app.include_router(admin.router)              # Administrativo (nuevo, sin categoría 5.1.x aún): /reset
 
 
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