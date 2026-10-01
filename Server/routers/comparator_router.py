from fastapi import APIRouter, Request

import state

router = APIRouter()


@router.get("/api/comparador/config")
def obtener_config_comparador():
    """Retorna la configuración actual del comparador y el estado de su scheduler."""
    return {
        "status": "ok",
        "config": state.comparador_logs.obtener_config(),
        "estado": state.comparador_logs.obtener_estado()
    }


@router.post("/api/comparador/config")
async def guardar_config_comparador_endpoint(request: Request):
    """Actualiza la configuración del comparador y la persiste en SQLite."""
    try:
        body = await request.json()
        cfg_actualizada = state.comparador_logs.actualizar_config(body)
        return {
            "status": "ok",
            "mensaje": "Configuración del comparador actualizada exitosamente.",
            "config": cfg_actualizada,
            "estado": state.comparador_logs.obtener_estado()
        }
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


@router.get("/api/comparador/resultados")
def obtener_resultados_comparador():
    """Retorna el resultado más reciente del análisis comparativo."""
    res = state.comparador_logs.obtener_ultimo_resultado()
    if not res:
        res = state.comparador_logs.ejecutar_analisis()
    return {
        "status": "ok",
        "resultado": res,
        "estado": state.comparador_logs.obtener_estado()
    }


@router.post("/api/comparador/ejecutar")
def ejecutar_comparador_ahora():
    """Dispara un análisis comparativo manual inmediato y retorna los resultados."""
    try:
        res = state.comparador_logs.ejecutar_analisis()
        return {
            "status": "ok",
            "mensaje": "Análisis comparativo ejecutado exitosamente.",
            "resultado": res,
            "estado": state.comparador_logs.obtener_estado()
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "mensaje": str(e)}