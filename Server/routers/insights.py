import os
import json
from fastapi import APIRouter, Request

import state
import database
import ai_insight

router = APIRouter()


@router.get("/api/insight/{client_id}")
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


@router.post("/api/insight/{client_id}")
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
    ruta_log = os.path.join(state.CARPETA_DATOS, f"{client_id}.log")
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