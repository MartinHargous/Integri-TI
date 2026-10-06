"""
Endpoints principales del agente (informe de arquitectura, sección 5.1.1):
GET /api/discovery, POST /sync, GET /profesor/comando/{nuevo_comando}.
"""
import os
import json
import pyotp
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException, Depends

import state
from security import solo_local

router = APIRouter()


@router.post("/sync")
async def recibir_telemetria(
    request: Request,
    client_id: str = Form(...),
    estado_local: str = Form(...),
    timestamp: str = Form(...),
    alertas: str = Form("[]"),
    configs: str = Form("{}"),
    archivo_log: UploadFile = File(None),
    codigo_otp: str = Form(...)
):
    # Cada client_id tiene su propio secreto, emitido en /api/discovery — no
    # hay un secreto global que verificar. Si este client_id nunca pasó por
    # discovery (o su secreto ya no existe porque hubo un /reset de por medio),
    # no hay nada contra qué validar: 401 directo, sin intentar nada más.
    secreto_cliente = state.secretos_por_cliente.get(client_id)
    if not secreto_cliente:
        raise HTTPException(
            status_code=401,
            detail="Este equipo no está inscrito en el examen actual. El agente debe volver a pasar por el descubrimiento del servidor (reinícielo)."
        )

    # valid_window=2 tolera hasta ~60s de desfase de reloj entre agente y
    # servidor en cada dirección (ventana total ~150s), además de la latencia
    # normal de red. Si un client_id falla sistemáticamente aquí, revisar la
    # hora del sistema de ese equipo antes que sospechar del secreto.
    if not pyotp.TOTP(secreto_cliente, interval=30).verify(codigo_otp, valid_window=2):
        raise HTTPException(
            status_code=401,
            detail="Código de sincronización inválido o expirado. Si esto persiste para el mismo equipo, verifique que su hora del sistema esté correcta."
        )

    ip_cliente = request.client.host if request.client else "127.0.0.1"
    ruta_destino = os.path.join(state.CARPETA_DATOS, f"{client_id}.log")

    # --- 0. Detección de silencio sospechoso (agente interrumpido a mitad de GRABANDO) ---
    # No es una regla del correlator: no depende del CONTENIDO del log, sino del
    # tiempo real transcurrido entre este /sync y el anterior. El correlator nunca
    # ve "lo que no llegó", así que esto se calcula aquí, con lo que el propio
    # servidor ya sabe (clientes_conectados). Se registra como una línea normal
    # en el .log del alumno para que quede visible en la auditoría forense igual
    # que cualquier evento de módulo, y también en el feed de alertas del dashboard.
    info_previa = state.clientes_conectados.get(client_id)
    if info_previa and info_previa.get("estado") == "GRABANDO":
        gap_seg = None
        try:
            ultimo_dt = datetime.strptime(info_previa.get("ultimo_visto", ""), "%Y-%m-%dT%H:%M:%S")
            ahora_dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
            gap_seg = (ahora_dt - ultimo_dt).total_seconds()
        except Exception:
            gap_seg = None

        if gap_seg is not None and gap_seg > state.UMBRAL_GAP_SEGUNDOS:
            gap_int = int(gap_seg)
            linea_alerta = f"[{timestamp}][Sistema] AGENTE_INTERRUMPIDO gap={gap_int}s\n"
            try:
                with open(ruta_destino, "a", encoding="utf-8") as f_gap:
                    f_gap.write(linea_alerta)
            except Exception as e:
                print(f"[!] No se pudo escribir la alerta de interrupción en el log de {client_id}: {e}")

            print(f"\n[ALERTA SISTEMA - {client_id}] AGENTE_INTERRUMPIDO gap={gap_int}s")
            state.historial_alertas.insert(0, {
                "client_id": client_id,
                "timestamp": timestamp,
                "nivel": "Alta",
                "regla_id": "SISTEMA",
                "regla_nombre": "Interrupción del Agente",
                "mensaje": f"El agente dejó de reportar por {gap_int}s durante el examen (posible cierre manual del proceso)."
            })
            if len(state.historial_alertas) > 100:
                state.historial_alertas.pop()

        # El alumno volvió a responder: si el watchdog en vivo ya lo había
        # marcado como en silencio, se limpia para que un futuro corte
        # (otro más adelante en el mismo examen) pueda alertar de nuevo.
        state._alumnos_alertados_por_silencio.discard(client_id)

    # 1. Guardar el archivo si el alumno envió uno y correlacionar secuencias de logs
    bytes_log = 0
    if archivo_log and archivo_log.filename:
        contenido = await archivo_log.read()
        bytes_log = len(contenido)

        # Se cuenta DESPUÉS de la posible escritura de la alerta de interrupción
        # de arriba, para que el offset del correlator quede correcto si esa
        # línea sintética ya se agregó al archivo en este mismo request.
        lineas_previas = 0
        if os.path.exists(ruta_destino):
            try:
                with open(ruta_destino, "rb") as f_prev:
                    lineas_previas = sum(1 for _ in f_prev)
            except Exception:
                lineas_previas = state.correlador.total_lineas_por_cliente.get(client_id, 0)

        with open(ruta_destino, "ab") as f:
            f.write(contenido)

        # Analizar las nuevas líneas a través del motor de correlación secuencial con offset de línea exacto
        texto_lineas = contenido.decode("utf-8", errors="ignore").splitlines()
        alertas_correlacion = state.correlador.procesar_nuevos_eventos(client_id, texto_lineas, offset_lineas=lineas_previas)
        for a in alertas_correlacion:
            print(f"\n[ALERTA SECUENCIAL DISPARADA - {client_id}] {a['nivel']}: {a['mensaje']}")
            state.historial_alertas.insert(0, a)
            if len(state.historial_alertas) > 100:
                state.historial_alertas.pop()

        for l in texto_lineas[-10:]:
            l_str = l.strip()
            if l_str and "--- IGNORE ---" not in l_str:
                state.eventos_telemetria_recientes.append({
                    "client_id": client_id,
                    "timestamp": timestamp,
                    "nivel": "Info",
                    "mensaje": l_str
                })

    # 2. Parsear configuraciones reportadas por HTTP desde el cliente remoto
    configs_recibidas = {}
    try:
        if configs and configs.strip():
            configs_recibidas = json.loads(configs)
    except Exception as e:
        print(f"Error decodificando configs de {client_id}: {e}")

    # 3. Registrar o actualizar cliente en clientes_conectados
    prev_configs = state.clientes_conectados.get(client_id, {}).get("configs", {})
    state.clientes_conectados[client_id] = {
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
                state.historial_alertas.insert(0, {
                    "client_id": client_id,
                    "timestamp": alerta.get("timestamp", timestamp),
                    "nivel": alerta.get("nivel", "Media"),
                    "regla_id": "CLIENTE",
                    "regla_nombre": "Alerta Local de Agente",
                    "mensaje": alerta.get("mensaje", "")
                })
                if len(state.historial_alertas) > 100:
                    state.historial_alertas.pop()
    except Exception as e:
        print(f"Error procesando alertas: {e}")

    # 5. Preparar configuraciones pendientes para enviar por HTTP al cliente
    configs_a_enviar = {}
    if client_id in state.configuraciones_pendientes:
        configs_a_enviar.update(state.configuraciones_pendientes.pop(client_id))

    if state.configuraciones_globales:
        for mod, cambios in state.configuraciones_globales.items():
            cliente_mod = configs_recibidas.get(mod, {})
            if not cliente_mod:
                for k_c, v_c in configs_recibidas.items():
                    if k_c.replace(" ", "_") == mod.replace(" ", "_"):
                        cliente_mod = v_c
                        break
            cambios_faltantes = {}
            for k, v in cambios.items():
                if str(cliente_mod.get(k, "")).strip().lower() != str(v).strip().lower():
                    cambios_faltantes[k] = v
            if cambios_faltantes:
                if mod not in configs_a_enviar:
                    configs_a_enviar[mod] = {}
                configs_a_enviar[mod].update(cambios_faltantes)

    return {
        "comando_global": state.comando_global,
        "configuraciones": configs_a_enviar
    }


# Este endpoint vive en este router por cómo está categorizado en el informe
# (5.1.1), pero es del profesor, no de los agentes: por eso, a diferencia de
# /sync y /api/discovery, exige acceso local.
@router.get("/profesor/comando/{nuevo_comando}", dependencies=[Depends(solo_local)])
def cambiar_estado_clase(nuevo_comando: str):
    comandos_validos = ["ESPERANDO", "GRABANDO", "FINALIZADO"]
    comando_upper = nuevo_comando.upper()

    if comando_upper in comandos_validos:
        state.comando_global = comando_upper
        print(f"\n[+] COMANDO GLOBAL CAMBIADO A: {state.comando_global}")
        try:
            state.comparador_logs.establecer_estado_examen(state.comando_global)
        except Exception as e:
            print(f"[!] Error actualizando estado de examen en comparador: {e}")
        state.guardar_estado_servidor()
        return {"status": "OK", "comando_actual": state.comando_global}
    return {"status": "ERROR"}


@router.get("/api/discovery")
def verificar_descubrimiento(request: Request, client_id: str):
    """
    Descubrimiento Y matrícula en un solo paso: un agente que pasa esta
    compuerta recibe (o recupera, si ya la tenía) su propia clave personal
    para /sync — ver el comentario de dos niveles en state.py.

    Dos condiciones, en este orden:
    1. El header X-Codigo-Distribucion debe coincidir con el de este repo.
       Filtra tráfico externo que nunca clonó el proyecto; no se envía en la
       URL para no quedar en logs de proxies/CDN (ver Cloudflare).
    2. El servidor debe estar en ESPERANDO (igual que antes) — fuera de esa
       ventana no se inscriben agentes nuevos, haya o no código correcto.

    Si un client_id repite la llamada (reintento, reconexión), recibe el
    MISMO secreto que la vez anterior — no se reemite uno nuevo cada vez.
    """
    codigo_recibido = request.headers.get("x-codigo-distribucion", "")
    if codigo_recibido != state.CODIGO_DISTRIBUCION:
        raise HTTPException(status_code=403, detail="Código de distribución inválido.")

    if state.comando_global != "ESPERANDO":
        raise HTTPException(
            status_code=403,
            detail=f"Servidor en estado {state.comando_global}. Solo se aceptan y establecen conexiones cuando el servidor está en ESPERANDO."
        )

    if client_id not in state.secretos_por_cliente:
        state.secretos_por_cliente[client_id] = pyotp.random_base32()
        state.guardar_estado_servidor()  # solo al emitir uno NUEVO, no en cada discovery repetido

    return {
        "status": "ready",
        "comando_global": state.comando_global,
        "secreto_sync": state.secretos_por_cliente[client_id],
        "mensaje": "Servidor listo. Secreto de sincronización asignado."
    }