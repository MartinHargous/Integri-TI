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

def extraer_contexto_logs(ruta_log: str, max_lineas: int = 400) -> str:
    """
    Lee y prepara los eventos de telemetría del archivo de log para pasarlos como contexto.
    Prioriza eventos relevantes si el log es muy extenso.
    """
    if not os.path.exists(ruta_log):
        return "No se encontraron registros de telemetría para este alumno."

    lineas_relevantes = []
    total_lineas = 0

    try:
        with open(ruta_log, "r", encoding="utf-8", errors="ignore") as f:
            for l in f:
                l_str = l.strip()
                if not l_str or "--- IGNORE ---" in l_str:
                    continue
                total_lineas += 1
                lineas_relevantes.append(l_str)
    except Exception as e:
        return f"Error leyendo registros: {e}"

    if not lineas_relevantes:
        return "El archivo de registro está vacío."

    # Si el log es muy largo, incluir las primeras y últimas líneas, o filtrar
    if len(lineas_relevantes) > max_lineas:
        mitad = max_lineas // 2
        lineas_seleccionadas = lineas_relevantes[:mitad] + ["... [Eventos intermedios omitidos por extensión] ..."] + lineas_relevantes[-mitad:]
    else:
        lineas_seleccionadas = lineas_relevantes

    return "\n".join(lineas_seleccionadas)

def construir_prompt(client_id: str, contexto_logs: str) -> Tuple[str, str]:
    """
    Construye el system prompt y el user prompt con los logs como contexto y las 4 preguntas requeridas.
    """
    system_prompt = (
        "Eres un analista pedagógico y forense de desarrollo de software para la plataforma educativa Integri-TI. "
        "Tu misión es analizar la telemetría recopilada en la sesión práctica de un estudiante (ejecución de código, "
        "errores y excepciones de Python, navegación web capturada, portapapeles y pulsaciones de teclado). "
        "Debes responder de manera profesional, fundamentada, analítica y objetiva, citando evidencias concretas "
        "de los registros (horas, trazas de error, dominios visitados o patrones de copiado y pegado).\n\n"
        "Debes responder obligatoriamente a las siguientes 4 preguntas estructuradas:\n"
        f"1. {PREGUNTAS_INSIGHT[0]}\n"
        f"2. {PREGUNTAS_INSIGHT[1]}\n"
        f"3. {PREGUNTAS_INSIGHT[2]}\n"
        f"4. {PREGUNTAS_INSIGHT[3]}\n\n"
        "Estructura tu respuesta exactamente con estas cuatro secciones numeradas precedidas de '### 1. ', '### 2. ', "
        "'### 3. ' y '### 4. ', seguidas de un resumen o conclusión pedagógica en '### Conclusión Pedagógica:'."
    )

    user_prompt = (
        f"A continuación se presenta el registro de telemetría forense del estudiante '{client_id}':\n\n"
        f"```telemetry_log\n{contexto_logs}\n```\n\n"
        "Con base exclusivamente en la evidencia cronológica de los registros anteriores, responde detalladamente a las 4 preguntas:\n\n"
        f"1. {PREGUNTAS_INSIGHT[0]}\n\n"
        f"2. {PREGUNTAS_INSIGHT[1]}\n\n"
        f"3. {PREGUNTAS_INSIGHT[2]}\n\n"
        f"4. {PREGUNTAS_INSIGHT[3]}\n\n"
        "Por favor, sustenta cada respuesta con momentos específicos y eventos observados en el log."
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

        # Buscar conclusión (con o sin tilde)
        patron_conclusion = re.compile(r"###\s*Conclusi[oó]n.*?:?\s*(.*?)(?=\n###|\Z)", re.DOTALL | re.IGNORECASE)
        match_conc = patron_conclusion.search(texto_respuesta)
        if match_conc:
            resultado["conclusion"] = match_conc.group(1).strip()

    return resultado

async def solicitar_insight_ia(
    client_id: str,
    ruta_log: str
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
    system_prompt, user_prompt = construir_prompt(client_id, contexto)

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
        async with httpx.AsyncClient(timeout=90.0) as client:
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
