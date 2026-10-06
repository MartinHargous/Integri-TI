import os
import re
import json
import httpx
from typing import Dict, Any, Optional, Tuple, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def cargar_env(ruta_env: Optional[str] = None):
    """Carga variables desde archivo .env si existen sin sobrescribir variables ya exportadas."""
    if ruta_env is None:
        ruta_env = os.path.join(BASE_DIR, ".env")
    if os.path.exists(ruta_env):
        try:
            with open(ruta_env, "r", encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea or linea.startswith("#") or "=" not in linea:
                        continue
                    k, v = linea.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception as e:
            print(f"[!] Error leyendo .env: {e}")

cargar_env()

def obtener_config_ia() -> Dict[str, str]:
    cargar_env()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "luna").strip() or "luna"
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    return {
        "api_key": api_key,
        "model": model,
        "base_url": base_url
    }

PREGUNTAS_INSIGHT = [
    "¿Qué concepto técnico, sintáctico o lógico específico está intentando resolver el estudiante (por ejemplo, recursividad, iteración de diccionarios o manejo de condicionales)?",
    "¿Existe una relación directa entre el error de ejecución recurrente y la búsqueda web realizada inmediatamente después en dominios no autorizados?",
    "¿El alumno intentó depurar el código modificando la lógica por sus propios medios antes de recurrir a fuentes externas, o buscó la respuesta de forma inmediata tras el primer error?",
    "Tras realizar la consulta en la red o insertar un nuevo bloque de código, ¿logró el estudiante resolver la excepción y avanzar en el desarrollo, o continuó generando la misma traza de error?"
]

def leer_lineas_log(ruta_log: str) -> List[str]:
    """
    Lee el log del alumno sin tope de líneas (el contexto de los modelos usados
    cubre con holgura una sesión completa: ~15-30 k tokens por hora de examen).
    Descarta líneas vacías y los latidos '--- IGNORE ---' del keylogger.
    """
    lineas = []
    with open(ruta_log, "r", encoding="utf-8", errors="ignore") as f:
        for l in f:
            l_str = l.strip()
            if not l_str or "--- IGNORE ---" in l_str:
                continue
            lineas.append(l_str)
    return lineas


def extraer_contexto_logs(ruta_log: str) -> str:
    """Devuelve el log completo del alumno como texto, listo para el prompt."""
    if not os.path.exists(ruta_log):
        return "No se encontraron registros de telemetría para este alumno."
    try:
        lineas = leer_lineas_log(ruta_log)
    except Exception as e:
        return f"Error leyendo registros: {e}"
    if not lineas:
        return "El archivo de registro está vacío."
    return "\n".join(lineas)


# Canales que en una sesión normal producen eventos. Sirve para que el modelo
# distinga "no hubo actividad" de "el sensor no estaba capturando".
SENSORES_ESPERADOS = ["Program Monitor", "Keylogger", "Sniffer", "Paperclip", "Usb Detection", "Auditoria Python"]
_RE_MODULO = re.compile(r"^\[([^\]]*)\]\[([^\]]+)\]")
_ORDEN_SEVERIDAD = {"CRÍTICA": 0, "CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3}


def resumir_sensores(lineas: List[str]) -> str:
    """Eventos por canal y periodo cubierto, con la advertencia de sensores sin datos."""
    conteo: Dict[str, int] = {}
    nombres: Dict[str, str] = {}
    primero = ultimo = None
    for l in lineas:
        m = _RE_MODULO.match(l)
        if not m:
            continue
        ts, mod = m.group(1), m.group(2).strip()
        clave = mod.lower()
        nombres.setdefault(clave, mod)
        conteo[clave] = conteo.get(clave, 0) + 1
        primero = primero or ts
        ultimo = ts

    salida = ["RESUMEN DE SENSORES (eventos registrados por canal):"]
    for esperado in SENSORES_ESPERADOS:
        conteo.setdefault(esperado.lower(), 0)
        nombres.setdefault(esperado.lower(), esperado)
    for clave in sorted(conteo, key=lambda k: -conteo[k]):
        n, nombre = conteo[clave], nombres[clave]
        if n > 0:
            salida.append(f"- {nombre}: {n} eventos")
        elif clave == "sniffer":
            salida.append(
                f"- {nombre}: 0 eventos  <- en una sesión normal registra cientos de dominios; "
                "0 indica que probablemente NO estuvo capturando. La ausencia de dominios NO prueba "
                "que no hubo navegación ni tráfico de red."
            )
        elif clave in {s.lower() for s in SENSORES_ESPERADOS}:
            salida.append(f"- {nombre}: 0 eventos  <- puede ser ausencia de actividad o que el sensor no capturó.")
    if primero and ultimo:
        try:
            from datetime import datetime
            t0 = datetime.strptime(primero, "%Y-%m-%dT%H:%M:%S")
            t1 = datetime.strptime(ultimo, "%Y-%m-%dT%H:%M:%S")
            salida.append(f"Periodo cubierto: {primero} a {ultimo} ({int((t1 - t0).total_seconds() // 60)} min)")
        except Exception:
            salida.append(f"Periodo cubierto: {primero} a {ultimo}")
    return "\n".join(salida)


def formatear_alertas(alertas: Optional[List[Dict[str, Any]]]) -> str:
    """
    Resume las detecciones del motor de reglas, agrupadas por regla (una línea por
    regla, con conteo y horas) para no inundar el prompt cuando una regla repite.
    Se cita por hora, no por número de línea: el modelo no ve números de línea.
    """
    cabecera = (
        "HALLAZGOS DEL MOTOR DE REGLAS (detección automática por patrones; úsalos como índice de "
        "dónde mirar y confírmalos con las líneas del log antes de afirmarlos):"
    )
    if not alertas:
        return cabecera + "\n- Ninguno."
    grupos: Dict[str, List[Dict[str, Any]]] = {}
    for a in alertas:
        grupos.setdefault(a.get("regla_id", "?"), []).append(a)

    def clave(item):
        _, lst = item
        sev = _ORDEN_SEVERIDAD.get(str(lst[0].get("nivel", "")).upper(), 9)
        return (sev, min(x.get("timestamp", "") for x in lst))

    filas = [cabecera]
    for rid, lst in sorted(grupos.items(), key=clave):
        horas = sorted(x.get("timestamp", "")[11:19] for x in lst)
        muestra = ", ".join(horas[:4]) + (f" … última {horas[-1]}" if len(horas) > 4 else "")
        filas.append(f"- [{str(lst[0].get('nivel', '')).upper()}] {rid} {lst[0].get('regla_nombre', '')}: "
                     f"{len(lst)} detección(es) ({muestra})")
    return "\n".join(filas)


POLITICA_POR_DEFECTO = (
    "Se permite internet para acceder a la plataforma canvas para ver y entregar el examen, no para navegación general. NO se permite asistencia de IA generativa (chatbots ni agentes de código) ni de otras personas."
)


def construir_prompt(
    client_id: str,
    contexto_logs: str,
    resumen_evidencia: str = "",
    politica: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Construye el system prompt y el user prompt: el log completo, un resumen de
    sensores, los hallazgos del motor de reglas, y las 4 preguntas requeridas.
    """
    
    bloque_politica = f"POLÍTICA DEL EXAMEN: {POLITICA_POR_DEFECTO}"

    system_prompt = (
        "Eres un analista pedagógico y forense de desarrollo de software para la plataforma educativa Integri-TI. "
        "Analizas la telemetría de la sesión de un estudiante durante un examen de programación en su propio equipo "
        "(ejecución de código, errores de Python, ventanas en primer plano, red, portapapeles y pulsaciones de teclado). "
        "Respondes de manera profesional y fundamentada, citando evidencia concreta del registro (hora y contenido).\n\n"
        f"{bloque_politica}\n\n"
        "REGLAS DE REDACCIÓN (obligatorias):\n"
        "1. Distingue tres niveles y úsalos explícitamente: HECHO (aparece en el registro; cita la hora), "
        "INFERENCIA (se deduce razonablemente de varios hechos; márcala como tal) y NO DETERMINABLE "
        "(el registro no contiene datos para saberlo).\n"
        "2. Afirma los hechos con firmeza y sin muletillas. No escribas \"no es comprobable\", \"no se puede asegurar\" "
        "ni similares sobre algo que el registro muestra directamente. Ejemplo: si el estudiante escribe \"opencode\" y Enter "
        "y a continuación redacta instrucciones en lenguaje natural, el hecho es que utilizó un agente de IA de programación; "
        "no es una hipótesis.\n"
        "3. Reserva la cautela únicamente para lo que el registro NO contiene: el contenido de lo escrito en aplicaciones "
        "externas, la identidad del interlocutor, el origen exacto de un bloque de código, o los motivos del estudiante.\n"
        "4. No especules sobre intenciones ni emitas juicios morales (\"mala fe\", \"trampa\", \"malas intenciones\"). Describe la "
        "conducta observada y evalúala solo contra la política indicada arriba. No repitas advertencias genéricas sobre intenciones.\n"
        "5. Un canal sin eventos no es evidencia de ausencia de actividad: consulta el resumen de sensores.\n"
        "6. Los hallazgos del motor de reglas son detecciones automáticas por patrones y pueden tener falsos positivos: "
        "úsalos como guía y confírmalos con las líneas del registro antes de afirmarlos.\n\n"
        "Debes responder obligatoriamente a las siguientes 4 preguntas estructuradas:\n"
        f"1. {PREGUNTAS_INSIGHT[0]}\n"
        f"2. {PREGUNTAS_INSIGHT[1]}\n"
        f"3. {PREGUNTAS_INSIGHT[2]}\n"
        f"4. {PREGUNTAS_INSIGHT[3]}\n\n"
        "Estructura tu respuesta exactamente con estas cuatro secciones numeradas precedidas de '### 1. ', '### 2. ', "
        "'### 3. ' y '### 4. ', seguidas de '### Conclusión:'. En la Conclusión, enumera los recursos externos y "
        "herramientas de IA que el registro evidencia (con la hora de la primera y la última evidencia de cada uno), "
        "evalúalos contra la política y señala qué no puede determinarse con este registro."
    )

    evidencia = (resumen_evidencia.strip() + "\n\n") if resumen_evidencia else ""
    user_prompt = (
        f"A continuación se presenta el registro de telemetría forense del estudiante '{client_id}':\n\n"
        f"{evidencia}"
        f"```telemetry_log\n{contexto_logs}\n```\n\n"
        "Con base exclusivamente en la evidencia cronológica de los registros anteriores, responde detalladamente a las 4 preguntas:\n\n"
        f"1. {PREGUNTAS_INSIGHT[0]}\n\n"
        f"2. {PREGUNTAS_INSIGHT[1]}\n\n"
        f"3. {PREGUNTAS_INSIGHT[2]}\n\n"
        f"4. {PREGUNTAS_INSIGHT[3]}\n\n"
        "Sustenta cada respuesta con momentos específicos y eventos observados en el log."
    )

    return system_prompt, user_prompt


def parsear_secciones_respuesta(texto_respuesta: str) -> Dict[str, Any]:
    """
    Separa la respuesta en las 4 preguntas pedagógicas y la conclusión si están presentes.
    """
    resultado = {
        "pregunta_1": "",
        "pregunta_2": "",
        "pregunta_3": "",
        "pregunta_4": "",
        "conclusion": "",
        "texto_completo": texto_respuesta
    }

    patron_secciones = re.compile(r"###\s*(\d)\.?\s*(.*?)(?=\n###|\Z)", re.DOTALL)
    coincidencias = patron_secciones.findall(texto_respuesta)

    if coincidencias:
        for num_str, contenido in coincidencias:
            contenido_limpio = contenido.strip()
            # Remover la pregunta repetida si el modelo la incluyó en la primera línea
            lineas = contenido_limpio.splitlines()
            if len(lineas) > 1 and ("¿" in lineas[0] or "?" in lineas[0]):
                contenido_limpio = "\n".join(lineas[1:]).strip()

            clave = f"pregunta_{num_str}"
            if clave in resultado:
                resultado[clave] = contenido_limpio

        # Buscar conclusión (con o sin tilde) y omitir encabezados como "Pedagógica:"
        patron_conclusion = re.compile(r"###\s*Conclusi[oó]n[^\n:]*:?\s*(.*?)(?=\n###|\Z)", re.DOTALL | re.IGNORECASE)
        match_conc = patron_conclusion.search(texto_respuesta)
        if match_conc:
            texto_conc = match_conc.group(1).strip()
            texto_conc = re.sub(r"^(?:pedag[oó]gica?|general)?\s*:?\s*", "", texto_conc, flags=re.IGNORECASE).strip()
            resultado["conclusion"] = texto_conc

    return resultado

async def solicitar_insight_ia(
    client_id: str,
    ruta_log: str,
    alertas: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Ejecuta la llamada a la API de ChatGPT / Modelo Luna enviando los logs como contexto
    y las 4 preguntas pedagógicas.
    """
    config = obtener_config_ia()
    api_key = config["api_key"]
    model = config["model"]
    base_url = config["base_url"]

    if not api_key:
        return {
            "status": "error",
            "mensaje": "La clave de API (OPENAI_API_KEY) no está configurada. Agrégala en el archivo Server/.env"
        }

    contexto = extraer_contexto_logs(ruta_log)
    try:
        lineas = leer_lineas_log(ruta_log)
    except Exception:
        lineas = []
    resumen = resumir_sensores(lineas) + "\n\n" + formatear_alertas(alertas)
    system_prompt, user_prompt = construir_prompt(
        client_id, contexto, resumen_evidencia=resumen or None
    )

    endpoint = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }

    try:
        # Sin tope de líneas el prompt es más grande y los modelos con razonamiento pueden tardar más de 90 s.
        timeout_seg = float(os.environ.get("OPENAI_TIMEOUT_SEGUNDOS", "300") or 300)
        async with httpx.AsyncClient(timeout=timeout_seg) as client:
            res = await client.post(endpoint, json=payload, headers=headers)
            if res.status_code == 401:
                return {
                    "status": "error",
                    "mensaje": "Error de autenticación con la API: API Key inválida o no autorizada."
                }
            elif res.status_code != 200:
                return {
                    "status": "error",
                    "mensaje": f"Error del proveedor ({res.status_code}): {res.text}"
                }

            data = res.json()
            contenido = data["choices"][0]["message"]["content"]

            secciones = parsear_secciones_respuesta(contenido)

            return {
                "status": "ok",
                "client_id": client_id,
                "model": model,
                "prompt": f"SYSTEM PROMPT:\n{system_prompt}\n\nUSER PROMPT:\n{user_prompt}",
                "response": contenido,
                "secciones": secciones,
                "raw_api": data
            }
    except httpx.ConnectError:
        return {
            "status": "error",
            "mensaje": f"No fue posible conectar con el endpoint '{base_url}'. Verifica la conexión de red o la URL en .env."
        }
    except httpx.TimeoutException:
        return {
            "status": "error",
            "mensaje": f"Tiempo de espera agotado al consultar el modelo '{model}'. Inténtalo nuevamente."
        }
    except Exception as e:
        return {
            "status": "error",
            "mensaje": f"Excepción durante la generación del insight: {str(e)}"
        }