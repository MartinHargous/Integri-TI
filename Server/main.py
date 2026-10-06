"""
Punto de entrada del servidor Integri-TI.

Crea la app FastAPI, monta archivos estáticos, registra los routers por
categoría (ver informe de arquitectura, sección 5.1) e inicia Uvicorn.

Todo el estado compartido (clientes conectados, alertas, reglas, comparador,
OTP, etc.) vive en state.py — este archivo no declara variables globales de
negocio propias, solo configuración de la app en sí.
"""
import os
import socket
import uvicorn
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import state  # Importar primero: inicializa DB, correlator, comparator y el watchdog de silencio.
from security import solo_local
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
# Control de acceso: todo lo del profesor exige acceso directo desde este equipo
# (ver security.py). La única excepción es agent.router, que mezcla endpoints
# públicos de los agentes (/sync, /api/discovery) con uno del profesor
# (/profesor/comando): ahí la restricción se aplica ruta por ruta, no al router.
solo_profesor = [Depends(solo_local)]

app.include_router(agent.router)                                          # 5.1.1 — Agente: /api/discovery, /sync (abiertos) + /profesor/comando (solo local)
app.include_router(dashboard.router, dependencies=solo_profesor)          # 5.1.2 — Dashboard: /, /auditoria/{id}, /api/status, /api/alertas/limpiar
app.include_router(rules.router, dependencies=solo_profesor)              # 5.1.3 — Reglas: /api/reglas*
app.include_router(modules.router, dependencies=solo_profesor)            # 5.1.4 — Módulos: /api/modulos*
app.include_router(logs.router, dependencies=solo_profesor)               # 5.1.5 — Logs y Auditoría: /api/logs/{id}, /api/auditoria/{id}
app.include_router(insights.router, dependencies=solo_profesor)           # 5.1.6 — AI Insights: /api/insight/{id}
app.include_router(comparator_router.router, dependencies=solo_profesor)  # 5.1.7 — Comparador: /api/comparador/*
app.include_router(admin.router, dependencies=solo_profesor)              # Administrativo (nuevo, sin categoría 5.1.x aún): /reset


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
    print(f"[*] Dashboard (solo desde este equipo): http://localhost:{PUERTO}")
    print(f"[*] Los agentes se conectan a: http://{mi_ip}:{PUERTO} (LAN) o por el túnel configurado")
    print("="*50 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=PUERTO)