"""
Endpoints administrativos del profesor, fuera de las 7 categorías originales
del informe de arquitectura (sección 5.1): por ahora, solo POST /reset.
"""
import os
import io
import zipfile
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import Response

import state
import database
import ai_insight

router = APIRouter()


def _generar_reporte_markdown() -> str:
    """
    Arma un reporte legible en Markdown con alertas e insights de IA por
    alumno, a partir del estado actual (antes de borrar nada). Incluye todo
    client_id conocido: los que siguen en clientes_conectados y cualquier
    otro que solo tenga un archivo .log en disco (por si ya se desconectó
    pero su información no debe perderse).
    """
    client_ids = set(state.clientes_conectados.keys())
    if os.path.exists(state.CARPETA_DATOS):
        for arch in os.listdir(state.CARPETA_DATOS):
            if arch.endswith(".log"):
                client_ids.add(arch[:-4])

    lineas = [
        "# Reporte General del Examen",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total de alumnos con datos registrados: {len(client_ids)}",
        ""
    ]

    for cid in sorted(client_ids):
        lineas.append(f"## {cid}")

        # --- Alertas de este alumno (correlator, cliente, y Sistema/silencio) ---
        alertas_cid = [a for a in state.historial_alertas if a.get("client_id") == cid]
        lineas.append(f"### Alertas ({len(alertas_cid)})")
        if alertas_cid:
            for a in sorted(alertas_cid, key=lambda x: x.get("timestamp", "")):
                nivel = a.get("nivel", "N/A")
                regla = a.get("regla_nombre", a.get("regla_id", "N/A"))
                ts = a.get("timestamp", "")
                msg = a.get("mensaje", "")
                lineas.append(f"- **[{nivel}]** `{ts}` — {regla}: {msg}")
        else:
            lineas.append("- Sin alertas registradas.")
        lineas.append("")

        # --- Historial completo de insights de IA de este alumno ---
        insights_cid = database.obtener_historial_insights(cid)
        lineas.append(f"### Insights de IA ({len(insights_cid)})")
        if insights_cid:
            for ins in insights_cid:
                secciones = ai_insight.parsear_secciones_respuesta(ins["response"])
                lineas.append(f"**Generado:** {ins['created_at']} (modelo: {ins['model']})")
                for i in range(1, 5):
                    lineas.append(f"- Pregunta {i}: {secciones.get(f'pregunta_{i}', '').strip() or '(sin respuesta)'}")
                lineas.append(f"- Conclusión: {secciones.get('conclusion', '').strip() or '(sin conclusión)'}")
                lineas.append("")
        else:
            lineas.append("- No se generó ningún insight para este alumno.")
        lineas.append("")

    return "\n".join(lineas)


@router.post("/reset")
def reset_server():
    """
    Reinicia el servidor para un nuevo examen, en tres etapas estrictamente
    en este orden (nunca se borra nada antes de respaldarlo):
    1. Genera un reporte (alertas + insights por alumno) y un zip con todos
       los .log crudos, devueltos juntos como un único archivo descargable.
    2. Resetea la base de datos (solo insights y comparator_runs — reglas y
       comparator_config NO se tocan, son configuración permanente).
    3. Limpia el estado en memoria y borra los .log de datos_alumnos/.
    """
    timestamp_backup = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- 1. Generar el backup (reporte + logs) ANTES de borrar nada ---
    reporte_md = _generar_reporte_markdown()

    buffer_zip = io.BytesIO()
    with zipfile.ZipFile(buffer_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"reporte_general_{timestamp_backup}.md", reporte_md)
        if os.path.exists(state.CARPETA_DATOS):
            for arch in os.listdir(state.CARPETA_DATOS):
                if arch.endswith(".log"):
                    ruta_log = os.path.join(state.CARPETA_DATOS, arch)
                    zf.write(ruta_log, arcname=f"logs/{arch}")
    buffer_zip.seek(0)
    contenido_zip = buffer_zip.getvalue()

    # --- 2. Resetear la base de datos (solo datos de sesión) ---
    resumen_db = database.resetear_datos_examen()
    print(f"[*] BD reseteada: {resumen_db['insights_borrados']} insights, {resumen_db['runs_borrados']} runs del comparador eliminados.")

    # LogComparator cachea el último resultado en memoria (self.ultimo_resultado),
    # cargado una sola vez desde SQLite al iniciar el servidor. Borrar la fila
    # en comparator_runs no alcanza: sin esto, /api/comparador/resultados seguía
    # devolviendo el análisis viejo hasta la siguiente ejecución del comparador.
    state.comparador_logs.ultimo_resultado = None
    state.comparador_logs.ultimo_analisis_ts = None

    # --- 3. Limpiar estado en memoria y logs en disco ---
    state.comando_global = "ESPERANDO"
    state.clientes_conectados.clear()
    state.historial_alertas.clear()
    state.eventos_telemetria_recientes.clear()
    state.configuraciones_globales.clear()
    state.configuraciones_pendientes.clear()
    state._alumnos_alertados_por_silencio.clear()

    # Los secretos personales de /sync son de ESTE examen: un alumno que
    # reconecte para el próximo (mismo client_id, equipo no reinstalado)
    # vuelve a pasar por /api/discovery y recibe uno nuevo, no el viejo.
    n_secretos = len(state.secretos_por_cliente)
    state.secretos_por_cliente.clear()
    print(f"[*] {n_secretos} secretos de sincronización revocados.")

    # Persistir el estado limpio ahora mismo: si el servidor se reinicia
    # justo después de un reset, no debe resucitar los secretos del examen
    # que recién se cerró.
    state.guardar_estado_servidor()

    if os.path.exists(state.CARPETA_DATOS):
        for arch in os.listdir(state.CARPETA_DATOS):
            if arch.endswith(".log"):
                try:
                    os.remove(os.path.join(state.CARPETA_DATOS, arch))
                except Exception as e:
                    print(f"[!] No se pudo eliminar {arch}: {e}")

    print("[+] Servidor reiniciado para un nuevo examen. Backup entregado al solicitante.")

    nombre_zip = f"backup_examen_{timestamp_backup}.zip"
    return Response(
        content=contenido_zip,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre_zip}"'}
    )