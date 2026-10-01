from fastapi import APIRouter, Request

import state

router = APIRouter()


@router.get("/api/modulos")
def obtener_todos_los_modulos(destino: str = "global"):
    if destino not in ("global", "alertas") and destino in state.clientes_conectados and "configs" in state.clientes_conectados[destino]:
        return state.clientes_conectados[destino]["configs"]

    for cid, info in state.clientes_conectados.items():
        if "configs" in info and info["configs"]:
            base = {}
            for m, conf in info["configs"].items():
                base[m] = dict(conf)
            for m, cambios in state.configuraciones_globales.items():
                if m in base:
                    base[m].update(cambios)
                else:
                    base[m] = dict(cambios)
            return base

    base = {}
    for m, conf in state.CONFIGS_POR_DEFECTO.items():
        base[m] = dict(conf)
    for m, cambios in state.configuraciones_globales.items():
        if m in base:
            base[m].update(cambios)
    return base


@router.get("/api/modulos/{nombre}")
def obtener_modulo(nombre: str, destino: str = "global"):
    modulos = obtener_todos_los_modulos(destino)
    for k, v in modulos.items():
        if k.lower() == nombre.lower() or k.lower().replace(" ", "_") == nombre.lower().replace(" ", "_"):
            return {"status": "ok", "modulo": k, "config": v}
    return {"status": "error", "mensaje": f"Módulo '{nombre}' no encontrado"}


@router.post("/api/modulos/{nombre}")
async def actualizar_modulo(nombre: str, request: Request):
    datos = await request.json()
    destino = datos.get("destino", "global")
    valores = datos.get("valores", datos)
    if isinstance(valores, dict) and "destino" in valores:
        valores = {k: v for k, v in valores.items() if k != "destino"}

    nombre_normalizado = nombre.strip().lower()

    if destino in ("global", "alertas"):
        if nombre_normalizado not in state.configuraciones_globales:
            state.configuraciones_globales[nombre_normalizado] = {}
        state.configuraciones_globales[nombre_normalizado].update(valores)

        for cid in state.clientes_conectados.keys():
            if cid not in state.configuraciones_pendientes:
                state.configuraciones_pendientes[cid] = {}
            if nombre_normalizado not in state.configuraciones_pendientes[cid]:
                state.configuraciones_pendientes[cid][nombre_normalizado] = {}
            state.configuraciones_pendientes[cid][nombre_normalizado].update(valores)

            if "configs" in state.clientes_conectados[cid]:
                for k in state.clientes_conectados[cid]["configs"].keys():
                    if k.replace(" ", "_") == nombre_normalizado.replace(" ", "_"):
                        state.clientes_conectados[cid]["configs"][k].update(valores)
                        break
                else:
                    state.clientes_conectados[cid]["configs"][nombre_normalizado] = dict(valores)

        print(f"\n[HTTP] Configuración global encolada para '{nombre_normalizado}': {valores}")
        return {
            "status": "ok",
            "mensaje": f"Configuración encolada por HTTP para todos los clientes",
            "modulo": nombre,
            "config": valores
        }
    else:
        if destino not in state.configuraciones_pendientes:
            state.configuraciones_pendientes[destino] = {}
        if nombre_normalizado not in state.configuraciones_pendientes[destino]:
            state.configuraciones_pendientes[destino][nombre_normalizado] = {}
        state.configuraciones_pendientes[destino][nombre_normalizado].update(valores)

        if destino in state.clientes_conectados and "configs" in state.clientes_conectados[destino]:
            for k in state.clientes_conectados[destino]["configs"].keys():
                if k.replace(" ", "_") == nombre_normalizado.replace(" ", "_"):
                    state.clientes_conectados[destino]["configs"][k].update(valores)
                    break
            else:
                state.clientes_conectados[destino]["configs"][nombre_normalizado] = dict(valores)

        print(f"\n[HTTP] Configuración encolada para {destino} en '{nombre_normalizado}': {valores}")
        return {
            "status": "ok",
            "mensaje": f"Configuración encolada por HTTP para {destino}",
            "modulo": nombre,
            "config": valores
        }