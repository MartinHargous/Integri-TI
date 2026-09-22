"""
comparator.py
---------------------------------
Revisa todos los logs de examen de una carpeta y calcula qué tan
relacionados están entre sí (posible copia/colusión).

Estrategia (pensada para bajo consumo de recursos):
  1. Reconstruye el texto realmente tecleado por cada alumno a partir
     del Keylogger (misma lógica de cursor/backspace que ya usas).
  2. Genera una firma MinHash del código por alumno -> comparar firmas
     es O(1), nunca se hace diff completo texto-contra-texto salvo
     para confirmar los pares candidatos.
  3. Usa MinHashLSH para encontrar candidatos sin comparar todos
     contra todos (evita el O(n^2)).
  4. Cruza 3 señales adicionales, independientes del texto:
       - Portapapeles (Paperclip): contenido idéntico/parecido pegado
         en dos máquinas distintas.
       - Fugas de contexto (Program Monitor + Sniffer): ventanas en las
         que el alumno salió de la app/ventana del examen, marcadas si
         hubo tráfico de red en el medio -- sin necesidad de conocer de
         antemano a qué dominio fueron. La señal fuerte es que DOS
         alumnos se "fuguen" al mismo tiempo, no a qué sitio entraron.
       - Ejecución (Auditoria Python): INICIO/FIN muy cercanos en el
         tiempo con el mismo script y output idéntico.
  5. Combina todo en un score de riesgo compuesto por par de alumnos.

Uso:
    python comparator.py /ruta/a/carpeta_logs
    python comparator.py /ruta/a/carpeta_logs --umbral 0.35 --salida reporte.json

Requisitos:
    pip install datasketch --break-system-packages
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from datasketch import MinHash, MinHashLSH

# ---------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------

LINEA_RE = re.compile(r"^\[([^\]]+)\]\s*\[([^\]]+)\](.*)$")
CLIPBOARD_RE = re.compile(r"content='(.*)'\s*length=(\d+)\s*$")
AUDITORIA_SCRIPT_RE = re.compile(r"\[(INICIO|FIN)\]\s*(.+)$")
CONTEXT_CHANGE_RE = re.compile(r"CONTEXT_CHANGE\s+app='([^']*)'\s*title='(.*)'\s*$")

# Contexto que SÍ corresponde al examen (whitelist), en vez de perseguir
# dominios sospechosos uno por uno. Cualquier cambio de ventana que no
# matchee esto se considera una "fuga" del contexto del examen.
# Se guardan SIN extensión: en Windows llega 'code.exe', en Linux/macOS
# llega 'code' o 'Code' a secas -- se normaliza antes de comparar.
APPS_PERMITIDAS = {"code", "python", "pythonw"}
TITULO_PERMITIDO_CONTIENE = ("integri-ti",)

K_SHINGLE = 5          # tamaño del n-grama de palabras para el MinHash
NUM_PERM = 128         # permutaciones del MinHash (precisión vs costo)
VENTANA_SINCRONIA_SEG = 8  # segundos para considerar "simultáneo"


def normalizar_nombre_app(app: str) -> str:
    """Quita la extensión (.exe en Windows; en Linux/macOS no suele haber)
    y baja a minúsculas, para poder comparar 'firefox.exe' == 'firefox'."""
    return Path(app).stem.lower()


def es_contexto_permitido(app: str, title: str) -> bool:
    if normalizar_nombre_app(app) in APPS_PERMITIDAS:
        return True
    title_l = title.lower()
    return any(k in title_l for k in TITULO_PERMITIDO_CONTIENE)


# ---------------------------------------------------------------------
# Estructuras
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
    texto_codigo: str = ""
    shingles: set = field(default_factory=set)
    minhash: MinHash = None
    portapapeles: List[str] = field(default_factory=list)
    fugas: List[Fuga] = field(default_factory=list)
    ejecuciones: List[Tuple[datetime, str]] = field(default_factory=list)  # (ts, "script|INICIO/FIN")
    outputs: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# 1. Reconstrucción del texto tecleado (misma lógica de cursor que ya usas)
# ---------------------------------------------------------------------

def reconstruir_texto(fragmentos_keylogger: List[str]) -> str:
    """Reconstruye el texto final simulando la posición del cursor,
    igual que limpiar_keylogger_avanzado, pero sobre TODO el log
    concatenado en orden cronológico."""
    texto_final: List[str] = []
    cursor = 0
    log_crudo = "".join(fragmentos_keylogger)
    partes = re.split(r"(\[.*?\])", log_crudo)

    for parte in partes:
        if not parte:
            continue
        parte_upper = parte.upper()

        if parte_upper in ("[BACKSPACE]", "[BASKSPACE]"):
            if cursor > 0:
                cursor -= 1
                texto_final.pop(cursor)
        elif parte_upper == "[LEFT]":
            if cursor > 0:
                cursor -= 1
        elif parte_upper == "[RIGHT]":
            if cursor < len(texto_final):
                cursor += 1
        elif parte_upper == "[UP]" or parte_upper == "[DOWN]":
            pass  # no afecta al cursor horizontal simulado
        elif parte_upper == "[ENTER]":
            texto_final.insert(cursor, "\n")
            cursor += 1
        elif parte.startswith("[") and parte.endswith("]"):
            pass  # CTRL, SHIFT, TAB, etc. se ignoran
        else:
            for char in parte:
                texto_final.insert(cursor, char)
                cursor += 1

    return "".join(texto_final)


def normalizar_codigo(texto: str) -> str:
    """Quita comentarios de línea, espacios sobrantes y baja a minúsculas,
    para que renombrar variables o reformatear no rompa la similitud."""
    lineas = []
    for linea in texto.splitlines():
        linea = re.sub(r"#.*$", "", linea)
        linea = linea.strip()
        if linea:
            lineas.append(linea)
    return " ".join(lineas).lower()


def parsear_timestamp(ts: str) -> datetime:
    ts = ts.replace("T", " ")
    return datetime.fromisoformat(ts)


# ---------------------------------------------------------------------
# 2. Parseo de un archivo de log completo
# ---------------------------------------------------------------------

def parsear_log(path: Path) -> PerfilAlumno:
    perfil = PerfilAlumno(archivo=path.name)
    fragmentos_keylogger: List[str] = []
    eventos_contexto: List[Tuple[datetime, str, str]] = []  # (ts, app, title)
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
                    perfil.portapapeles.append(cm.group(1))

            elif tag == "Program Monitor":
                cc = CONTEXT_CHANGE_RE.search(contenido)
                if cc:
                    try:
                        eventos_contexto.append((parsear_timestamp(ts_raw), cc.group(1), cc.group(2)))
                    except ValueError:
                        pass

            elif tag == "Sniffer":
                try:
                    eventos_red.append(parsear_timestamp(ts_raw))
                except ValueError:
                    pass

            elif tag == "Auditoria Python":
                am = AUDITORIA_SCRIPT_RE.match(contenido)
                if am:
                    try:
                        perfil.ejecuciones.append((parsear_timestamp(ts_raw), am.group(2).strip()))
                    except ValueError:
                        pass
                elif contenido.startswith("[PRINT]"):
                    perfil.outputs.append(contenido[len("[PRINT]"):].strip())

    perfil.texto_codigo = normalizar_codigo(reconstruir_texto(fragmentos_keylogger))

    palabras = perfil.texto_codigo.split()
    perfil.shingles = {
        " ".join(palabras[i:i + K_SHINGLE])
        for i in range(max(len(palabras) - K_SHINGLE + 1, 0))
    }

    mh = MinHash(num_perm=NUM_PERM)
    for sh in perfil.shingles:
        mh.update(sh.encode("utf-8"))
    perfil.minhash = mh

    perfil.fugas = construir_fugas(eventos_contexto, eventos_red)

    return perfil


def construir_fugas(eventos_contexto: List[Tuple[datetime, str, str]],
                     eventos_red: List[datetime]) -> List[Fuga]:
    """A partir de la secuencia de cambios de ventana (Program Monitor),
    arma las ventanas de tiempo en las que el alumno estuvo FUERA del
    contexto del examen (whitelist), y marca cuáles de esas ventanas
    tuvieron tráfico de red (Sniffer) en el medio -- sin importar a qué
    dominio. No hace falta conocer de antemano qué apps van a usar."""
    eventos_contexto = sorted(eventos_contexto, key=lambda e: e[0])
    fugas: List[Fuga] = []
    fuga_actual = None

    for ts, app, title in eventos_contexto:
        permitido = es_contexto_permitido(app, title)
        if not permitido and fuga_actual is None:
            fuga_actual = {"inicio": ts, "app": app, "titulo": title}
        elif permitido and fuga_actual is not None:
            fugas.append(Fuga(inicio=fuga_actual["inicio"], fin=ts,
                               app=fuga_actual["app"], titulo=fuga_actual["titulo"]))
            fuga_actual = None

    if fuga_actual is not None and eventos_contexto:
        # nunca volvió al contexto permitido: la fuga llega hasta el último evento visto
        fugas.append(Fuga(inicio=fuga_actual["inicio"], fin=eventos_contexto[-1][0],
                           app=fuga_actual["app"], titulo=fuga_actual["titulo"]))

    for fuga in fugas:
        fuga.hubo_red = any(fuga.inicio <= ts_red <= fuga.fin for ts_red in eventos_red)

    return fugas


# ---------------------------------------------------------------------
# 3. Búsqueda de candidatos con LSH (evita comparar todos contra todos)
# ---------------------------------------------------------------------

def encontrar_pares_candidatos(perfiles: Dict[str, PerfilAlumno], umbral: float) -> List[Tuple[str, str]]:
    lsh = MinHashLSH(threshold=umbral, num_perm=NUM_PERM)
    for nombre, perfil in perfiles.items():
        if perfil.shingles:  # evita insertar logs vacíos
            lsh.insert(nombre, perfil.minhash)

    pares = set()
    for nombre, perfil in perfiles.items():
        if not perfil.shingles:
            continue
        for candidato in lsh.query(perfil.minhash):
            if candidato != nombre:
                par = tuple(sorted((nombre, candidato)))
                pares.add(par)
    return sorted(pares)


# ---------------------------------------------------------------------
# 4. Señales adicionales para un par ya candidato
# ---------------------------------------------------------------------

def encontrar_pares_por_portapapeles(perfiles: Dict[str, PerfilAlumno], umbral_clip: float = 0.5) -> List[Tuple[str, str]]:
    """Candidatos adicionales: alumnos que jamás escriben el mismo código
    pero comparten contenido de portapapeles (uno lo copia, el otro lo
    pega). El LSH de código nunca los detectaría porque el texto pegado
    no pasa por el Keylogger. Es barato: solo compara las pocas entradas
    de Paperclip que tiene cada alumno, no el código completo."""
    nombres = list(perfiles.keys())
    pares = set()
    for i in range(len(nombres)):
        for j in range(i + 1, len(nombres)):
            a, b = perfiles[nombres[i]], perfiles[nombres[j]]
            if not a.portapapeles or not b.portapapeles:
                continue
            sim, _ = similitud_portapapeles(a, b)
            if sim >= umbral_clip:
                pares.add(tuple(sorted((nombres[i], nombres[j]))))
    return sorted(pares)


def similitud_portapapeles(a: PerfilAlumno, b: PerfilAlumno) -> Tuple[float, str]:
    mejor = 0.0
    detalle = ""
    for texto_a in a.portapapeles:
        for texto_b in b.portapapeles:
            if len(texto_a) < 15 or len(texto_b) < 15:
                continue  # fragmentos muy cortos no son informativos
            r = difflib.SequenceMatcher(None, texto_a, texto_b).ratio()
            if r > mejor:
                mejor = r
                detalle = texto_a[:60]
    return mejor, detalle


def sincronia_fugas(a: PerfilAlumno, b: PerfilAlumno) -> List[str]:
    """Busca ventanas de fuga (salida del contexto del examen) que se
    solapan o casi coinciden en el tiempo entre dos alumnos. No mira a
    qué app/dominio fueron -- el hecho de salir juntos ya es la señal;
    si además hubo red en ambos lados, se marca como más fuerte."""
    hallazgos = []
    for fa in a.fugas:
        for fb in b.fugas:
            solapan = fa.inicio <= fb.fin and fb.inicio <= fa.fin
            if solapan:
                gap = 0
            else:
                gap = min(abs((fa.inicio - fb.fin).total_seconds()),
                          abs((fb.inicio - fa.fin).total_seconds()))
            if solapan or gap <= VENTANA_SINCRONIA_SEG:
                fuerza = "con red en ambos" if (fa.hubo_red and fb.hubo_red) else "sin red confirmada"
                hallazgos.append(
                    f"{fa.app}<->{fb.app} {'(solapadas)' if solapan else f'(~{int(gap)}s de diferencia)'} [{fuerza}]"
                )
    return hallazgos


def sincronia_ejecucion(a: PerfilAlumno, b: PerfilAlumno) -> List[str]:
    hallazgos = []
    for ts_a, script_a in a.ejecuciones:
        for ts_b, script_b in b.ejecuciones:
            mismo_script = Path(script_a).name == Path(script_b).name
            if mismo_script and abs((ts_a - ts_b).total_seconds()) <= VENTANA_SINCRONIA_SEG:
                hallazgos.append(f"{Path(script_a).name} (~{int(abs((ts_a - ts_b).total_seconds()))}s de diferencia)")
    outputs_comunes = set(a.outputs) & set(b.outputs)
    if outputs_comunes:
        hallazgos.append(f"outputs idénticos: {len(outputs_comunes)}")
    return hallazgos


# ---------------------------------------------------------------------
# 5. Orquestador
# ---------------------------------------------------------------------

def analizar_carpeta(carpeta: Path, umbral: float) -> List[dict]:
    archivos = sorted(carpeta.glob("*.log"))
    perfiles = {p.name: parsear_log(p) for p in archivos}

    pares_candidatos = set(encontrar_pares_candidatos(perfiles, umbral))
    pares_candidatos |= set(encontrar_pares_por_portapapeles(perfiles))
    pares_candidatos = sorted(pares_candidatos)

    resultados = []
    for nombre_a, nombre_b in pares_candidatos:
        a, b = perfiles[nombre_a], perfiles[nombre_b]

        jaccard_exacto = (
            len(a.shingles & b.shingles) / len(a.shingles | b.shingles)
            if (a.shingles | b.shingles) else 0.0
        )
        sim_clip, muestra_clip = similitud_portapapeles(a, b)
        fuga_sync = sincronia_fugas(a, b)
        fuga_con_red = any("con red en ambos" in h for h in fuga_sync)
        exec_sync = sincronia_ejecucion(a, b)

        # Score compuesto: el código pesa más, las otras señales lo refuerzan.
        # Una fuga simultánea CON red confirmada pesa más que una sin red.
        score = (
            0.6 * jaccard_exacto
            + 0.2 * sim_clip
            + 0.1 * (1.0 if fuga_con_red else (0.5 if fuga_sync else 0.0))
            + 0.1 * (1.0 if exec_sync else 0.0)
        )

        resultados.append({
            "alumno_a": nombre_a,
            "alumno_b": nombre_b,
            "similitud_codigo": round(jaccard_exacto, 3),
            "similitud_portapapeles": round(sim_clip, 3),
            "muestra_portapapeles": muestra_clip,
            "fugas_sincronizadas": fuga_sync,
            "sincronia_ejecucion": exec_sync,
            "score_riesgo": round(score, 3),
            "señales_independientes": sum([
                jaccard_exacto > 0.3,
                sim_clip > 0.6,
                bool(fuga_sync),
                bool(exec_sync),
            ]),
        })

    resultados.sort(key=lambda r: r["score_riesgo"], reverse=True)
    return resultados


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analiza relación/similitud entre logs de examen.")
    parser.add_argument("carpeta", type=Path, help="Carpeta con los .log de los alumnos")
    parser.add_argument("--umbral", type=float, default=0.35,
                         help="Umbral de similitud de código para buscar candidatos (0-1, default 0.35)")
    parser.add_argument("--salida", type=Path, default=None, help="Archivo JSON de salida (opcional)")
    parser.add_argument("--debug", action="store_true",
                         help="Muestra, por cada log encontrado, cuánto texto/portapapeles se extrajo (para diagnosticar por qué no aparecen pares)")
    args = parser.parse_args()

    if args.debug:
        archivos = sorted(args.carpeta.glob("*.log"))
        print(f"Archivos .log encontrados en '{args.carpeta}': {len(archivos)}")
        for p in archivos:
            perfil = parsear_log(p)
            print(f"\n- {perfil.archivo}")
            print(f"    caracteres de código reconstruido: {len(perfil.texto_codigo)}")
            print(f"    palabras: {len(perfil.texto_codigo.split())}  |  shingles generados: {len(perfil.shingles)}")
            print(f"    entradas de portapapeles: {len(perfil.portapapeles)}")
            print(f"    fugas de contexto detectadas: {len(perfil.fugas)}"
                  + (f"  (con red: {sum(1 for fg in perfil.fugas if fg.hubo_red)})" if perfil.fugas else ""))
            print(f"    ejecuciones registradas (Auditoria Python): {len(perfil.ejecuciones)}")
            if perfil.texto_codigo:
                print(f"    muestra de texto: {perfil.texto_codigo[:120]!r}")
        return

    resultados = analizar_carpeta(args.carpeta, args.umbral)

    if not resultados:
        print("No se encontraron pares con similitud por encima del umbral.")
        return

    for r in resultados:
        print(f"\n{r['alumno_a']}  <->  {r['alumno_b']}")
        print(f"  score de riesgo:         {r['score_riesgo']}  ({r['señales_independientes']} señales independientes)")
        print(f"  similitud de código:     {r['similitud_codigo']}")
        print(f"  similitud portapapeles:  {r['similitud_portapapeles']}"
              + (f"  -> \"{r['muestra_portapapeles']}...\"" if r["muestra_portapapeles"] else ""))
        if r["fugas_sincronizadas"]:
            print(f"  fugas de contexto sincronizadas: {r['fugas_sincronizadas']}")
        if r["sincronia_ejecucion"]:
            print(f"  ejecución sincronizada:  {r['sincronia_ejecucion']}")

    if args.salida:
        args.salida.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReporte guardado en {args.salida}")


if __name__ == "__main__":
    main()