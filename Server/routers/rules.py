from fastapi import APIRouter, Request
 
import state
 
router = APIRouter()
 
 
@router.get("/api/reglas")
def obtener_reglas():
    return state.correlador.obtener_reglas()
 
 
@router.post("/api/reglas")
async def guardar_regla(request: Request):
    datos = await request.json()
    regla = state.correlador.agregar_o_actualizar_regla(datos)
    # Reanalizar archivos para que la nueva regla busque matches de inmediato
    state.inicializar_alertas_desde_historial()
    return {"status": "ok", "regla": regla, "total_alertas": len(state.historial_alertas)}
 
 
@router.delete("/api/reglas/{regla_id}")
def eliminar_regla(regla_id: str):
    ok = state.correlador.eliminar_regla(regla_id)
    return {"status": "ok" if ok else "error"}
 
 
@router.post("/api/reglas/reanalizar")
def reanalizar_todo():
    state.historial_alertas.clear()
    state.inicializar_alertas_desde_historial()
    return {"status": "ok", "total_alertas": len(state.historial_alertas)}