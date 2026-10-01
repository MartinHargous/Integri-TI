import os
from fastapi import APIRouter
from fastapi.responses import FileResponse

import state

router = APIRouter()


@router.get("/api/logs/{client_id}")
def ver_log_cliente(client_id: str):
    ruta = os.path.join(state.CARPETA_DATOS, f"{client_id}.log")
    if os.path.exists(ruta):
        return FileResponse(ruta, media_type="text/plain; charset=utf-8")
    return {"status": "error", "mensaje": f"No hay logs guardados para {client_id}"}


@router.get("/api/auditoria/{client_id}")
def obtener_auditoria_cliente(client_id: str):
    ruta = os.path.join(state.CARPETA_DATOS, f"{client_id}.log")
    if not os.path.exists(ruta):
        return {"status": "error", "mensaje": f"No hay logs registrados para {client_id}"}

    # 1. Obtener alertas directamente analizando el archivo completo para garantizar
    # sincronización 1:1 absoluta de números de línea con el archivo visualizado
    alertas_archivo = state.correlador.analizar_archivo_completo(client_id, ruta)

    # Combinar con alertas explícitas de cliente (ej. SVM o Crash) si existen
    alertas_cliente_locales = [
        a for a in state.historial_alertas
        if a.get("client_id") == client_id and a.get("regla_id") == "CLIENTE"
    ]
    alertas_cliente = alertas_archivo + alertas_cliente_locales
    alertas_cliente.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    # 2. Mapear líneas afectadas distinguiendo entre línea de disparo (infracción) y pasos previos
    lineas_con_alerta = {}
    for a in alertas_cliente:
        r_id = a.get("regla_id", "ALERTA")
        lineas_af = a.get("lineas_afectadas", [])
        linea_fin = a.get("linea_fin", lineas_af[-1] if lineas_af else 0)
        for num_l in lineas_af:
            if num_l not in lineas_con_alerta:
                lineas_con_alerta[num_l] = {
                    "alertas": [],
                    "es_disparo": False
                }
            if r_id not in lineas_con_alerta[num_l]["alertas"]:
                lineas_con_alerta[num_l]["alertas"].append(r_id)
            if num_l == linea_fin:
                lineas_con_alerta[num_l]["es_disparo"] = True

    lineas_parseadas = []
    modulos_encontrados = set()
    try:
        with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
            for idx, raw_line in enumerate(f, start=1):
                raw_clean = raw_line.rstrip("\r\n")
                alerta_info = lineas_con_alerta.get(idx, {"alertas": [], "es_disparo": False})
                tiene_alerta = len(alerta_info["alertas"]) > 0

                if not raw_clean.strip():
                    lineas_parseadas.append({
                        "numero": idx,
                        "raw": "",
                        "timestamp": "",
                        "modulo": "General",
                        "modulo_key": "general",
                        "contenido": "",
                        "es_alerta": False,
                        "es_disparo": False,
                        "alerta_tags": []
                    })
                    continue

                ev = state.correlador.parsear_linea(raw_clean, numero_linea=idx)
                if ev:
                    modulos_encontrados.add(ev["modulo_orig"])
                    lineas_parseadas.append({
                        "numero": idx,
                        "raw": raw_clean,
                        "timestamp": ev["ts_str"],
                        "modulo": ev["modulo_orig"],
                        "modulo_key": ev["modulo"],
                        "contenido": ev["contenido"],
                        "es_alerta": tiene_alerta,
                        "es_disparo": alerta_info["es_disparo"],
                        "alerta_tags": alerta_info["alertas"]
                    })
                else:
                    lineas_parseadas.append({
                        "numero": idx,
                        "raw": raw_clean,
                        "timestamp": "",
                        "modulo": "General",
                        "modulo_key": "general",
                        "contenido": raw_clean,
                        "es_alerta": tiene_alerta,
                        "es_disparo": alerta_info["es_disparo"],
                        "alerta_tags": alerta_info["alertas"]
                    })
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}

    info_cliente = state.clientes_conectados.get(client_id, {
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