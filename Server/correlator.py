import re
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

class LogCorrelator:
    """
    Motor de Correlación Secuencial de Logs para Integri-TI.
    Analiza telemetría en tiempo real buscando secuencias sospechosas de eventos
    (ej: foco en navegador -> consulta a IA -> pegado de código en editor)
    dentro de una ventana temporal definida.
    """

    PATRON_LINEA = re.compile(r"^\[(.*?)\]\[(.*?)\]\s*(.*)$")

    REGLAS_DEFAULT = [
        {
            "id": "R-01",
            "nombre": "Consulta a IA y Pegado de Código",
            "severidad": "CRÍTICA",
            "ventana_segundos": 60,
            "pasos": [
                {"modulo": "keylogger", "patron": r"\[CTRL\]\+c"},
                {"modulo": "sniffer", "patron": r"gemini\.google\.com|chatgpt\.com|claude\.ai|deepseek"},
                {"modulo": "keylogger", "patron": r"\[CTRL\]\+v"}
            ]
        },
        {
            "id": "R-02",
            "nombre": "Copia a Portapapeles y Acceso a IA/Web",
            "severidad": "ALTA",
            "ventana_segundos": 45,
            "pasos": [
                {"modulo": "paperclip", "patron": r"CLIPBOARD_CHANGED"},
                {"modulo": "sniffer", "patron": r"chatgpt\.com|gemini\.google\.com|claude\.ai"}
            ]
        },
        {
            "id": "R-03",
            "nombre": "Exfiltración vía Portapapeles a Mensajería",
            "severidad": "ALTA",
            "ventana_segundos": 30,
            "pasos": [
                {"modulo": "paperclip", "patron": r"CLIPBOARD_CHANGED"},
                {"modulo": "sniffer", "patron": r"discord\.com|miro\.com|chat\.google\.com|telegram|whatsapp"}
            ]
        },
        {
            "id": "R-04",
            "nombre": "Crash en Código seguido de Búsqueda Externa",
            "severidad": "MEDIA",
            "ventana_segundos": 60,
            "pasos": [
                {"modulo": "error_detection", "patron": r"CRASH DETECTADO"},
                {"modulo": "sniffer", "patron": r"chatgpt\.com|gemini\.google\.com|google\.com|stackoverflow\.com"}
            ]
        },
        {
            "id": "R-05",
            "nombre": "Desvío a Navegador Externo durante Ejecución",
            "severidad": "MEDIA",
            "ventana_segundos": 30,
            "pasos": [
                {"modulo": "program_monitor", "patron": r"CONTEXT_CHANGE.*app='(firefox|chrome|msedge)\.exe'"},
                {"modulo": "keylogger", "patron": r"\[CTRL\]\+v"}
            ]
        }
    ]

    def __init__(self, ruta_reglas: str = "reglas.json"):
        self.ruta_reglas = ruta_reglas
        self.reglas: List[Dict[str, Any]] = []

        try:
            import database
            database.inicializar_db(self.ruta_reglas)
        except Exception as e:
            print(f"[!] Advertencia inicializando base de datos SQLite: {e}")

        self.cargar_reglas()

        # Buffer en memoria por cliente: { client_id: list of parsed events }
        self.historial_eventos_por_cliente: Dict[str, List[Dict[str, Any]]] = {}

        # Registro de último disparo para evitar duplicar alertas sobre la misma secuencia:
        # { client_id: { regla_id: ultimo_timestamp_disparado } }
        self.ultimo_disparo: Dict[str, Dict[str, float]] = {}

    def normalizar_modulo(self, modulo: str) -> str:
        mod = modulo.strip().lower()
        if mod in ("program monitor", "program_monitor"):
            return "program_monitor"
        elif mod in ("auditoria python", "error_detection", "error detection"):
            return "error_detection"
        elif mod in ("keystrokes svm", "keystroke_svm", "svm"):
            return "svm"
        elif mod == "sniffer":
            return "sniffer"
        elif mod == "keylogger":
            return "keylogger"
        elif mod == "paperclip":
            return "paperclip"
        return mod

    def parsear_timestamp(self, ts_str: str) -> float:
        ts_str = ts_str.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(ts_str, fmt).timestamp()
            except ValueError:
                pass
        return time.time()

    def parsear_linea(self, linea: str, numero_linea: int = 0, ignorar_heartbeats: bool = False) -> Optional[Dict[str, Any]]:
        linea = linea.strip()
        if not linea:
            return None
        if ignorar_heartbeats and "--- IGNORE ---" in linea:
            return None
        m = self.PATRON_LINEA.match(linea)
        if not m:
            return None
        ts_str, mod_str, cont_str = m.groups()
        mod_norm = self.normalizar_modulo(mod_str)
        return {
            "numero_linea": numero_linea,
            "ts": self.parsear_timestamp(ts_str),
            "ts_str": ts_str,
            "modulo": mod_norm,
            "modulo_orig": mod_str.strip(),
            "contenido": cont_str.strip(),
            "linea_raw": linea
        }

    def cargar_reglas(self):
        try:
            import database
            reglas_db = database.obtener_todas_las_reglas()
            if reglas_db:
                self.reglas = reglas_db
                print(f"[*] {len(self.reglas)} reglas de correlación cargadas desde SQLite")
                return
        except Exception as e:
            print(f"[!] Error leyendo reglas desde SQLite: {e}")

        # Fallback a JSON si la base de datos no tuviese registros
        if os.path.exists(self.ruta_reglas):
            try:
                with open(self.ruta_reglas, "r", encoding="utf-8") as f:
                    self.reglas = json.load(f)
                print(f"[*] {len(self.reglas)} reglas de correlación cargadas desde {self.ruta_reglas}")
                return
            except Exception as e:
                print(f"[!] Error leyendo {self.ruta_reglas}: {e}. Usando reglas por defecto.")
        
        self.reglas = list(self.REGLAS_DEFAULT)

    def guardar_reglas(self):
        # Las reglas ahora persisten en SQLite, manteniendo reglas.json como defaults intacto
        pass

    def obtener_reglas(self) -> List[Dict[str, Any]]:
        try:
            import database
            self.reglas = database.obtener_todas_las_reglas()
        except Exception:
            pass
        return self.reglas

    def agregar_o_actualizar_regla(self, nueva_regla: Dict[str, Any]) -> Dict[str, Any]:
        regla_id = nueva_regla.get("id")
        if not regla_id:
            # Generar siguiente ID
            max_num = 0
            for r in self.reglas:
                m = re.search(r"\d+", r.get("id", ""))
                if m:
                    max_num = max(max_num, int(m.group(0)))
            regla_id = f"R-{max_num + 1:02d}"
            nueva_regla["id"] = regla_id

        # Normalizar pasos
        pasos_norm = []
        for p in nueva_regla.get("pasos", []):
            pasos_norm.append({
                "modulo": self.normalizar_modulo(p.get("modulo", "")),
                "patron": p.get("patron", "")
            })
        nueva_regla["pasos"] = pasos_norm
        nueva_regla["ventana_segundos"] = int(nueva_regla.get("ventana_segundos", 30))

        # Guardar en SQLite
        try:
            import database
            database.guardar_o_actualizar_regla(nueva_regla)
        except Exception as e:
            print(f"[!] Error guardando regla en SQLite: {e}")

        # Reemplazar si existe o agregar en memoria
        for i, r in enumerate(self.reglas):
            if r.get("id") == regla_id:
                self.reglas[i] = nueva_regla
                return nueva_regla

        self.reglas.append(nueva_regla)
        return nueva_regla

    def eliminar_regla(self, regla_id: str) -> bool:
        eliminado_db = False
        try:
            import database
            eliminado_db = database.eliminar_regla_db(regla_id)
        except Exception as e:
            print(f"[!] Error eliminando regla en SQLite: {e}")

        inicial = len(self.reglas)
        self.reglas = [r for r in self.reglas if r.get("id") != regla_id]
        return eliminado_db or (len(self.reglas) < inicial)

    def coincide_paso(self, paso: Dict[str, Any], evento: Dict[str, Any]) -> bool:
        mod_paso = self.normalizar_modulo(paso.get("modulo", ""))
        if mod_paso and mod_paso not in ("cualquiera", "*", "todos"):
            if mod_paso != evento["modulo"]:
                return False

        patron = paso.get("patron", "")
        if not patron:
            return True

        try:
            return bool(re.search(patron, evento["contenido"], re.IGNORECASE))
        except Exception:
            return patron.lower() in evento["contenido"].lower()

    def correlacionar_secuencia(self, eventos: List[Dict[str, Any]], regla: Dict[str, Any], t_min_permitido: float = 0.0) -> List[Dict[str, Any]]:
        pasos = regla.get("pasos", [])
        if not pasos:
            return []

        ventana = float(regla.get("ventana_segundos", 30))
        num_pasos = len(pasos)
        alertas = []
        
        def buscar_paso(idx_paso: int, idx_evento_inicio: int, camino: List[Dict[str, Any]]):
            paso_actual = pasos[idx_paso]
            
            for i in range(idx_evento_inicio, len(eventos)):
                ev = eventos[i]
                
                if camino:
                    if ev["ts"] < camino[0]["ts"]:
                        continue
                    if (ev["ts"] - camino[0]["ts"]) > ventana:
                        break
                    if ev["ts"] < camino[-1]["ts"]:
                        continue

                if self.coincide_paso(paso_actual, ev):
                    nuevo_camino = camino + [ev]
                    
                    if idx_paso + 1 == num_pasos:
                        t_primero = nuevo_camino[0]["ts"]
                        t_ultimo = nuevo_camino[-1]["ts"]
                        
                        if t_ultimo > t_min_permitido:
                            delta_seg = round(t_ultimo - t_primero, 1)
                            resumen_pasos = " -> ".join([
                                f"{e['modulo']}: {e['contenido'][:35]}" for e in nuevo_camino
                            ])
                            lineas_afectadas = [e.get("numero_linea", 0) for e in nuevo_camino if e.get("numero_linea", 0) > 0]
                            alertas.append({
                                "t_disparo": t_ultimo,
                                "timestamp": nuevo_camino[-1]["ts_str"],
                                "nivel": regla.get("severidad", "ALTA"),
                                "regla_id": regla.get("id"),
                                "regla_nombre": regla.get("nombre"),
                                "delta_segundos": delta_seg,
                                "lineas_afectadas": lineas_afectadas,
                                "linea_inicio": lineas_afectadas[0] if lineas_afectadas else 0,
                                "linea_fin": lineas_afectadas[-1] if lineas_afectadas else 0,
                                "mensaje": f"{regla.get('nombre', 'Secuencia')}: [{resumen_pasos}] en {delta_seg}s"
                            })
                    else:
                        buscar_paso(idx_paso + 1, i + 1, nuevo_camino)

        buscar_paso(0, 0, [])
        return alertas

    def procesar_nuevos_eventos(self, client_id: str, lineas: List[str]) -> List[Dict[str, Any]]:
        """
        Parsea un lote de nuevas líneas recibidas para un cliente,
        las agrega al buffer y evalúa todas las reglas activas.
        Retorna alertas generadas.
        """
        if client_id not in self.historial_eventos_por_cliente:
            self.historial_eventos_por_cliente[client_id] = []
        if client_id not in self.ultimo_disparo:
            self.ultimo_disparo[client_id] = {}

        # Calcular número de línea aproximado
        offset_lineas = len(self.historial_eventos_por_cliente[client_id])
        nuevos = []
        for idx, l in enumerate(lineas, start=offset_lineas + 1):
            ev = self.parsear_linea(l, numero_linea=idx, ignorar_heartbeats=True)
            if ev:
                nuevos.append(ev)

        if not nuevos:
            return []

        # Agregar y ordenar por timestamp
        self.historial_eventos_por_cliente[client_id].extend(nuevos)
        self.historial_eventos_por_cliente[client_id].sort(key=lambda x: x["ts"])

        # Mantener tamaño razonable (últimos 1500 eventos)
        if len(self.historial_eventos_por_cliente[client_id]) > 1500:
            self.historial_eventos_por_cliente[client_id] = self.historial_eventos_por_cliente[client_id][-1500:]

        eventos_cliente = self.historial_eventos_por_cliente[client_id]
        alertas_generadas = []

        for regla in self.reglas:
            regla_id = regla.get("id", "")
            ultimo_t = self.ultimo_disparo[client_id].get(regla_id, 0.0)

            hallazgos = self.correlacionar_secuencia(eventos_cliente, regla, t_min_permitido=ultimo_t)
            
            if hallazgos:
                ultima_alerta = hallazgos[-1]
                self.ultimo_disparo[client_id][regla_id] = ultima_alerta["t_disparo"]
                
                alerta_formato = {
                    "client_id": client_id,
                    "timestamp": ultima_alerta["timestamp"],
                    "nivel": ultima_alerta["nivel"],
                    "regla_id": ultima_alerta["regla_id"],
                    "regla_nombre": ultima_alerta["regla_nombre"],
                    "lineas_afectadas": ultima_alerta.get("lineas_afectadas", []),
                    "linea_inicio": ultima_alerta.get("linea_inicio", 0),
                    "linea_fin": ultima_alerta.get("linea_fin", 0),
                    "delta_segundos": ultima_alerta.get("delta_segundos", 0),
                    "mensaje": ultima_alerta["mensaje"]
                }
                alertas_generadas.append(alerta_formato)

        return alertas_generadas

    def analizar_archivo_completo(self, client_id: str, ruta_archivo: str) -> List[Dict[str, Any]]:
        """
        Lee un archivo de log existente completo (ej. Martin Hargous@MartinPC.log)
        y evalúa todas las reglas para reconstruir el historial de alertas reales.
        """
        if not os.path.exists(ruta_archivo):
            return []

        lineas = []
        try:
            with open(ruta_archivo, "r", encoding="utf-8", errors="ignore") as f:
                lineas = f.readlines()
        except Exception as e:
            print(f"Error leyendo {ruta_archivo}: {e}")
            return []

        eventos = []
        for idx, l in enumerate(lineas, start=1):
            ev = self.parsear_linea(l, numero_linea=idx, ignorar_heartbeats=True)
            if ev:
                eventos.append(ev)

        eventos.sort(key=lambda x: x["ts"])
        self.historial_eventos_por_cliente[client_id] = eventos

        if client_id not in self.ultimo_disparo:
            self.ultimo_disparo[client_id] = {}

        todas_alertas = []
        for regla in self.reglas:
            regla_id = regla.get("id", "")
            hallazgos = self.correlacionar_secuencia(eventos, regla, t_min_permitido=0.0)
            
            t_anterior = 0.0
            for h in hallazgos:
                if (h["t_disparo"] - t_anterior) > 15.0:
                    t_anterior = h["t_disparo"]
                    todas_alertas.append({
                        "client_id": client_id,
                        "timestamp": h["timestamp"],
                        "nivel": h["nivel"],
                        "regla_id": h["regla_id"],
                        "regla_nombre": h["regla_nombre"],
                        "lineas_afectadas": h.get("lineas_afectadas", []),
                        "linea_inicio": h.get("linea_inicio", 0),
                        "linea_fin": h.get("linea_fin", 0),
                        "delta_segundos": h.get("delta_segundos", 0),
                        "mensaje": h["mensaje"]
                    })

        todas_alertas.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return todas_alertas

