import os
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

import state

router = APIRouter()


@router.get("/api/status")
def obtener_estado_actual():
    if not state._clientes_inicializados_desde_disco:
        state._inicializar_clientes_desde_disco()
        state._clientes_inicializados_desde_disco = True

    # Se arma una copia superficial por cliente para inyectar el flag de
    # silencio calculado por el watchdog, sin mutar el estado interno.
    clientes_con_estado_conexion = {}
    for cid, info in state.clientes_conectados.items():
        info_copia = dict(info)
        info_copia["en_silencio"] = cid in state._alumnos_alertados_por_silencio
        clientes_con_estado_conexion[cid] = info_copia

    return {
        "comando_global": state.comando_global,
        "clientes": clientes_con_estado_conexion,
        "alertas": state.historial_alertas,
        "eventos_logs": list(state.eventos_telemetria_recientes)[-12:]
    }


@router.post("/api/alertas/limpiar")
def limpiar_alertas():
    state.historial_alertas.clear()
    return {"status": "ok"}


@router.get("/auditoria/{client_id}")
def ver_auditoria_html(request: Request, client_id: str):
    if state.templates and os.path.exists(os.path.join(state.TEMPLATES_DIR, "audit.html")):
        response = state.templates.TemplateResponse(request, "audit.html", context={"client_id": client_id})
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    ruta_root = os.path.join(state.BASE_DIR, "audit.html")
    return FileResponse(ruta_root)


@router.get("/")
def ver_dashboard(request: Request):
    if state.templates and os.path.exists(os.path.join(state.TEMPLATES_DIR, "index.html")):
        response = state.templates.TemplateResponse(request, "index.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    ruta_root = os.path.join(state.BASE_DIR, "index.html")
    return FileResponse(ruta_root)