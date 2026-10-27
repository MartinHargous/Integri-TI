"""
Simulador de carga para la marcha blanca de Hito 2.

Levanta N "alumnos" ficticios, cada uno en su propio hilo, que pasan por el
flujo real de autenticación (discovery con código de distribución -> secreto
personal -> /sync periódico con TOTP) exactamente como lo haría client.py.

Con --con-logs, además de ejercitar el transporte, cada /sync adjunta un
fragmento de combined_log.log sintético pero realista: keystrokes de
keylogger (con los mismos tags [CTRL]+c[/CTRL] / [CTRL]+v[/CTRL] que genera
pynput de verdad), cambios de portapapeles, cambios de ventana activa,
dominios visitados y, de vez en cuando, una secuencia completa pensada para
disparar una de las reglas de Server/correlator.py (R-01 a R-06) o para
alimentar con señal real el motor de similitud de Server/comparator.py
(código pegado, portapapeles, fugas de contexto, ejecuciones). La idea es
poner a prueba el pipeline de detección completo bajo carga, no solo medir
cuántos HTTP 200 aguanta el servidor.

--colusion N además hace que los primeros N clientes (agrupados de 2 en 2)
compartan literalmente el mismo código tecleado/pegado de a ratos, para
generar pares con similitud alta de verdad — útil para calibrar los
umbrales del comparador contra un caso con "ground truth" conocido, en vez
de solo ruido aleatorio.

No reemplaza una prueba con equipos reales (los módulos de captura siguen
sin ejecutarse, y la red real no está presente), pero sirve para medir
estabilidad, latencia y comportamiento del servidor — incluyendo el
correlator y el comparator — bajo concurrencia real de /sync.

Requiere: requests, pyotp (pip install requests pyotp)

Uso típico:
    python simulador_carga.py --server https://servidor.ejemplo.org --clientes 30 --con-logs

Para apuntar a un servidor local de pruebas:
    python simulador_carga.py --server http://localhost:8000 --clientes 10 --intervalo 15 --con-logs

Para ejercitar también al comparador con un caso de colusión conocido:
    python simulador_carga.py --server http://localhost:8000 --clientes 10 --con-logs --colusion 4

IMPORTANTE: el servidor debe estar en ESPERANDO mientras los clientes hacen
discovery (por diseño - ver /api/discovery). Corré el simulador, esperá a
que todos terminen de matricularse (lo avisa en consola), y recién ahí pasá
el examen a GRABANDO desde el dashboard real para medir el comportamiento
bajo carga durante la "grabación".
"""
import argparse
import os
import random
import threading
import time
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests
import pyotp

# Debe coincidir exactamente con el CODIGO_DISTRIBUCION configurado en el
# servidor (state.py) y en los agentes reales (client.py). Se puede
# sobreescribir con --codigo-distribucion si tu despliegue usa otro valor.
CODIGO_DISTRIBUCION_DEFAULT = os.environ.get(
    "CODIGO_DISTRIBUCION_SIMULADOR",
    "3R2M3HZA7ZTUXSN54EDTUFLSMKWZRMU5",
)

# ---------------------------------------------------------------------------
# Banco de contenido "realista" para el log sintético.
#
# Los formatos de abajo están copiados exactamente de cómo los escribe cada
# módulo real (no inventados):
#   - Keylogger:        Client/modules/keylogger/keylogger.py       -> "[ts] <texto>"
#   - Paperclip:         Client/modules/paperclip/paperclip.py       -> "[ts] CLIPBOARD_CHANGED content='...' length=N"
#   - Program Monitor:   Client/modules/program_monitor/program_monitor.py -> "[ts] CONTEXT_CHANGE app='...' title='...'"
#   - Sniffer:           Client/modules/sniffer/sniffer.py           -> "[ts] <dominio>"
#   - Error Detection:   Client/modules/error_detection/mod_site_customize.py -> "[ts] [INICIO]/[FIN]/[PRINT]/[CRASH DETECTADO] ..."
#   - Usb Detection:     Client/modules/usb_detection/utils.py       -> "[ts] ALERTA_USB Dispositivo=... | Archivo=... | Accion=... | Proceso=..."
#
# El tag de módulo con el que se envuelve cada línea (p.ej. "[ts][Keylogger]")
# sigue la convención de Client/orchestrator.py: combine_logs() usa
# log_file.stem.replace("_", " ").title(), por eso "program_monitor.log" ->
# "Program Monitor" y "error_detection.log" -> "Error Detection".
# ---------------------------------------------------------------------------

FRAGMENTOS_CODIGO = [
    "def resolver(nums):[ENTER]    total = 0[ENTER]    for n in nums:[ENTER]        total += n[ENTER]    return total",
    "class Pila:[ENTER]    def __init__(self):[ENTER]        self.datos = [][ENTER]    def push(self, x):[ENTER]        self.datos.append(x)",
    'import pandas as pd[ENTER]df = pd.read_csv("datos.csv")[ENTER]print(df.describe())',
    "for i in range(10):[ENTER]    if i % 2 == 0:[ENTER]        print(i)",
    "SELECT nombre, promedio FROM alumnos WHERE promedio >= 4.0 ORDER BY promedio DESC;",
    "public static int factorial(int n) {[ENTER]    if (n <= 1) return 1;[ENTER]    return n * factorial(n - 1);[ENTER]}",
]

APPS_PERMITIDAS_SIM = ["code.exe", "python.exe", "pythonw.exe"]
TITULOS_PERMITIDOS_SIM = [
    "main.py - Visual Studio Code",
    "tarea3.py - Visual Studio Code",
    "terminal - Visual Studio Code",
]

APPS_FUGA = ["chrome.exe", "firefox.exe", "msedge.exe"]
TITULOS_FUGA = [
    "ChatGPT",
    "Gemini - Google",
    "Claude",
    "Nueva pestaña",
    "WhatsApp Web",
]

DOMINIOS_BENIGNOS = [
    "github.com",
    "stackoverflow.com",
    "docs.python.org",
    "pypi.org",
    "developer.mozilla.org",
]
DOMINIOS_IA = ["chatgpt.com", "gemini.google.com", "claude.ai", "deepseek.com"]
DOMINIOS_MENSAJERIA = ["discord.com", "web.whatsapp.com", "chat.google.com", "telegram.org"]
DOMINIOS_BUSQUEDA_CRASH = ["stackoverflow.com", "google.com", "chatgpt.com"]

USB_DISPOSITIVOS = [
    ("Almacenamiento Masivo", "Kingston DataTraveler", "archivo_examen.docx", "COPIA_A_USB"),
    ("Dispositivo MTP", "iPhone de Alumno", "respuestas.txt", "CONEXION_DETECTADA"),
]


def _ts(momento):
    return momento.strftime("%Y-%m-%dT%H:%M:%S")


class GeneradorEventos:
    """Genera, para un cliente simulado y un instante dado, un lote de líneas
    de log con el mismo formato y las mismas etiquetas que produciría un
    agente real, incluyendo de forma intencional secuencias que deberían
    disparar las reglas del correlator y dar señal al comparator."""

    def __init__(self, client_id, prob_sospechoso, codigo_compartido=None, rng=None):
        self.client_id = client_id
        self.prob_sospechoso = prob_sospechoso
        # Si este cliente participa de un escenario de colusión, acá va el
        # fragmento de código que comparte con su "pareja" — a propósito
        # idéntico, para que el comparador los marque con similitud alta.
        self.codigo_compartido = codigo_compartido
        self.rng = rng or random.Random(client_id)

    def _linea(self, tag, contenido, momento):
        return f"[{_ts(momento)}][{tag}] {contenido}\n"

    def _fragmento_tecleado(self):
        if self.codigo_compartido and self.rng.random() < 0.5:
            return self.codigo_compartido
        return self.rng.choice(FRAGMENTOS_CODIGO)

    def generar_lote(self, ahora):
        """Devuelve una lista de líneas ya formateadas (texto, con \\n), con
        timestamps escalonados en los segundos anteriores a `ahora` para que
        cada paso de una secuencia caiga dentro de la ventana_segundos de la
        regla correspondiente en correlator.py."""
        lineas = []
        t = ahora - timedelta(seconds=self.rng.uniform(8, 12))

        def avanzar(seg_min=1.0, seg_max=3.0):
            nonlocal t
            t += timedelta(seconds=self.rng.uniform(seg_min, seg_max))
            return t

        # --- Actividad de base (siempre, para que el log no sea solo
        # eventos sospechosos — un alumno normal también teclea, cambia de
        # ventana y navega) ---
        lineas.append(self._linea("Program Monitor",
                                   f"CONTEXT_CHANGE app={self.rng.choice(APPS_PERMITIDAS_SIM)!r} "
                                   f"title={self.rng.choice(TITULOS_PERMITIDOS_SIM)!r}",
                                   avanzar()))
        lineas.append(self._linea("Keylogger", self._fragmento_tecleado(), avanzar()))
        if self.rng.random() < 0.5:
            lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_BENIGNOS), avanzar()))
        if self.rng.random() < 0.2:
            lineas.append(self._linea("Paperclip",
                                       f"CLIPBOARD_CHANGED content={self._fragmento_tecleado()!r} length=42",
                                       avanzar()))
        if self.rng.random() < 0.15:
            script = "tarea.py"
            lineas.append(self._linea("Error Detection", f"[INICIO] {script}", avanzar()))
            lineas.append(self._linea("Error Detection", f"[PRINT] Resultado: {self.rng.randint(1, 100)}", avanzar()))
            lineas.append(self._linea("Error Detection", f"[FIN] {script}", avanzar()))

        # --- Escenario sospechoso (ocasional, uno de los 6 por ciclo) ---
        if self.rng.random() < self.prob_sospechoso:
            escenario = self.rng.choice(["R-01", "R-02", "R-03", "R-04", "R-05", "R-06"])
            codigo = self.codigo_compartido or self.rng.choice(FRAGMENTOS_CODIGO)

            if escenario == "R-01":
                # Consulta a IA + pegado de código: Ctrl+C, visita a dominio
                # de IA, Ctrl+V — en ese orden, dentro de los 60s que exige R-01.
                lineas.append(self._linea("Keylogger", "[CTRL]+c[/CTRL]", avanzar()))
                lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_IA), avanzar()))
                lineas.append(self._linea("Keylogger", "[CTRL]+v[/CTRL]", avanzar()))
                lineas.append(self._linea("Keylogger", codigo, avanzar()))

            elif escenario == "R-02":
                lineas.append(self._linea("Paperclip",
                                           f"CLIPBOARD_CHANGED content={codigo!r} length={len(codigo)}",
                                           avanzar()))
                lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_IA), avanzar()))

            elif escenario == "R-03":
                lineas.append(self._linea("Paperclip",
                                           f"CLIPBOARD_CHANGED content={codigo!r} length={len(codigo)}",
                                           avanzar()))
                lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_MENSAJERIA), avanzar()))

            elif escenario == "R-04":
                lineas.append(self._linea("Error Detection",
                                           f"[CRASH DETECTADO] Excepcion: ValueError | Mensaje: invalid literal",
                                           avanzar()))
                lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_BUSQUEDA_CRASH), avanzar()))

            elif escenario == "R-05":
                app_fuga = self.rng.choice(APPS_FUGA)
                lineas.append(self._linea("Program Monitor",
                                           f"CONTEXT_CHANGE app={app_fuga!r} title={self.rng.choice(TITULOS_FUGA)!r}",
                                           avanzar()))
                lineas.append(self._linea("Keylogger", "[CTRL]+v[/CTRL]", avanzar()))
                # vuelve al editor para no dejar la "fuga" abierta para siempre
                lineas.append(self._linea("Program Monitor",
                                           f"CONTEXT_CHANGE app={self.rng.choice(APPS_PERMITIDAS_SIM)!r} "
                                           f"title={self.rng.choice(TITULOS_PERMITIDOS_SIM)!r}",
                                           avanzar()))

            elif escenario == "R-06":
                dispositivo, nombre, archivo, accion = self.rng.choice(USB_DISPOSITIVOS)
                lineas.append(self._linea(
                    "Usb Detection",
                    f"ALERTA_USB Dispositivo={dispositivo} ({nombre}) | Archivo={archivo} | "
                    f"Accion={accion} | Proceso=explorer.exe",
                    avanzar(),
                ))

        return lineas


@dataclass
class EstadisticasCliente:
    client_id: str
    discovery_ok: bool = False
    syncs_ok: int = 0
    syncs_error: int = 0
    syncs_saltados: int = 0
    latencias: list = field(default_factory=list)
    ultimo_error: str = ""


class ClienteSimulado(threading.Thread):
    """Un 'alumno' ficticio: su propio hilo, su propia sesión HTTP, su propio
    secreto de sincronización — igual de aislado que un agente real."""

    def __init__(self, client_id, server_url, codigo_distribucion, intervalo,
                 detener_evento, con_logs, stats, generador=None,
                 prob_desconexion=0.0, rng_desconexion=None):
        super().__init__(daemon=True)
        self.client_id = client_id
        self.server_url = server_url.rstrip("/")
        self.codigo_distribucion = codigo_distribucion
        self.intervalo = intervalo
        self.detener_evento = detener_evento
        self.con_logs = con_logs
        self.stats = stats
        self.generador = generador
        self.secreto_sync = None
        self.session = requests.Session()
        # Probabilidad, en cada ciclo, de que este "alumno" se salte el /sync
        # por completo — simula un corte de wifi, el notebook que se va a
        # dormir, una caída momentánea del túnel, etc. Es justo el tipo de
        # blip que el client.py real tiene que sobrevivir con su caché y sus
        # reintentos, así que el simulador debería poder provocarlo.
        self.prob_desconexion = prob_desconexion
        self.rng_desconexion = rng_desconexion or random.Random(client_id)
        # Cuando una desconexión "larga" está en curso, cuántos ciclos más
        # le quedan antes de volver a sincronizar.
        self._ciclos_desconectado_restantes = 0

    def _discovery(self, reintentos=40, espera_seg=3):
        """Reintenta hasta que el servidor esté en ESPERANDO y acepte la
        matrícula. Pensado para que puedas lanzar el simulador ANTES de
        poner el servidor en ESPERANDO, sin que todos los hilos fallen de
        entrada."""
        for intento in range(1, reintentos + 1):
            if self.detener_evento.is_set():
                return False
            try:
                r = self.session.get(
                    f"{self.server_url}/api/discovery",
                    params={"client_id": self.client_id},
                    headers={"X-Codigo-Distribucion": self.codigo_distribucion},
                    timeout=5,
                )
                if r.status_code == 200:
                    self.secreto_sync = r.json().get("secreto_sync")
                    if self.secreto_sync:
                        self.stats.discovery_ok = True
                        return True
                elif r.status_code == 403:
                    pass  # servidor no está en ESPERANDO todavía, o código incorrecto
                else:
                    self.stats.ultimo_error = f"discovery HTTP {r.status_code}: {r.text[:150]}"
            except requests.exceptions.RequestException as e:
                self.stats.ultimo_error = f"discovery: {e}"

            time.sleep(espera_seg)
        return False

    def _generar_otp(self):
        return pyotp.TOTP(self.secreto_sync, interval=30).now()

    def _un_sync(self):
        ahora = datetime.now()
        payload = {
            "client_id": self.client_id,
            "estado_local": "GRABANDO",
            "timestamp": _ts(ahora),
            "alertas": "[]",
            "configs": "{}",
            "codigo_otp": self._generar_otp(),
        }
        if self.con_logs and self.generador is not None:
            lineas = self.generador.generar_lote(ahora)
            contenido = "".join(lineas).encode("utf-8")
            archivos = {"archivo_log": (f"{self.client_id}.log", contenido)}
        else:
            archivos = {"archivo_log": ("", "")}

        inicio = time.monotonic()
        r = self.session.post(f"{self.server_url}/sync", data=payload, files=archivos, timeout=10)
        latencia_ms = (time.monotonic() - inicio) * 1000

        self.stats.latencias.append(latencia_ms)
        if r.status_code == 200:
            self.stats.syncs_ok += 1
        else:
            self.stats.syncs_error += 1
            self.stats.ultimo_error = f"sync HTTP {r.status_code}: {r.text[:150]}"

    def run(self):
        if not self._discovery():
            self.stats.ultimo_error = self.stats.ultimo_error or "discovery: se agotaron los reintentos"
            return

        # Desfase inicial aleatorio (basado en el hash del client_id, pero
        # determinístico) para no mandar los N primeros /sync exactamente
        # en el mismo instante — un examen real nunca arranca perfectamente
        # sincronizado entre máquinas.
        time.sleep((hash(self.client_id) % 1000) / 1000.0 * self.intervalo)

        while not self.detener_evento.is_set():
            if self._debe_saltar_este_ciclo():
                self.stats.syncs_saltados += 1
            else:
                try:
                    self._un_sync()
                except requests.exceptions.RequestException as e:
                    self.stats.syncs_error += 1
                    self.stats.ultimo_error = f"sync: {e}"
            self.detener_evento.wait(self.intervalo)

    def _debe_saltar_este_ciclo(self):
        """Decide si este ciclo se salta el /sync, simulando una
        desconexión. Si ya hay una desconexión "larga" en curso (varios
        ciclos seguidos, como un corte de wifi real) la sigue respetando;
        si no, tira los dados con prob_desconexion y, si sale, también
        decide cuántos ciclos extra dura el corte (la mayoría de las veces
        es un solo /sync perdido, pero a veces se estira un poco más, como
        en los cortes de Cloudflare que ya vimos en pruebas de campo)."""
        if self._ciclos_desconectado_restantes > 0:
            self._ciclos_desconectado_restantes -= 1
            return True
        if self.prob_desconexion and self.rng_desconexion.random() < self.prob_desconexion:
            self._ciclos_desconectado_restantes = self.rng_desconexion.choices(
                [0, 1, 2], weights=[60, 30, 10]
            )[0]
            return True
        return False


def imprimir_resumen(stats_por_cliente):
    total_ok = sum(s.syncs_ok for s in stats_por_cliente)
    total_err = sum(s.syncs_error for s in stats_por_cliente)
    no_matriculados = [s.client_id for s in stats_por_cliente if not s.discovery_ok]
    todas_latencias = [l for s in stats_por_cliente for l in s.latencias]

    print("\n" + "=" * 70)
    print(f"  RESUMEN — {len(stats_por_cliente)} clientes simulados")
    print("=" * 70)
    print(f"Matriculados correctamente : {len(stats_por_cliente) - len(no_matriculados)}/{len(stats_por_cliente)}")
    if no_matriculados:
        print(f"  [!] Nunca pasaron discovery: {', '.join(no_matriculados)}")
    total_salt = sum(s.syncs_saltados for s in stats_por_cliente)
    print(f"/sync exitosos              : {total_ok}")
    print(f"/sync fallidos              : {total_err}")
    print(f"/sync saltados (desconexión): {total_salt}")
    if todas_latencias:
        print(f"Latencia /sync (ms)         : "
              f"min={min(todas_latencias):.0f}  "
              f"p50={statistics.median(todas_latencias):.0f}  "
              f"max={max(todas_latencias):.0f}  "
              f"promedio={statistics.mean(todas_latencias):.0f}")
    errores_distintos = {s.client_id: s.ultimo_error for s in stats_por_cliente if s.ultimo_error}
    if errores_distintos:
        print("Últimos errores por cliente  :")
        for cid, err in list(errores_distintos.items())[:10]:
            print(f"  - {cid}: {err}")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="Simulador de carga concurrente para marcha blanca.")
    ap.add_argument("--server", required=True, help="URL del servidor, ej. https://servidor.ejemplo.org")
    ap.add_argument("--clientes", type=int, default=10, help="Cantidad de alumnos simulados (default: 10)")
    ap.add_argument("--intervalo", type=float, default=15.0, help="Segundos entre cada /sync por cliente (default: 15)")
    ap.add_argument("--duracion", type=float, default=0, help="Segundos totales de prueba (0 = correr hasta Ctrl+C)")
    ap.add_argument("--prefijo", default="simulado", help="Prefijo del client_id (default: 'simulado')")
    ap.add_argument("--con-logs", action="store_true",
                     help="Adjuntar logs sintéticos realistas (keystrokes, portapapeles, ventanas, dominios) "
                          "en cada /sync, ejercitando el correlator y el comparator, no solo el transporte")
    ap.add_argument("--prob-sospechoso", type=float, default=0.2,
                     help="Probabilidad por ciclo de que un cliente dispare una secuencia pensada para activar "
                          "una regla del correlator (default: 0.2). Solo aplica con --con-logs")
    ap.add_argument("--colusion", type=int, default=0,
                     help="Cantidad de clientes (agrupados de a 2) que comparten literalmente el mismo código "
                          "tecleado/pegado, para darle al comparador un caso de similitud alta con 'ground truth' "
                          "conocido. 0 = desactivado. Solo aplica con --con-logs")
    ap.add_argument("--prob-desconexion", type=float, default=0.0,
                     help="Probabilidad por ciclo de que un cliente se salte el /sync por completo "
                          "(simula wifi cortado, notebook suspendida, blip del túnel, etc.). "
                          "0 = desactivado (default)")
    ap.add_argument("--seed", type=int, default=None, help="Semilla para reproducir exactamente la misma corrida")
    ap.add_argument("--codigo-distribucion", default=CODIGO_DISTRIBUCION_DEFAULT, help="Debe coincidir con el del servidor")
    args = ap.parse_args()

    rng_global = random.Random(args.seed)

    print(f"[*] Lanzando {args.clientes} clientes simulados contra {args.server}")
    print(f"[*] Intervalo de sync: {args.intervalo}s | Logs sintéticos: {'sí' if args.con_logs else 'no'}")
    if args.con_logs:
        print(f"[*] Prob. de secuencia sospechosa por ciclo: {args.prob_sospechoso}")
        if args.colusion >= 2:
            print(f"[*] Colusión simulada entre los primeros {args.colusion} clientes (pares con código idéntico)")
    if args.prob_desconexion:
        print(f"[*] Prob. de desconexión por ciclo: {args.prob_desconexion}")
    print("[*] Esperando que el servidor esté en ESPERANDO para matricular a todos (reintenta solo)...\n")

    detener_evento = threading.Event()
    hilos = []
    stats_por_cliente = []

    # Arma los pares de colusión: a los clientes 0&1, 2&3, ... se les asigna
    # el mismo fragmento de código, elegido una sola vez por par.
    codigo_por_indice = {}
    if args.con_logs and args.colusion >= 2:
        for base in range(0, args.colusion - 1, 2):
            fragmento = rng_global.choice(FRAGMENTOS_CODIGO)
            codigo_por_indice[base] = fragmento
            codigo_por_indice[base + 1] = fragmento

    for i in range(args.clientes):
        client_id = f"{args.prefijo}_{i:03d}@sim"
        stats = EstadisticasCliente(client_id=client_id)
        stats_por_cliente.append(stats)

        generador = None
        if args.con_logs:
            generador = GeneradorEventos(
                client_id=client_id,
                prob_sospechoso=args.prob_sospechoso,
                codigo_compartido=codigo_por_indice.get(i),
                rng=random.Random(args.seed + i if args.seed is not None else None),
            )

        hilo = ClienteSimulado(
            client_id=client_id,
            server_url=args.server,
            codigo_distribucion=args.codigo_distribucion,
            intervalo=args.intervalo,
            detener_evento=detener_evento,
            con_logs=args.con_logs,
            stats=stats,
            generador=generador,
            prob_desconexion=args.prob_desconexion,
            rng_desconexion=random.Random(args.seed + i if args.seed is not None else None),
        )
        hilos.append(hilo)
        hilo.start()

    try:
        inicio = time.monotonic()
        while True:
            time.sleep(5)
            matriculados = sum(1 for s in stats_por_cliente if s.discovery_ok)
            total_ok = sum(s.syncs_ok for s in stats_por_cliente)
            total_err = sum(s.syncs_error for s in stats_por_cliente)
            total_salt = sum(s.syncs_saltados for s in stats_por_cliente)
            print(f"[{time.strftime('%H:%M:%S')}] matriculados={matriculados}/{args.clientes}  "
                  f"syncs_ok={total_ok}  syncs_error={total_err}  syncs_saltados={total_salt}")

            if args.duracion and (time.monotonic() - inicio) >= args.duracion:
                print("\n[*] Duración configurada alcanzada. Deteniendo...")
                break
    except KeyboardInterrupt:
        print("\n[*] Interrumpido por el usuario. Deteniendo...")

    detener_evento.set()
    for hilo in hilos:
        hilo.join(timeout=5)

    imprimir_resumen(stats_por_cliente)


if __name__ == "__main__":
    main()