"""
comparator.py
---------------------------------
Módulo de Comparación y Similitud Multi-Señal de Logs de Examen para Integri-TI.
Analiza todos los logs de telemetría de los alumnos en una carpeta para detectar
posible colusión, copia de código o sincronía no permitida.

Estrategia (optimizada para bajo consumo y alta precisión):
  1. Reconstruye el texto tecleado por cada alumno a partir del Keylogger
     (emulación de cursor, flechas y borrado dinámico).
  2. Genera una firma MinHash del código normalizado por alumno (comparar firmas
     es O(1) con precisión configurable vía K_SHINGLE y NUM_PERM).
  3. Usa MinHashLSH para encontrar pares candidatos de código sin O(n^2).
  4. Cruza 3 señales adicionales independientes del texto:
       - Portapapeles (Paperclip): contenido idéntico o similar compartido.
       - Fugas de contexto (Program Monitor + Sniffer): salidas simultáneas de la
         ventana del examen dentro de una ventana de tiempo (VENTANA_SINCRONIA_SEG),
         marcando si hubo tráfico de red en ambos lados.
       - Ejecución (Auditoría Python): INICIO/FIN de scripts con pocos segundos de
         diferencia y outputs idénticos.
  5. Calcula un Score de Riesgo Compuesto (0.0 a 1.0) y cuenta de señales independientes.

Soporta ejecución programada en segundo plano y disparador bajo demanda desde el Dashboard.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from datasketch import MinHash, MinHashLSH

LINEA_RE = re.compile(r"^\[([^\]]+)\]\s*\[([^\]]+)\](.*)$")
CLIPBOARD_RE = re.compile(r"content='(.*)'\s*length=(\d+)\s*$")
AUDITORIA_SCRIPT_RE = re.compile(r"\[(INICIO|FIN)\]\s*(.+)$")
CONTEXT_CHANGE_RE = re.compile(r"CONTEXT_CHANGE\s+app='([^']*)'\s*title='(.*)'\s*$")

# Configuración por defecto
CONFIG_DEFAULT: Dict[str, Any] = {
    "intervalo_segundos": 60,         # Intervalo de análisis automático en segundos
    "auto_analisis": True,            # Ejecución periódica automática en segundo plano
    "k_shingle": 5,                   # Tamaño del n-grama de palabras para MinHash
    "num_perm": 128,                  # Permutaciones MinHash (precisión vs costo)
    "ventana_sincronia_seg": 8,       # Segundos para considerar eventos simultáneos
    "umbral_codigo": 0.35,            # Umbral de similitud de código para candidatos LSH (0-1)
    "umbral_portapapeles": 0.50,      # Umbral de similitud para portapapeles (0-1)
    "apps_permitidas": "code, python, pythonw", # Whitelist de ejecutables permitidos
    "titulos_permitidos": "integri-ti"          # Whitelist de títulos de ventana
}


# ---------------------------------------------------------------------
# Estructuras de Datos
# ---------------------------------------------------------------------

@dataclass
class Fuga:
    inicio: datetime
    fin: datetime
    app: str
    titulo: str
    hubo_red: bool = False


@dataclass
class PerfilAlumno:
    archivo: str
    alumno_id: str
    texto_codigo: str = ""
    shingles: set = field(default_factory=set)
    minhash: Optional[MinHash] = None
    portapapeles: List[str] = field(default_factory=list)
    fugas: List[Fuga] = field(default_factory=list)
    ejecuciones: List[Tuple[datetime, str]] = field(default_factory=list)  # (ts, "script|INICIO/FIN")
    outputs: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Funciones Utilitarias de Texto y Logs
# ---------------------------------------------------------------------

def normalizar_nombre_app(app: str) -> str:
    """Quita la extensión (.exe en Windows) y convierte a minúsculas."""
    return Path(app).stem.lower()


def parsear_timestamp(ts: str) -> datetime:
    ts_clean = ts.replace("T", " ").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts_clean, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(ts_clean)


def reconstruir_texto(fragmentos_keylogger: List[str]) -> str:
    """Reconstruye el texto final simulando la posición exacta del cursor en O(N) lineal."""
    left: List[str] = []
    right: List[str] = []
    log_crudo = "".join(fragmentos_keylogger)
    partes = re.split(r"(\[.*?\])", log_crudo)

    for parte in partes:
        if not parte:
            continue
        parte_upper = parte.upper()

        if parte_upper in ("[BACKSPACE]", "[BASKSPACE]"):
            if left:
                left.pop()
        elif parte_upper == "[LEFT]":
            if left:
                right.append(left.pop())
        elif parte_upper == "[RIGHT]":
            if right:
                left.append(right.pop())
        elif parte_upper in ("[UP]", "[DOWN]"):
            pass
        elif parte_upper == "[ENTER]":
            left.append("\n")
        elif parte.startswith("[") and parte.endswith("]"):
            pass
        else:
            left.extend(list(parte))

    right.reverse()
    return "".join(left) + "".join(right)


def normalizar_codigo(texto: str) -> str:
    """Quita comentarios de línea, espacios sobrantes y baja a minúsculas."""
    lineas = []
    for linea in texto.splitlines():
        linea = re.sub(r"#.*$", "", linea).strip()
        if linea:
            lineas.append(linea)
    return " ".join(lineas).lower()


# ---------------------------------------------------------------------
# Clase Principal del Módulo Comparador
# ---------------------------------------------------------------------

class LogComparator:
    """
    Motor Modular de Análisis y Comparación de Logs para Integri-TI.
    """

    def __init__(self, carpeta_logs: str | Path, config: Optional[Dict[str, Any]] = None, db_path: Optional[str] = None):
        self.carpeta_logs = Path(carpeta_logs)
        self.db_path = db_path
        self.config = CONFIG_DEFAULT.copy()

        # Cargar configuración desde SQLite si existe
        try:
            import database
            guardada = database.obtener_config_comparador()
            if guardada:
                self.config.update(guardada)
        except Exception:
            pass

        if config:
            self.config.update(config)

        # Último resultado en memoria
        self.ultimo_resultado: Optional[Dict[str, Any]] = None
        try:
            import database
            ultimo_db = database.obtener_ultimo_resultado_comparador()
            if ultimo_db:
                self.ultimo_resultado = ultimo_db
        except Exception:
            pass

        # Control del scheduler en segundo plano
        self.running = False
        self.estado_examen = "ESPERANDO"  # ESPERANDO, GRABANDO, FINALIZADO
        self.scheduler_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.segundos_restantes = int(self.config.get("intervalo_segundos", 60))
        self.ultimo_analisis_ts: Optional[str] = (
            self.ultimo_resultado.get("timestamp") if self.ultimo_resultado else None
        )

    # -----------------------------------------------------------------
    # Configuración Dinámica
    # -----------------------------------------------------------------

    def actualizar_config(self, nueva_config: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            if "intervalo_segundos" in nueva_config:
                self.config["intervalo_segundos"] = max(0, int(nueva_config["intervalo_segundos"]))
            if "auto_analisis" in nueva_config:
                self.config["auto_analisis"] = bool(nueva_config["auto_analisis"])
            if "k_shingle" in nueva_config:
                self.config["k_shingle"] = max(1, min(20, int(nueva_config["k_shingle"])))
            if "num_perm" in nueva_config:
                self.config["num_perm"] = max(16, min(512, int(nueva_config["num_perm"])))
            if "ventana_sincronia_seg" in nueva_config:
                self.config["ventana_sincronia_seg"] = max(1, min(120, int(nueva_config["ventana_sincronia_seg"])))
            if "umbral_codigo" in nueva_config:
                self.config["umbral_codigo"] = max(0.0, min(1.0, float(nueva_config["umbral_codigo"])))
            if "umbral_portapapeles" in nueva_config:
                self.config["umbral_portapapeles"] = max(0.0, min(1.0, float(nueva_config["umbral_portapapeles"])))
            if "apps_permitidas" in nueva_config:
                self.config["apps_permitidas"] = str(nueva_config["apps_permitidas"]).strip()
            if "titulos_permitidos" in nueva_config:
                self.config["titulos_permitidos"] = str(nueva_config["titulos_permitidos"]).strip()

            self.segundos_restantes = self.config["intervalo_segundos"]

            # Persistir en SQLite
            try:
                import database
                database.guardar_config_comparador(self.config)
            except Exception as e:
                print(f"[!] Error guardando config del comparador en SQLite: {e}")

            return self.config.copy()

    def obtener_config(self) -> Dict[str, Any]:
        with self.lock:
            return self.config.copy()

    def obtener_estado(self) -> Dict[str, Any]:
        with self.lock:
            pausado_finalizado = (self.estado_examen == "FINALIZADO")
            return {
                "activo": self.running,
                "auto_analisis": self.config.get("auto_analisis", True),
                "intervalo_segundos": self.config.get("intervalo_segundos", 60),
                "segundos_restantes": 0 if pausado_finalizado else max(0, self.segundos_restantes),
                "ultimo_analisis": self.ultimo_analisis_ts,
                "estado_examen": self.estado_examen,
                "pausado_por_finalizacion": pausado_finalizado,
                "total_pares_detectados": self.ultimo_resultado.get("total_pares", 0) if self.ultimo_resultado else 0,
                "total_alumnos_analizados": self.ultimo_resultado.get("total_alumnos", 0) if self.ultimo_resultado else 0
            }

    def establecer_estado_examen(self, nuevo_estado: str):
        """
        Actualiza el estado global del examen (ESPERANDO, GRABANDO, FINALIZADO).
        Si el estado llega a FINALIZADO, el comparador detiene el análisis periódico automático.
        """
        with self.lock:
            previo = self.estado_examen
            self.estado_examen = str(nuevo_estado).upper().strip()

        if self.estado_examen == "FINALIZADO":
            print(f"[*] [Comparador] Examen en estado FINALIZADO: Auto-análisis periódico detenido.")
            if previo != "FINALIZADO":
                # Disparar análisis de cierre en segundo plano para consolidar los resultados finales
                try:
                    hilo = threading.Thread(target=self._analisis_cierre, daemon=True, name="ComparatorCierreThread")
                    hilo.start()
                except Exception as e:
                    print(f"[!] Error iniciando análisis de cierre: {e}")
        else:
            with self.lock:
                self.segundos_restantes = self.config.get("intervalo_segundos", 60)
            print(f"[*] [Comparador] Examen en estado {self.estado_examen}: Scheduler periódico activo.")

    def _analisis_cierre(self):
        """Ejecuta un análisis final de cierre al finalizar el examen."""
        try:
            print(f"\n[*] [Comparador] Ejecutando análisis final de consolidación post-examen...")
            self.ejecutar_analisis()
            print(f"[+] [Comparador] Análisis final de consolidación completado con éxito.")
        except Exception as e:
            print(f"[!] [Comparador] Error durante análisis final de consolidación: {e}")

    # -----------------------------------------------------------------
    # Contexto Whitelist
    # -----------------------------------------------------------------

    def es_contexto_permitido(self, app: str, title: str) -> bool:
        apps_permitidas = {
            normalizar_nombre_app(a.strip())
            for a in self.config.get("apps_permitidas", "code, python, pythonw").split(",")
            if a.strip()
        }
        if normalizar_nombre_app(app) in apps_permitidas:
            return True

        titulos_permitidos = [
            t.strip().lower()
            for t in self.config.get("titulos_permitidos", "integri-ti").split(",")
            if t.strip()
        ]
        title_l = title.lower()
        return any(k in title_l for k in titulos_permitidos)

    # -----------------------------------------------------------------
    # Parseo de Log de Alumno
    # -----------------------------------------------------------------

    def parsear_log(self, path: Path) -> PerfilAlumno:
        alumno_id = path.name[:-4] if path.name.endswith(".log") else path.name
        perfil = PerfilAlumno(archivo=path.name, alumno_id=alumno_id)
        fragmentos_keylogger: List[str] = []
        eventos_contexto: List[Tuple[datetime, str, str]] = []
        eventos_red: List[datetime] = []

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for linea in f:
                m = LINEA_RE.match(linea)
                if not m:
                    continue
                ts_raw, tag, contenido = m.group(1), m.group(2).strip(), m.group(3).strip()

                if tag == "Keylogger":
                    if contenido != "--- IGNORE ---":
                        fragmentos_keylogger.append(contenido)

                elif tag == "Paperclip":
                    cm = CLIPBOARD_RE.search(contenido)
                    if cm:
                        texto_clip = cm.group(1).replace("\\n", "\n").replace("\\t", "\t")
                        perfil.portapapeles.append(texto_clip)

                elif tag == "Program Monitor":
                    cc = CONTEXT_CHANGE_RE.search(contenido)
                    if cc:
                        try:
                            eventos_contexto.append((parsear_timestamp(ts_raw), cc.group(1), cc.group(2)))
                        except Exception:
                            pass

                elif tag == "Sniffer":
                    try:
                        eventos_red.append(parsear_timestamp(ts_raw))
                    except Exception:
                        pass

                elif tag in ("Auditoria Python", "Error Detection"):
                    am = AUDITORIA_SCRIPT_RE.match(contenido)
                    if am:
                        try:
                            perfil.ejecuciones.append((parsear_timestamp(ts_raw), am.group(2).strip()))
                        except Exception:
                            pass
                    elif contenido.startswith("[PRINT]"):
                        perfil.outputs.append(contenido[len("[PRINT]"):].strip())

        texto_tecleado = reconstruir_texto(fragmentos_keylogger)
        textos_totales = [texto_tecleado] + [t for t in perfil.portapapeles if len(t.strip()) >= 15]
        perfil.texto_codigo = normalizar_codigo("\n".join(textos_totales))

        k_shingle = int(self.config.get("k_shingle", 4))
        num_perm = int(self.config.get("num_perm", 128))

        palabras = perfil.texto_codigo.split()
        perfil.shingles = {
            " ".join(palabras[i:i + k_shingle])
            for i in range(max(len(palabras) - k_shingle + 1, 0))
        }

        mh = MinHash(num_perm=num_perm)
        for sh in perfil.shingles:
            mh.update(sh.encode("utf-8"))
        perfil.minhash = mh

        perfil.fugas = self.construir_fugas(eventos_contexto, eventos_red)
        return perfil

    def construir_fugas(self, eventos_contexto: List[Tuple[datetime, str, str]], eventos_red: List[datetime]) -> List[Fuga]:
        eventos_contexto = sorted(eventos_contexto, key=lambda e: e[0])
        fugas: List[Fuga] = []
        fuga_actual = None

        for ts, app, title in eventos_contexto:
            permitido = self.es_contexto_permitido(app, title)
            if not permitido and fuga_actual is None:
                fuga_actual = {"inicio": ts, "app": app, "titulo": title}
            elif permitido and fuga_actual is not None:
                fugas.append(Fuga(inicio=fuga_actual["inicio"], fin=ts, app=fuga_actual["app"], titulo=fuga_actual["titulo"]))
                fuga_actual = None

        if fuga_actual is not None and eventos_contexto:
            fugas.append(Fuga(inicio=fuga_actual["inicio"], fin=eventos_contexto[-1][0], app=fuga_actual["app"], titulo=fuga_actual["titulo"]))

        for fuga in fugas:
            fuga.hubo_red = any(fuga.inicio <= ts_red <= fuga.fin for ts_red in eventos_red)

        return fugas

    # -----------------------------------------------------------------
    # Búsqueda de Candidatos y Cálculo de Similitud
    # -----------------------------------------------------------------

    def encontrar_pares_candidatos_lsh(self, perfiles: Dict[str, PerfilAlumno], umbral: float) -> List[Tuple[str, str]]:
        num_perm = int(self.config.get("num_perm", 128))
        lsh = MinHashLSH(threshold=umbral, num_perm=num_perm)
        for nombre, perfil in perfiles.items():
            if perfil.shingles and perfil.minhash:
                lsh.insert(nombre, perfil.minhash)

        pares = set()
        for nombre, perfil in perfiles.items():
            if not perfil.shingles or not perfil.minhash:
                continue
            for candidato in lsh.query(perfil.minhash):
                if candidato != nombre:
                    par = tuple(sorted((nombre, candidato)))
                    pares.add(par)
        return sorted(pares)

    def encontrar_pares_por_portapapeles(self, perfiles: Dict[str, PerfilAlumno], umbral_clip: float) -> List[Tuple[str, str]]:
        # Indexar palabras significativas (len >= 5) para comparar únicamente pares que compartan vocabulario
        word_to_students: Dict[str, Set[str]] = defaultdict(set)
        for name, perf in perfiles.items():
            for text in perf.portapapeles:
                if len(text) >= 15:
                    for w in set(re.findall(r"\w{5,}", text.lower())):
                        word_to_students[w].add(name)

        candidatos = set()
        for w, stus in word_to_students.items():
            if len(stus) > 1:
                st_list = list(stus)
                for i in range(len(st_list)):
                    for j in range(i + 1, len(st_list)):
                        candidatos.add(tuple(sorted((st_list[i], st_list[j]))))

        pares = set()
        for na, nb in candidatos:
            sim, _ = self.similitud_portapapeles(perfiles[na], perfiles[nb])
            if sim >= umbral_clip:
                pares.add((na, nb))
        return sorted(pares)

    def similitud_portapapeles(self, a: PerfilAlumno, b: PerfilAlumno) -> Tuple[float, str]:
        # Cache por par para evitar duplicación de cálculos
        cache_key = tuple(sorted((a.alumno_id, b.alumno_id)))
        if hasattr(self, "_cache_sim_clip") and cache_key in self._cache_sim_clip:
            return self._cache_sim_clip[cache_key]

        mejor = 0.0
        detalle = ""
        umbral = float(self.config.get("umbral_portapapeles", 0.50))

        for texto_a in a.portapapeles:
            la = len(texto_a)
            if la < 15:
                continue
            for texto_b in b.portapapeles:
                lb = len(texto_b)
                if lb < 15:
                    continue

                # Coincidencia exacta O(1)
                if texto_a == texto_b:
                    res = (1.0, texto_a[:80].replace("\n", " ").strip())
                    if hasattr(self, "_cache_sim_clip"):
                        self._cache_sim_clip[cache_key] = res
                    return res

                # Filtro rápido de longitud
                if min(la, lb) / max(la, lb) < umbral:
                    continue

                sm = difflib.SequenceMatcher(None, texto_a, texto_b)
                # Filtro quick_ratio O(N) antes del ratio O(N*M)
                if sm.quick_ratio() < umbral:
                    continue

                r = sm.ratio()
                if r > mejor:
                    mejor = r
                    detalle = texto_a[:80].replace("\n", " ").strip()

        res = (mejor, detalle)
        if hasattr(self, "_cache_sim_clip"):
            self._cache_sim_clip[cache_key] = res
        return res

    def sincronia_fugas(self, a: PerfilAlumno, b: PerfilAlumno) -> List[str]:
        ventana_seg = float(self.config.get("ventana_sincronia_seg", 8))
        hallazgos = []
        for fa in a.fugas:
            for fb in b.fugas:
                if fb.inicio > fa.fin and (fb.inicio - fa.fin).total_seconds() > ventana_seg:
                    break
                if fa.inicio > fb.fin and (fa.inicio - fb.fin).total_seconds() > ventana_seg:
                    continue
                solapan = fa.inicio <= fb.fin and fb.inicio <= fa.fin
                gap = 0 if solapan else min(abs((fa.inicio - fb.fin).total_seconds()), abs((fb.inicio - fa.fin).total_seconds()))
                if solapan or gap <= ventana_seg:
                    fuerza = "con red en ambos" if (fa.hubo_red and fb.hubo_red) else "sin red confirmada"
                    hallazgos.append(
                        f"{fa.app} <-> {fb.app} {'(solapadas)' if solapan else f'(~{int(gap)}s dif)'} [{fuerza}]"
                    )
                    if len(hallazgos) >= 10:
                        return hallazgos
        return hallazgos

    def sincronia_ejecucion(self, a: PerfilAlumno, b: PerfilAlumno) -> List[str]:
        ventana_seg = float(self.config.get("ventana_sincronia_seg", 8))
        hallazgos = []
        for ts_a, script_a in a.ejecuciones:
            name_a = Path(script_a).name
            for ts_b, script_b in b.ejecuciones:
                diff = (ts_b - ts_a).total_seconds()
                if diff > ventana_seg:
                    break
                if abs(diff) <= ventana_seg and name_a == Path(script_b).name:
                    hallazgos.append(f"{name_a} (~{int(abs(diff))}s dif)")
                    if len(hallazgos) >= 10:
                        break
            if len(hallazgos) >= 10:
                break

        outputs_comunes = set(a.outputs) & set(b.outputs)
        if outputs_comunes:
            hallazgos.append(f"outputs idénticos: {len(outputs_comunes)}")
        return hallazgos

    # -----------------------------------------------------------------
    # Orquestador del Análisis
    # -----------------------------------------------------------------

    def ejecutar_analisis(self) -> Dict[str, Any]:
        """
        Ejecuta el análisis comparativo completo sobre todos los logs en carpeta_logs.
        Guarda los resultados en memoria y en la base de datos SQLite.
        """
        timestamp_inicio = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        archivos = sorted(self.carpeta_logs.glob("*.log"))

        if len(archivos) < 2:
            resultado_vacio = {
                "timestamp": timestamp_inicio,
                "total_alumnos": len(archivos),
                "total_pares": 0,
                "pares": [],
                "mensaje": "Se requieren al menos 2 archivos de log para realizar la comparación.",
                "config_usada": self.config.copy()
            }
            with self.lock:
                self.ultimo_resultado = resultado_vacio
                self.ultimo_analisis_ts = timestamp_inicio
            return resultado_vacio

        perfiles = {p.name: self.parsear_log(p) for p in archivos}

        umbral_cod = float(self.config.get("umbral_codigo", 0.35))
        umbral_clip = float(self.config.get("umbral_portapapeles", 0.50))

        self._cache_sim_clip = {}
        pares_candidatos = set(self.encontrar_pares_candidatos_lsh(perfiles, umbral_cod))
        pares_candidatos |= set(self.encontrar_pares_por_portapapeles(perfiles, umbral_clip))
        pares_candidatos = sorted(pares_candidatos)

        resultados = []
        for nombre_a, nombre_b in pares_candidatos:
            a, b = perfiles[nombre_a], perfiles[nombre_b]

            jaccard_exacto = (
                len(a.shingles & b.shingles) / len(a.shingles | b.shingles)
                if (a.shingles | b.shingles) else 0.0
            )
            sim_clip, muestra_clip = self.similitud_portapapeles(a, b)
            fuga_sync = self.sincronia_fugas(a, b)
            fuga_con_red = any("con red en ambos" in h for h in fuga_sync)
            exec_sync = self.sincronia_ejecucion(a, b)

            # Score compuesto ponderado (código 45%, portapapeles 35%, fugas 15%, ejecución 5%)
            score = (
                0.45 * jaccard_exacto
                + 0.35 * sim_clip
                + 0.15 * (1.0 if fuga_con_red else (0.5 if fuga_sync else 0.0))
                + 0.05 * (1.0 if exec_sync else 0.0)
            )

            # Si el portapapeles es idéntico o casi idéntico con ejecución simultánea, elevar score
            if sim_clip >= 0.85 and exec_sync:
                score = max(score, 0.70)
            elif sim_clip >= 0.85:
                score = max(score, 0.50)

            # Nivel de severidad
            if score >= 0.55 or jaccard_exacto >= 0.65 or (sim_clip >= 0.85 and exec_sync):
                nivel_riesgo = "CRÍTICO"
                color_riesgo = "red"
            elif score >= 0.35 or jaccard_exacto >= 0.45 or sim_clip >= 0.70:
                nivel_riesgo = "ALTO"
                color_riesgo = "amber"
            else:
                nivel_riesgo = "MEDIO"
                color_riesgo = "yellow"

            resultados.append({
                "alumno_a": nombre_a,
                "alumno_b": nombre_b,
                "alumno_a_id": a.alumno_id,
                "alumno_b_id": b.alumno_id,
                "score_riesgo": round(score, 3),
                "score_porcentaje": int(round(score * 100)),
                "nivel_riesgo": nivel_riesgo,
                "color_riesgo": color_riesgo,
                "similitud_codigo": round(jaccard_exacto, 3),
                "similitud_codigo_porcentaje": int(round(jaccard_exacto * 100)),
                "similitud_portapapeles": round(sim_clip, 3),
                "similitud_portapapeles_porcentaje": int(round(sim_clip * 100)),
                "muestra_portapapeles": muestra_clip,
                "fugas_sincronizadas": fuga_sync,
                "fugas_con_red": fuga_con_red,
                "sincronia_ejecucion": exec_sync,
                "señales_independientes": sum([
                    jaccard_exacto > 0.30,
                    sim_clip > 0.50,
                    bool(fuga_sync),
                    bool(exec_sync),
                ]),
            })

        resultados.sort(key=lambda r: r["score_riesgo"], reverse=True)

        res_final = {
            "timestamp": timestamp_inicio,
            "total_alumnos": len(archivos),
            "total_pares": len(resultados),
            "pares": resultados,
            "config_usada": self.config.copy()
        }

        with self.lock:
            self.ultimo_resultado = res_final
            self.ultimo_analisis_ts = timestamp_inicio

        # Guardar en SQLite
        try:
            import database
            database.guardar_resultado_comparador(
                total_alumnos=len(archivos),
                total_pares=len(resultados),
                resultados=resultados,
                config_usada=self.config.copy(),
                timestamp=timestamp_inicio
            )
        except Exception as e:
            print(f"[!] Error persistiendo resultado de comparador en SQLite: {e}")

        return res_final

    def obtener_ultimo_resultado(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.ultimo_resultado

    # -----------------------------------------------------------------
    # Hilo Planificador en Segundo Plano (Background Scheduler)
    # -----------------------------------------------------------------

    def _loop_scheduler(self):
        while self.running:
            time.sleep(1.0)
            with self.lock:
                # Si el examen está finalizado, no ejecutar análisis periódico
                if self.estado_examen == "FINALIZADO":
                    continue
                if not self.config.get("auto_analisis", True):
                    continue
                intervalo = self.config.get("intervalo_segundos", 60)
                if intervalo <= 0:
                    continue

                self.segundos_restantes -= 1
                debe_ejecutar = self.segundos_restantes <= 0

            if debe_ejecutar:
                try:
                    print(f"\n[*] [Comparador] Ejecutando análisis automático periódico sobre {self.carpeta_logs}...")
                    self.ejecutar_analisis()
                    print(f"[+] [Comparador] Análisis completado exitosamente.")
                except Exception as e:
                    print(f"[!] [Comparador] Error durante análisis automático: {e}")
                finally:
                    with self.lock:
                        self.segundos_restantes = self.config.get("intervalo_segundos", 60)

    def iniciar_scheduler(self):
        if self.running:
            return
        self.running = True
        self.segundos_restantes = self.config.get("intervalo_segundos", 60)
        self.scheduler_thread = threading.Thread(target=self._loop_scheduler, daemon=True, name="ComparatorScheduler")
        self.scheduler_thread.start()
        print(f"[*] Scheduler del comparador de logs iniciado (intervalo: {self.config['intervalo_segundos']}s, auto: {self.config['auto_analisis']}).")

    def detener_scheduler(self):
        self.running = False


# Instancia global por defecto para uso directo
comparador = LogComparator(Path(os.path.dirname(os.path.abspath(__file__))) / "datos_alumnos")


# ---------------------------------------------------------------------
# Modo CLI Independiente
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analiza relación/similitud entre logs de examen.")
    parser.add_argument("carpeta", type=Path, nargs="?", default=Path("datos_alumnos"), help="Carpeta con los .log de los alumnos")
    parser.add_argument("--umbral", type=float, default=0.35, help="Umbral de similitud de código (0-1, default 0.35)")
    parser.add_argument("--salida", type=Path, default=None, help="Archivo JSON de salida (opcional)")
    parser.add_argument("--k-shingle", type=int, default=5, help="Tamaño n-grama MinHash (default: 5)")
    parser.add_argument("--num-perm", type=int, default=128, help="Permutaciones MinHash (default: 128)")
    parser.add_argument("--ventana", type=int, default=8, help="Ventana de sincronía en segundos (default: 8)")
    args = parser.parse_args()

    cfg = {
        "k_shingle": args.k_shingle,
        "num_perm": args.num_perm,
        "ventana_sincronia_seg": args.ventana,
        "umbral_codigo": args.umbral
    }
    motor = LogComparator(args.carpeta, config=cfg)
    resultado = motor.ejecutar_analisis()
    pares = resultado.get("pares", [])

    if not pares:
        print("No se encontraron pares con similitud por encima del umbral.")
        return

    print(f"\n{'='*70}")
    print(f"REPORTE COMPARATIVO DE LOGS ({resultado['total_alumnos']} alumnos, {len(pares)} pares sospechosos)")
    print(f"{'='*70}")

    for r in pares:
        print(f"\n{r['alumno_a']}  <->  {r['alumno_b']}")
        print(f"  Score de Riesgo:         {r['score_riesgo']} [{r['nivel_riesgo']}] ({r['señales_independientes']} señales independientes)")
        print(f"  Similitud de Código:     {r['similitud_codigo_porcentaje']}%")
        print(f"  Similitud Portapapeles:  {r['similitud_portapapeles_porcentaje']}%" + (f" -> \"{r['muestra_portapapeles']}...\"" if r["muestra_portapapeles"] else ""))
        if r["fugas_sincronizadas"]:
            print(f"  Fugas Sincronizadas:     {r['fugas_sincronizadas']}")
        if r["sincronia_ejecucion"]:
            print(f"  Ejecución Sincronizada:  {r['sincronia_ejecucion']}")

    if args.salida:
        args.salida.write_text(json.dumps(pares, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReporte guardado en {args.salida}")


if __name__ == "__main__":
    main()