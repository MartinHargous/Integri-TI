"""
Simulador de carga para la marcha blanca de Hito 2.

Levanta N "alumnos" ficticios, cada uno en su propio hilo, que pasan por el
flujo real de autenticación (discovery con código de distribución -> secreto
personal -> /sync periódico con TOTP) exactamente como lo haría client.py.

A diferencia de un script que simplemente "asume" que el examen está
GRABANDO desde el primer sync, cada cliente simulado replica la máquina de
estados real de client.py: arranca en ESPERANDO, sigue mandando /sync vacíos
(sin logs) cada intervalo mientras espera, y recién empieza a adjuntar
telemetría sintética cuando el PROFESOR cambia el examen a GRABANDO desde el
dashboard real (el comando llega en el campo `comando_global` de la
respuesta de cada /sync, igual que al agente real — no hay otra forma de
que un cliente remoto se entere, `/api/status` es exclusivo del profesor).
Cuando el profesor pasa a FINALIZADO, cada cliente manda un último /sync con
`estado_local="FINALIZADO"` (con la telemetría pendiente, si la había) y se
detiene — igual que el reporte final que manda client.py antes de volver a
buscar un servidor en ESPERANDO.

Con --con-logs, además de ejercitar el transporte, cada /sync (mientras está
GRABANDO) adjunta entre 30 y 40 líneas de combined_log.log sintético pero
realista: keystrokes de keylogger (con los mismos tags [CTRL]+c[/CTRL] /
[CTRL]+v[/CTRL] que genera pynput de verdad), cambios de portapapeles,
cambios de ventana activa, dominios visitados y, de vez en cuando, una
secuencia completa pensada para disparar una de las reglas de
Server/correlator.py (R-01 a R-06) o para alimentar con señal real el motor
de similitud de Server/comparator.py (código pegado, portapapeles, fugas de
contexto, ejecuciones). La idea es poner a prueba el pipeline de detección
completo bajo carga, no solo medir cuántos HTTP 200 aguanta el servidor.

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
La interfaz gráfica (--gui) requiere tkinter (ya viene con Python en
Windows; en Linux puede requerir instalar el paquete del sistema, ej.
`sudo apt install python3-tk`).

Uso típico (línea de comandos):
    python simulador_carga.py --server https://servidor.ejemplo.org --clientes 30 --con-logs

Para apuntar a un servidor local de pruebas:
    python simulador_carga.py --server http://localhost:8000 --clientes 10 --intervalo 15 --con-logs

Para ejercitar también al comparador con un caso de colusión conocido:
    python simulador_carga.py --server http://localhost:8000 --clientes 10 --con-logs --colusion 4

Con interfaz gráfica (los mismos parámetros, editables en pantalla):
    python simulador_carga.py --gui

IMPORTANTE: el servidor debe estar en ESPERANDO mientras los clientes hacen
discovery (por diseño - ver /api/discovery). Lanzá el simulador, esperá a
que todos terminen de matricularse (lo avisa en consola / en la GUI), y
recién ahí pasá el examen a GRABANDO desde el dashboard real — los clientes
simulados lo detectan solos en su próximo /sync y empiezan a mandar
telemetría. Al pasar a FINALIZADO desde el dashboard, cada cliente manda su
último reporte y se detiene solo.
"""
import argparse
import os
import queue
import random
import sys
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

# Cuántas líneas de log sintético se adjuntan por cada /sync mientras el
# cliente está en GRABANDO (pedido explícito: entre 30 y 40 por ciclo, no
# un puñado como en la versión original pensada solo para probar el
# transporte).
LINEAS_LOG_MIN = 30
LINEAS_LOG_MAX = 40

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

# NOTA sobre variedad del código tecleado/pegado — IMPORTANTE para el
# comparador de similitud (Server/comparator.py):
#
# La versión original tenía una lista fija de solo 6 fragmentos, elegidos al
# azar en CADA evento de teclado para CUALQUIER cliente. Con decenas de
# clientes simulados sacando de un pool tan chico, era prácticamente
# garantizado que alumnos sin ninguna relación entre sí terminaran tecleando
# el mismo fragmento palabra por palabra — y el comparador (MinHash/Jaccard
# sobre el texto reconstruido, más similitud de portapapeles) lo marca como
# alta similitud igual que marcaría una colusión real. Eso fue lo que generó
# el aluvión de falsos positivos (score CRÍTICO entre pares que nunca
# compartieron nada a propósito).
#
# La corrección: en vez de un pool global compartido, cada cliente genera su
# PROPIO fragmento a partir de plantillas con variables/nombres/números
# sorteados independientemente (ver `_generar_fragmento_personal`), y lo
# reutiliza durante toda la corrida (como haría un alumno real trabajando en
# su propia solución). La combinatoria de plantillas x nombres x números hace
# que dos clientes sin relación casi nunca terminen con el mismo texto exacto.
# La colusión deliberada (--colusion) sigue funcionando igual que antes,
# forzando el mismo fragmento literal entre los dos miembros del par — eso
# es justamente lo que se quiere seguir detectando como señal real.
#
# Las plantillas son TODAS Python (el examen es Python, no tiene sentido que
# un alumno simulado aparezca tecleando SQL, Java o JS) y representan un
# mismo set fijo de ejercicios típicos de examen — como si todos los
# alumnos simulados estuvieran resolviendo el mismo enunciado, cada uno con
# su propio estilo de nombres de variables/funciones. Eso es justo el
# escenario que tiene sentido calibrar: similitud ALTA de verdad solo cuando
# dos alumnos comparten también el nombrado exacto (colusión), no solo por
# estar resolviendo el mismo ejercicio del examen.

PLANTILLAS_CODIGO = [
    # Suma de una lista (dos variantes de acumulación: += vs = x + y, para
    # que compartir plantilla no implique texto casi idéntico incluso con
    # nombres distintos — ver nota en _generar_fragmento_personal)
    "def {fn}({param}):[ENTER]    {var2} = 0[ENTER]    for {var} in {param}:[ENTER]        {var2} += {var}[ENTER]    return {var2}",
    "def {fn}({param}):[ENTER]    {var2} = 0[ENTER]    for {var} in {param}:[ENTER]        {var2} = {var2} + {var}[ENTER]    return {var2}",
    # Estructura de datos: pila
    "class {clase}:[ENTER]    def __init__(self):[ENTER]        self.{var} = [][ENTER]    def push(self, x):[ENTER]        self.{var}.append(x)",
    # Imprimir los pares de un rango
    "for {var} in range({n}):[ENTER]    if {var} % 2 == 0:[ENTER]        print({var})",
    # Fibonacci recursivo
    "def {fn}({var}):[ENTER]    if {var} <= 1:[ENTER]        return {var}[ENTER]    return {fn}({var} - 1) + {fn}({var} - 2)",
    # Ordenamiento burbuja
    "def {fn}({param}):[ENTER]    for {var} in range(len({param}) - 1):[ENTER]        for {var2} in range(len({param}) - {var} - 1):[ENTER]            if {param}[{var2}] > {param}[{var2} + 1]:[ENTER]                {param}[{var2}], {param}[{var2} + 1] = {param}[{var2} + 1], {param}[{var2}]",
    # Verificar palíndromo
    "def {fn}({param}):[ENTER]    {var} = {param}.lower().replace(' ', '')[ENTER]    return {var} == {var}[::-1]",
    # Promedio de una lista
    "{var} = [{n}, {n2}, {n3}][ENTER]{var2} = sum({var}) / len({var})[ENTER]print({var2})",
    # Búsqueda binaria
    "def {fn}({param}, {var2}):[ENTER]    izq, der = 0, len({param}) - 1[ENTER]    while izq <= der:[ENTER]        medio = (izq + der) // 2",
    # Validación de entrada con try/except
    'try:[ENTER]    {var} = int(input())[ENTER]except ValueError:[ENTER]    print("entrada invalida")',
    # Verificar si un número es primo
    "def {fn}({var}):[ENTER]    if {var} < 2:[ENTER]        return False[ENTER]    for {var2} in range(2, {var}):[ENTER]        if {var} % {var2} == 0:[ENTER]            return False[ENTER]    return True",
    # Invertir un string
    "def {fn}({param}):[ENTER]    {var} = ''[ENTER]    for {var2} in {param}:[ENTER]        {var} = {var2} + {var}[ENTER]    return {var}",
    # Máximo de una lista sin usar max()
    "def {fn}({param}):[ENTER]    {var} = {param}[0][ENTER]    for {var2} in {param}:[ENTER]        if {var2} > {var}:[ENTER]            {var} = {var2}[ENTER]    return {var}",
]

_POOL_FN = ["resolver", "calcular", "procesar", "obtener_resultado", "hacer_tarea",
            "ejercicio", "compute", "solve", "ejecutar", "validar"]
_POOL_VAR = ["n", "x", "i", "valor", "dato", "num", "elem", "item", "acc", "resultado",
             "total", "temp", "aux", "contador", "indice"]
_POOL_PARAM = ["nums", "lista", "datos", "valores", "arreglo", "coleccion", "serie", "entrada"]
_POOL_CLASE = ["Pila", "Cola", "Nodo", "Lista", "Stack", "Contenedor", "Registro"]
_POOL_NOMBRE_SCRIPT = ["tarea.py", "examen.py", "main.py", "solucion.py", "ejercicio.py",
                        "practica.py", "respuesta.py", "codigo.py", "script.py", "entrega.py"]


def _generar_fragmento_personal(rng):
    """Arma un fragmento de código único (en la práctica) para un cliente
    simulado, sorteando una de las plantillas del set de ejercicios del
    examen y sustituyendo TODOS sus placeholders (nombre de función,
    variables, nombre del parámetro, números) con valores independientes.
    Dos llamadas con dos `rng` distintos casi nunca producen el mismo texto
    exacto, a propósito — eso es lo que evita que el comparador confunda
    "dos alumnos resolviendo el mismo ejercicio del examen" con colusión
    real. Las plantillas parametrizan deliberadamente CADA identificador
    (no solo uno o dos) precisamente porque dejar texto literal compartido
    (p. ej. un nombre de parámetro fijo) alcanza para que SequenceMatcher
    marque como "portapapeles compartido" a alumnos sin ninguna relación
    entre sí que por azar sacaron la misma plantilla — confirmado con un
    benchmark real donde eso disparaba falsos positivos críticos (ver
    informe, sección 5.2)."""
    plantilla = rng.choice(PLANTILLAS_CODIGO)
    valores = {
        "fn": rng.choice(_POOL_FN),
        "var": rng.choice(_POOL_VAR),
        "var2": rng.choice(_POOL_VAR),
        "param": rng.choice(_POOL_PARAM),
        "clase": rng.choice(_POOL_CLASE),
        "n": rng.randint(1, 100),
        "n2": rng.randint(1, 100),
        "n3": rng.randint(1, 100),
    }
    return plantilla.format(**valores)


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


def _linea_clipboard(texto):
    """Arma la línea CLIPBOARD_CHANGED sin pasar por repr(): Python decide
    el caracter delimitador según el contenido (usa comillas dobles si el
    texto tiene una comilla simple adentro y ninguna doble), lo que rompía
    CLIPBOARD_RE en Server/comparator.py (espera siempre content='...' con
    comilla simple) apenas el código "tecleado" incluía una comilla simple
    — pasó con las plantillas de SQL/try-except. Para no depender de ese
    detalle de repr(), se escapa manualmente y se fuerza la comilla simple
    como delimitador, igual que el formato real documentado arriba."""
    escapado = texto.replace("\\", "\\\\").replace("'", "\\'")
    return f"CLIPBOARD_CHANGED content='{escapado}' length={len(texto)}"


class GeneradorEventos:
    """Genera, para un cliente simulado y un instante dado, un lote de líneas
    de log con el mismo formato y las mismas etiquetas que produciría un
    agente real, incluyendo de forma intencional secuencias que deberían
    disparar las reglas del correlator y dar señal al comparator."""

    def __init__(self, client_id, prob_sospechoso, codigo_compartido=None, rng=None,
                 nombre_script_fijo=False):
        self.client_id = client_id
        self.prob_sospechoso = prob_sospechoso
        # Si este cliente participa de un escenario de colusión, acá va el
        # fragmento de código que comparte con su "pareja" — a propósito
        # idéntico, para que el comparador los marque con similitud alta.
        self.codigo_compartido = codigo_compartido
        self.rng = rng or random.Random(client_id)
        # El código "propio" de este alumno: se sortea UNA sola vez por
        # cliente (plantilla + nombres/números independientes) y se reutiliza
        # durante toda la corrida, como haría un alumno trabajando en su
        # propia solución — y, sobre todo, para que dos clientes sin ninguna
        # relación entre sí casi nunca terminen tecleando el mismo texto
        # exacto (ver la nota junto a PLANTILLAS_CODIGO más arriba).
        self._fragmento_propio = _generar_fragmento_personal(self.rng)
        # Nombre del script que este alumno "ejecuta" (Error Detection
        # trio). Por defecto cada alumno saca uno distinto del pool, igual
        # que en la vida real donde casi nadie nombra su archivo igual que
        # otro salvo que el examen lo exija — eso evita que decenas de
        # alumnos sin relación entre sí choquen en "ejecución sincronizada"
        # solo porque el simulador los hizo correr el mismo nombre de
        # archivo. Con nombre_script_fijo=True se simula el escenario real
        # documentado en el informe (5.2) donde el examen SÍ exige un
        # nombre de entrega fijo para todos.
        # El pool de nombres genéricos (10 entradas) para 60 clientes
        # todavía deja ~6 alumnos por nombre — suficiente para que la
        # ventana de sincronía de ejecución (8s) choque por pura
        # coincidencia bastantes veces en una corrida larga (confirmado
        # con benchmark). Se le suma un número al azar para que el
        # espacio de nombres sea, en la práctica, único por alumno — igual
        # que en la vida real, donde casi nadie termina con el mismo
        # nombre de archivo exacto que otro salvo que el examen lo exija.
        self._nombre_script = ("tarea.py" if nombre_script_fijo
                                else f"{self.rng.choice(_POOL_NOMBRE_SCRIPT)[:-3]}_{self.rng.randint(1, 9999)}.py")

    def _linea(self, tag, contenido, momento):
        return f"[{_ts(momento)}][{tag}] {contenido}\n"

    def _fragmento_tecleado(self):
        if self.codigo_compartido and self.rng.random() < 0.5:
            return self.codigo_compartido
        return self._fragmento_propio

    def _linea_base_aleatoria(self, avanzar):
        """Una línea de actividad "normal" de alumno, elegida al azar entre
        los tipos de evento más comunes — usada para rellenar el lote hasta
        alcanzar el tamaño objetivo (30 a 40 líneas por ciclo).

        El peso de "paperclip" se mantiene deliberadamente bajo: un alumno
        real no pega al portapapeles varias veces por minuto durante horas.
        Con el peso anterior (20%, igual que keylogger) un examen de una
        hora con 60 clientes acumulaba ~450 eventos de portapapeles por
        alumno — y como Server/comparator.py indexa candidatos por
        cualquier palabra de 5+ letras compartida entre dos alumnos, eso
        marcaba básicamente TODOS los pares como candidatos y disparaba
        decenas de millones de comparaciones difflib (confirmado con un
        benchmark: 20 clientes / 20 min ya alcanzaban 41 millones). Bajar
        el volumen de portapapeles es lo que mantiene esto en un rango
        realista y hace que el comparador real pueda terminar a tiempo."""
        opcion = self.rng.choices(
            ["program_monitor", "keylogger", "sniffer", "paperclip"],
            weights=[30, 55, 12, 3],
        )[0]
        if opcion == "program_monitor":
            return self._linea(
                "Program Monitor",
                f"CONTEXT_CHANGE app={self.rng.choice(APPS_PERMITIDAS_SIM)!r} "
                f"title={self.rng.choice(TITULOS_PERMITIDOS_SIM)!r}",
                avanzar(),
            )
        if opcion == "keylogger":
            return self._linea("Keylogger", self._fragmento_tecleado(), avanzar())
        if opcion == "sniffer":
            return self._linea("Sniffer", self.rng.choice(DOMINIOS_BENIGNOS), avanzar())
        return self._linea("Paperclip", _linea_clipboard(self._contenido_portapapeles()), avanzar())

    def _contenido_portapapeles(self):
        """El contenido de un evento de portapapeles "normal". La mayoría de
        las veces un alumno copia algo chico (un nombre, un número) — no un
        bloque de código entero cada vez que usa Ctrl+C. Además de ser más
        realista, esto importa para el rendimiento real del comparador: un
        texto de menos de 15 caracteres queda excluido de las comparaciones
        de similitud (Server/comparator.py filtra por longitud), así que la
        mayoría de estos eventos ni siquiera llegan a compararse. Reservado
        para pegar el fragmento de código completo, poco frecuente — y
        siempre el caso en los escenarios R-02/R-03 deliberadamente
        sospechosos, que no pasan por aquí.

        OJO con subir este 2%: como `_fragmento_tecleado()` reutiliza el
        mismo fragmento propio del cliente en cada llamada, un porcentaje
        alto termina acumulando decenas de copias casi idénticas del mismo
        texto por alumno en una sesión larga — puro ruido que igual el
        comparador tiene que recorrer. Con 2% (unas pocas pegadas de
        código reales por hora, no decenas) el tiempo real del comparador
        sobre 60 alumnos / 1h de examen pasó de no terminar (>4 minutos)
        a unos segundos — benchmarkeado antes de bajar este valor."""
        if self.rng.random() < 0.02:
            return self._fragmento_tecleado()
        return self.rng.choice(_POOL_VAR + [str(self.rng.randint(0, 999)), "True", "False", "None"])

    def generar_lote(self, ahora, lineas_min=LINEAS_LOG_MIN, lineas_max=LINEAS_LOG_MAX):
        """Devuelve una lista de líneas ya formateadas (texto, con \\n), con
        timestamps escalonados en los segundos anteriores a `ahora`. El lote
        tiene entre `lineas_min` y `lineas_max` líneas de actividad base, más
        —ocasionalmente, aparte del conteo base— una secuencia sospechosa
        completa, para que cada paso de esa secuencia caiga dentro de la
        ventana_segundos de la regla correspondiente en correlator.py sin
        competir por espacio con el relleno."""
        objetivo = self.rng.randint(lineas_min, lineas_max)
        lineas = []
        # Suficiente margen de tiempo hacia atrás para que, con pasos de 1 a
        # 3 segundos entre líneas, el lote completo termine justo en "ahora".
        t = ahora - timedelta(seconds=self.rng.uniform(objetivo * 2.0, objetivo * 3.0))

        def avanzar(seg_min=1.0, seg_max=3.0):
            nonlocal t
            t += timedelta(seconds=self.rng.uniform(seg_min, seg_max))
            return t

        # --- Relleno de actividad "normal" hasta el tamaño objetivo (un
        # alumno real también teclea, cambia de ventana y navega todo el
        # rato, no solo en los instantes sospechosos) ---
        while len(lineas) < objetivo:
            lineas.append(self._linea_base_aleatoria(avanzar))
            # De cuando en cuando, un trío Error Detection INICIO/PRINT/FIN
            # (correr y revisar el resultado de un script), sin pasarse del
            # objetivo.
            if self.rng.random() < 0.08 and len(lineas) + 3 <= objetivo:
                script = self._nombre_script
                lineas.append(self._linea("Error Detection", f"[INICIO] {script}", avanzar()))
                # Server/comparator.py marca "outputs idénticos" por
                # coincidencia EXACTA de este string completo entre dos
                # alumnos (sin ventana de tiempo, set intersection sobre
                # toda la corrida — ver sincronia_ejecucion). Con un rango
                # angosto (1-100) y ~3 prints por alumno en una corrida de
                # 10 minutos, 60 alumnos ya generan colisiones casi
                # garantizadas por puro azar (confirmado: 20 pares CRÍTICO
                # con 'outputs idénticos' como señal en un benchmark real).
                # Un rango amplio hace que una coincidencia real sea,
                # estadísticamente, señal real y no ruido del simulador.
                lineas.append(self._linea("Error Detection", f"[PRINT] Resultado: {self.rng.randint(1, 10_000_000)}", avanzar()))
                lineas.append(self._linea("Error Detection", f"[FIN] {script}", avanzar()))

        # --- Escenario sospechoso (ocasional, uno de los 6 por ciclo), al
        # final y por fuera del conteo de relleno ---
        if self.rng.random() < self.prob_sospechoso:
            escenario = self.rng.choice(["R-01", "R-02", "R-03", "R-04", "R-05", "R-06"])
            codigo = self.codigo_compartido or self._fragmento_propio

            if escenario == "R-01":
                # Consulta a IA + pegado de código: Ctrl+C, visita a dominio
                # de IA, Ctrl+V — en ese orden, dentro de los 60s que exige R-01.
                lineas.append(self._linea("Keylogger", "[CTRL]+c[/CTRL]", avanzar()))
                lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_IA), avanzar()))
                lineas.append(self._linea("Keylogger", "[CTRL]+v[/CTRL]", avanzar()))
                lineas.append(self._linea("Keylogger", codigo, avanzar()))

            elif escenario == "R-02":
                lineas.append(self._linea("Paperclip", _linea_clipboard(codigo), avanzar()))
                lineas.append(self._linea("Sniffer", self.rng.choice(DOMINIOS_IA), avanzar()))

            elif escenario == "R-03":
                lineas.append(self._linea("Paperclip", _linea_clipboard(codigo), avanzar()))
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
    estado_local: str = "ESPERANDO"
    finalizado: bool = False


class ClienteSimulado(threading.Thread):
    """Un 'alumno' ficticio: su propio hilo, su propia sesión HTTP, su propio
    secreto de sincronización — igual de aislado que un agente real.

    Replica la máquina de estados de client.py: arranca en ESPERANDO
    (sincroniza sin adjuntar logs), pasa a GRABANDO recién cuando el
    servidor lo indica en la respuesta de /sync (`comando_global`), y al
    recibir FINALIZADO manda un último /sync con la telemetría pendiente
    antes de detenerse — exactamente como el reporte final de
    client.SyncManager()."""

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
        self.estado_local = "ESPERANDO"
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

            self.detener_evento.wait(espera_seg)
        return False

    def _generar_otp(self):
        return pyotp.TOTP(self.secreto_sync, interval=30).now()

    def _armar_archivos(self, ahora, con_logs_este_ciclo, sufijo=""):
        if con_logs_este_ciclo and self.generador is not None:
            lineas = self.generador.generar_lote(ahora)
            contenido = "".join(lineas).encode("utf-8")
            return {"archivo_log": (f"{self.client_id}{sufijo}.log", contenido)}
        return {"archivo_log": ("", "")}

    def _un_sync(self):
        """Un ciclo normal de sincronización. Solo adjunta telemetría
        sintética si este cliente ya está en GRABANDO — mientras está en
        ESPERANDO, sincroniza "en vacío" únicamente para enterarse de si el
        profesor ya cambió el estado del examen, igual que client.py."""
        ahora = datetime.now()
        con_logs_este_ciclo = self.con_logs and self.estado_local == "GRABANDO"
        payload = {
            "client_id": self.client_id,
            "estado_local": self.estado_local,
            "timestamp": _ts(ahora),
            "alertas": "[]",
            "configs": "{}",
            "codigo_otp": self._generar_otp(),
        }
        archivos = self._armar_archivos(ahora, con_logs_este_ciclo)

        inicio = time.monotonic()
        r = self.session.post(f"{self.server_url}/sync", data=payload, files=archivos, timeout=10)
        latencia_ms = (time.monotonic() - inicio) * 1000

        self.stats.latencias.append(latencia_ms)
        if r.status_code == 200:
            self.stats.syncs_ok += 1
            try:
                datos_servidor = r.json()
            except ValueError:
                datos_servidor = {}
            self._procesar_comando(datos_servidor.get("comando_global"), ahora)
        else:
            self.stats.syncs_error += 1
            self.stats.ultimo_error = f"sync HTTP {r.status_code}: {r.text[:150]}"

    def _procesar_comando(self, comando_global, ahora):
        """Reacciona al comando_global que viene en la respuesta de /sync,
        igual que _procesar_comando en client.py: ESPERANDO -> GRABANDO
        arranca el envío de telemetría; GRABANDO -> FINALIZADO manda el
        reporte final y detiene este cliente."""
        if comando_global == "GRABANDO" and self.estado_local != "GRABANDO":
            self.estado_local = "GRABANDO"
            self.stats.estado_local = "GRABANDO"
        elif comando_global == "FINALIZADO" and not self.stats.finalizado:
            self._enviar_sync_final(ahora)
        elif comando_global == "ESPERANDO" and self.estado_local == "GRABANDO":
            # El profesor volvió a ESPERANDO sin pasar por FINALIZADO (reset
            # manual) — no debería ocurrir en el flujo normal de un examen,
            # pero si pasa, el agente real también vuelve a ESPERANDO.
            self.estado_local = "ESPERANDO"
            self.stats.estado_local = "ESPERANDO"

    def _enviar_sync_final(self, ahora):
        """Réplica del reporte final que manda client.SyncManager() al
        recibir FINALIZADO: un último /sync con estado_local="FINALIZADO" y
        la telemetría pendiente (si la había), después del cual este cliente
        deja de sincronizar."""
        payload = {
            "client_id": self.client_id,
            "estado_local": "FINALIZADO",
            "timestamp": _ts(ahora),
            "alertas": "[]",
            "configs": "{}",
            "codigo_otp": self._generar_otp(),
        }
        archivos = self._armar_archivos(ahora, self.con_logs, sufijo="_final")
        try:
            self.session.post(f"{self.server_url}/sync", data=payload, files=archivos, timeout=10)
            self.stats.syncs_ok += 1
        except requests.exceptions.RequestException as e:
            self.stats.syncs_error += 1
            self.stats.ultimo_error = f"sync final: {e}"
        self.estado_local = "FINALIZADO"
        self.stats.estado_local = "FINALIZADO"
        self.stats.finalizado = True

    def run(self):
        if not self._discovery():
            self.stats.ultimo_error = self.stats.ultimo_error or "discovery: se agotaron los reintentos"
            return

        # Desfase inicial aleatorio (basado en el hash del client_id, pero
        # determinístico) para no mandar los N primeros /sync exactamente
        # en el mismo instante — un examen real nunca arranca perfectamente
        # sincronizado entre máquinas.
        self.detener_evento.wait((hash(self.client_id) % 1000) / 1000.0 * self.intervalo)

        while not self.detener_evento.is_set() and not self.stats.finalizado:
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


def construir_resumen(stats_por_cliente):
    """Calcula el resumen agregado como líneas de texto, reutilizable tanto
    por la consola (CLI) como por la GUI."""
    total_ok = sum(s.syncs_ok for s in stats_por_cliente)
    total_err = sum(s.syncs_error for s in stats_por_cliente)
    total_salt = sum(s.syncs_saltados for s in stats_por_cliente)
    no_matriculados = [s.client_id for s in stats_por_cliente if not s.discovery_ok]
    finalizados = sum(1 for s in stats_por_cliente if s.finalizado)
    todas_latencias = [l for s in stats_por_cliente for l in s.latencias]

    lineas = []
    lineas.append("=" * 70)
    lineas.append(f"  RESUMEN — {len(stats_por_cliente)} clientes simulados")
    lineas.append("=" * 70)
    lineas.append(f"Matriculados correctamente   : {len(stats_por_cliente) - len(no_matriculados)}/{len(stats_por_cliente)}")
    if no_matriculados:
        lineas.append(f"  [!] Nunca pasaron discovery: {', '.join(no_matriculados)}")
    lineas.append(f"Llegaron a FINALIZADO        : {finalizados}/{len(stats_por_cliente)}")
    lineas.append(f"/sync exitosos                : {total_ok}")
    lineas.append(f"/sync fallidos                : {total_err}")
    lineas.append(f"/sync saltados (desconexión)  : {total_salt}")
    if todas_latencias:
        lineas.append(
            f"Latencia /sync (ms)           : "
            f"min={min(todas_latencias):.0f}  "
            f"p50={statistics.median(todas_latencias):.0f}  "
            f"max={max(todas_latencias):.0f}  "
            f"promedio={statistics.mean(todas_latencias):.0f}"
        )
    errores_distintos = {s.client_id: s.ultimo_error for s in stats_por_cliente if s.ultimo_error}
    if errores_distintos:
        lineas.append("Últimos errores por cliente  :")
        for cid, err in list(errores_distintos.items())[:10]:
            lineas.append(f"  - {cid}: {err}")
    lineas.append("=" * 70)
    return lineas


def imprimir_resumen(stats_por_cliente):
    for linea in construir_resumen(stats_por_cliente):
        print(linea)


def crear_clientes(args, detener_evento):
    """Arma las N instancias de ClienteSimulado (y sus EstadisticasCliente)
    según los parámetros de `args`, sin arrancarlas todavía. Compartido por
    el modo consola y la GUI para no duplicar la lógica de armado de pares
    de colusión, semillas, etc."""
    rng_global = random.Random(args.seed)
    hilos = []
    stats_por_cliente = []

    # Arma los pares de colusión: a los clientes 0&1, 2&3, ... se les asigna
    # el mismo fragmento de código, elegido una sola vez por par.
    codigo_por_indice = {}
    if args.con_logs and args.colusion >= 2:
        for base in range(0, args.colusion - 1, 2):
            fragmento = _generar_fragmento_personal(rng_global)
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
                nombre_script_fijo=args.nombre_script_fijo,
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
        stats_por_cliente[-1] = stats
    return hilos, stats_por_cliente


def _construir_parser():
    ap = argparse.ArgumentParser(description="Simulador de carga concurrente para marcha blanca.")
    ap.add_argument("--server", default="", help="URL del servidor, ej. https://servidor.ejemplo.org (requerido salvo con --gui)")
    ap.add_argument("--clientes", type=int, default=10, help="Cantidad de alumnos simulados (default: 10)")
    ap.add_argument("--intervalo", type=float, default=15.0, help="Segundos entre cada /sync por cliente (default: 15)")
    ap.add_argument("--duracion", type=float, default=0, help="Segundos totales de prueba (0 = correr hasta Ctrl+C o FINALIZADO)")
    ap.add_argument("--max-requests", type=int, default=0,
                     help="Corta la corrida apenas la suma de /sync exitosos + fallidos de TODOS los clientes llega a "
                          "este número, en vez de medir por tiempo (0 = desactivado). Útil para benchmarking donde la "
                          "variable que importa es el volumen de requests, no cuánto duró el reloj — con --intervalo 15 "
                          "y N clientes, llegar a X requests toma aprox. (X / N) * 15 segundos, pero esto corta exacto "
                          "en el conteo real en vez de aproximarlo con --duracion.")
    ap.add_argument("--prefijo", default="simulado", help="Prefijo del client_id (default: 'simulado')")
    ap.add_argument("--con-logs", action="store_true",
                     help="Adjuntar logs sintéticos realistas (30-40 líneas por ciclo en GRABANDO: keystrokes, portapapeles, "
                          "ventanas, dominios) ejercitando el correlator y el comparator, no solo el transporte")
    ap.add_argument("--prob-sospechoso", type=float, default=0.2,
                     help="Probabilidad por ciclo de que un cliente dispare una secuencia pensada para activar "
                          "una regla del correlator (default: 0.2). Solo aplica con --con-logs. OJO con corridas "
                          "largas y muchos clientes: cada disparo de R-02/R-03 pega código completo al "
                          "portapapeles, y Server/comparator.py compara TODOS los pares de alumnos por "
                          "portapapeles (O(N^2)) — con el default 0.2, 60 clientes y 1h de examen, el comparador "
                          "tardó ~4 minutos en una corrida de referencia. Con 0.03 bajó a ~30s. Para pruebas de "
                          "carga largas/con muchos clientes, usar algo como 0.02-0.05.")
    ap.add_argument("--colusion", type=int, default=0,
                     help="Cantidad de clientes (agrupados de a 2) que comparten literalmente el mismo código "
                          "tecleado/pegado, para darle al comparador un caso de similitud alta con 'ground truth' "
                          "conocido. 0 = desactivado. Solo aplica con --con-logs")
    ap.add_argument("--prob-desconexion", type=float, default=0.0,
                     help="Probabilidad por ciclo de que un cliente se salte el /sync por completo "
                          "(simula wifi cortado, notebook suspendida, blip del túnel, etc.). "
                          "0 = desactivado (default)")
    ap.add_argument("--nombre-script-fijo", action="store_true",
                     help="Hace que TODOS los clientes 'ejecuten' el mismo nombre de archivo (tarea.py) en vez de "
                          "uno al azar por alumno. Por defecto (desactivado) cada alumno saca un nombre distinto "
                          "de un pool, para no inflar artificialmente los pares de 'ejecución sincronizada' entre "
                          "alumnos sin relación entre sí. Activar esto solo para simular el escenario real donde "
                          "el examen exige un nombre de entrega fijo para todos (ver informe, sección 5.2).")
    ap.add_argument("--seed", type=int, default=None, help="Semilla para reproducir exactamente la misma corrida")
    ap.add_argument("--codigo-distribucion", default=CODIGO_DISTRIBUCION_DEFAULT, help="Debe coincidir con el del servidor")
    ap.add_argument("--gui", action="store_true", help="Abrir la interfaz gráfica (tkinter) en vez de correr por consola")
    return ap


def main_cli(args):
    print(f"[*] Lanzando {args.clientes} clientes simulados contra {args.server}")
    print(f"[*] Intervalo de sync: {args.intervalo}s | Logs sintéticos: {'sí' if args.con_logs else 'no'}")
    if args.con_logs:
        print(f"[*] Prob. de secuencia sospechosa por ciclo: {args.prob_sospechoso}")
        if args.colusion >= 2:
            print(f"[*] Colusión simulada entre los primeros {args.colusion} clientes (pares con código idéntico)")
    if args.prob_desconexion:
        print(f"[*] Prob. de desconexión por ciclo: {args.prob_desconexion}")
    print("[*] Esperando que el servidor esté en ESPERANDO para matricular a todos (reintenta solo)...")
    print("[*] Cada cliente queda esperando el comando GRABANDO del profesor antes de mandar telemetría.\n")

    detener_evento = threading.Event()
    hilos, stats_por_cliente = crear_clientes(args, detener_evento)
    for hilo in hilos:
        hilo.start()

    try:
        inicio = time.monotonic()
        while True:
            time.sleep(5)
            matriculados = sum(1 for s in stats_por_cliente if s.discovery_ok)
            grabando = sum(1 for s in stats_por_cliente if s.estado_local == "GRABANDO")
            finalizados = sum(1 for s in stats_por_cliente if s.finalizado)
            total_ok = sum(s.syncs_ok for s in stats_por_cliente)
            total_err = sum(s.syncs_error for s in stats_por_cliente)
            total_salt = sum(s.syncs_saltados for s in stats_por_cliente)
            print(f"[{time.strftime('%H:%M:%S')}] matriculados={matriculados}/{args.clientes}  "
                  f"grabando={grabando}  finalizados={finalizados}  "
                  f"syncs_ok={total_ok}  syncs_error={total_err}  syncs_saltados={total_salt}")

            if finalizados == len(stats_por_cliente) and matriculados > 0:
                print("\n[*] Todos los clientes llegaron a FINALIZADO. Deteniendo...")
                break
            if args.duracion and (time.monotonic() - inicio) >= args.duracion:
                print("\n[*] Duración configurada alcanzada. Deteniendo...")
                break
            if args.max_requests and (total_ok + total_err) >= args.max_requests:
                print(f"\n[*] Cantidad de requests configurada alcanzada ({total_ok + total_err}/{args.max_requests}). Deteniendo...")
                break
    except KeyboardInterrupt:
        print("\n[*] Interrumpido por el usuario. Deteniendo...")

    detener_evento.set()
    for hilo in hilos:
        hilo.join(timeout=5)

    imprimir_resumen(stats_por_cliente)


# ---------------------------------------------------------------------------
# Interfaz gráfica (tkinter)
# ---------------------------------------------------------------------------

def lanzar_gui(valores_iniciales=None):
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox

    valores_iniciales = valores_iniciales or {}

    class SimuladorGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Simulador de carga — Integri-TI")

            self.detener_evento = None
            self.hilos = []
            self.stats_por_cliente = []
            self.corriendo = False
            self.inicio_monotonic = None
            self.duracion_configurada = 0.0
            self.max_requests_configurado = 0

            self._armar_formulario()
            self._armar_botones()
            self._armar_estado()
            self._armar_log()

        # --- construcción de widgets ---
        def _armar_formulario(self):
            marco = ttk.LabelFrame(self.root, text="Parámetros")
            marco.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

            def fila(etiqueta, valor_defecto, fila_n):
                ttk.Label(marco, text=etiqueta).grid(row=fila_n, column=0, sticky="w", padx=4, pady=2)
                var = tk.StringVar(value=str(valor_defecto))
                entrada = ttk.Entry(marco, textvariable=var, width=38)
                entrada.grid(row=fila_n, column=1, sticky="we", padx=4, pady=2)
                return var

            self.var_server = fila("Servidor (URL):", valores_iniciales.get("server", "http://localhost:8000"), 0)
            self.var_clientes = fila("Cantidad de clientes:", valores_iniciales.get("clientes", 10), 1)
            self.var_intervalo = fila("Intervalo de sync (s):", valores_iniciales.get("intervalo", 15.0), 2)
            self.var_duracion = fila("Duración máx. (s, 0 = sin límite):", valores_iniciales.get("duracion", 0), 3)
            self.var_max_requests = fila("Max. requests totales (0 = sin límite, prioridad sobre duración):", valores_iniciales.get("max_requests", 0), 4)
            self.var_prefijo = fila("Prefijo client_id:", valores_iniciales.get("prefijo", "simulado"), 5)
            self.var_prob_sospechoso = fila("Prob. secuencia sospechosa:", valores_iniciales.get("prob_sospechoso", 0.2), 6)
            self.var_colusion = fila("Clientes en colusión (pares):", valores_iniciales.get("colusion", 0), 7)
            self.var_prob_desconexion = fila("Prob. desconexión por ciclo:", valores_iniciales.get("prob_desconexion", 0.0), 8)
            self.var_seed = fila("Semilla (vacío = al azar):", valores_iniciales.get("seed", ""), 9)
            self.var_codigo_distribucion = fila("Código de distribución:", valores_iniciales.get("codigo_distribucion", CODIGO_DISTRIBUCION_DEFAULT), 10)

            self.var_con_logs = tk.BooleanVar(value=valores_iniciales.get("con_logs", True))
            ttk.Checkbutton(marco, text="Adjuntar logs sintéticos (30-40 líneas/ciclo en GRABANDO)",
                             variable=self.var_con_logs).grid(row=11, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

            self.var_nombre_script_fijo = tk.BooleanVar(value=valores_iniciales.get("nombre_script_fijo", False))
            ttk.Checkbutton(marco, text="Mismo nombre de archivo para todos (simula examen con nombre de entrega fijo)",
                             variable=self.var_nombre_script_fijo).grid(row=12, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 2))

            marco.columnconfigure(1, weight=1)

        def _armar_botones(self):
            marco = ttk.Frame(self.root)
            marco.grid(row=1, column=0, sticky="we", padx=8, pady=(0, 4))
            self.btn_iniciar = ttk.Button(marco, text="Iniciar simulación", command=self._on_iniciar)
            self.btn_iniciar.pack(side="left", padx=4)
            self.btn_detener = ttk.Button(marco, text="Detener", command=self._on_detener, state="disabled")
            self.btn_detener.pack(side="left", padx=4)

        def _armar_estado(self):
            marco = ttk.LabelFrame(self.root, text="Estado en vivo")
            marco.grid(row=2, column=0, sticky="we", padx=8, pady=4)

            self.lbl_matriculados = ttk.Label(marco, text="Matriculados: -")
            self.lbl_matriculados.grid(row=0, column=0, sticky="w", padx=6, pady=2)
            self.lbl_grabando = ttk.Label(marco, text="Grabando: -")
            self.lbl_grabando.grid(row=0, column=1, sticky="w", padx=6, pady=2)
            self.lbl_finalizados = ttk.Label(marco, text="Finalizados: -")
            self.lbl_finalizados.grid(row=0, column=2, sticky="w", padx=6, pady=2)
            self.lbl_syncs = ttk.Label(marco, text="/sync ok/error/saltados: -")
            self.lbl_syncs.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=2)
            self.lbl_latencia = ttk.Label(marco, text="Latencia (ms): -")
            self.lbl_latencia.grid(row=1, column=2, sticky="w", padx=6, pady=2)

        def _armar_log(self):
            marco = ttk.LabelFrame(self.root, text="Registro")
            marco.grid(row=3, column=0, sticky="nsew", padx=8, pady=(4, 8))
            self.root.rowconfigure(3, weight=1)
            self.root.columnconfigure(0, weight=1)

            self.texto_log = scrolledtext.ScrolledText(marco, width=90, height=16, state="disabled", wrap="word")
            self.texto_log.pack(fill="both", expand=True, padx=4, pady=4)

        def _log(self, mensaje):
            self.texto_log.configure(state="normal")
            self.texto_log.insert("end", mensaje + "\n")
            self.texto_log.see("end")
            self.texto_log.configure(state="disabled")

        # --- validación / parsing de los campos ---
        def _leer_args(self):
            server = self.var_server.get().strip()
            if not server:
                raise ValueError("Falta la URL del servidor.")
            seed_txt = self.var_seed.get().strip()
            ap = _construir_parser()
            argv = [
                "--server", server,
                "--clientes", self.var_clientes.get().strip() or "10",
                "--intervalo", self.var_intervalo.get().strip() or "15",
                "--duracion", self.var_duracion.get().strip() or "0",
                "--max-requests", self.var_max_requests.get().strip() or "0",
                "--prefijo", self.var_prefijo.get().strip() or "simulado",
                "--prob-sospechoso", self.var_prob_sospechoso.get().strip() or "0.2",
                "--colusion", self.var_colusion.get().strip() or "0",
                "--prob-desconexion", self.var_prob_desconexion.get().strip() or "0.0",
                "--codigo-distribucion", self.var_codigo_distribucion.get().strip() or CODIGO_DISTRIBUCION_DEFAULT,
            ]
            if seed_txt:
                argv += ["--seed", seed_txt]
            if self.var_con_logs.get():
                argv.append("--con-logs")
            if self.var_nombre_script_fijo.get():
                argv.append("--nombre-script-fijo")
            return ap.parse_args(argv)

        # --- manejadores de botones ---
        def _on_iniciar(self):
            if self.corriendo:
                return
            try:
                args = self._leer_args()
            except Exception as e:
                messagebox.showerror("Parámetros inválidos", str(e))
                return

            self._log(f"[*] Lanzando {args.clientes} clientes contra {args.server} "
                      f"(logs {'sí' if args.con_logs else 'no'}, intervalo {args.intervalo}s)")
            self._log("[*] Cada cliente espera el comando GRABANDO del profesor antes de mandar telemetría.")

            self.detener_evento = threading.Event()
            self.hilos, self.stats_por_cliente = crear_clientes(args, self.detener_evento)
            for hilo in self.hilos:
                hilo.start()

            self.duracion_configurada = args.duracion
            self.max_requests_configurado = args.max_requests
            self.inicio_monotonic = time.monotonic()
            self.corriendo = True
            self.btn_iniciar.configure(state="disabled")
            self.btn_detener.configure(state="normal")
            self.root.after(500, self._tick)

        def _on_detener(self, desde_tick=False):
            if not self.corriendo:
                return
            self.corriendo = False
            self.btn_iniciar.configure(state="normal")
            self.btn_detener.configure(state="disabled")
            if self.detener_evento:
                self.detener_evento.set()

            def _esperar_hilos():
                for hilo in self.hilos:
                    hilo.join(timeout=5)
                resumen = construir_resumen(self.stats_por_cliente)
                self.root.after(0, lambda: [self._log(l) for l in resumen])

            threading.Thread(target=_esperar_hilos, daemon=True).start()
            if not desde_tick:
                self._log("[*] Deteniendo por pedido del usuario...")

        # --- actualización periódica ---
        def _tick(self):
            if not self.corriendo:
                return
            matriculados = sum(1 for s in self.stats_por_cliente if s.discovery_ok)
            grabando = sum(1 for s in self.stats_por_cliente if s.estado_local == "GRABANDO")
            finalizados = sum(1 for s in self.stats_por_cliente if s.finalizado)
            total_ok = sum(s.syncs_ok for s in self.stats_por_cliente)
            total_err = sum(s.syncs_error for s in self.stats_por_cliente)
            total_salt = sum(s.syncs_saltados for s in self.stats_por_cliente)
            todas_latencias = [l for s in self.stats_por_cliente for l in s.latencias]

            self.lbl_matriculados.configure(text=f"Matriculados: {matriculados}/{len(self.stats_por_cliente)}")
            self.lbl_grabando.configure(text=f"Grabando: {grabando}")
            self.lbl_finalizados.configure(text=f"Finalizados: {finalizados}/{len(self.stats_por_cliente)}")
            self.lbl_syncs.configure(text=f"/sync ok/error/saltados: {total_ok}/{total_err}/{total_salt}")
            if todas_latencias:
                self.lbl_latencia.configure(
                    text=f"Latencia (ms): min={min(todas_latencias):.0f} "
                         f"p50={statistics.median(todas_latencias):.0f} max={max(todas_latencias):.0f}"
                )

            detener_por_fin = finalizados == len(self.stats_por_cliente) and matriculados > 0
            detener_por_duracion = (
                self.duracion_configurada
                and (time.monotonic() - self.inicio_monotonic) >= self.duracion_configurada
            )
            detener_por_requests = (
                self.max_requests_configurado
                and (total_ok + total_err) >= self.max_requests_configurado
            )
            if detener_por_fin or detener_por_duracion or detener_por_requests:
                if detener_por_fin:
                    motivo = "todos los clientes llegaron a FINALIZADO"
                elif detener_por_requests:
                    motivo = f"se alcanzó la cantidad de requests configurada ({total_ok + total_err}/{self.max_requests_configurado})"
                else:
                    motivo = "se alcanzó la duración configurada"
                self._log(f"[*] Deteniendo automáticamente: {motivo}.")
                self._on_detener(desde_tick=True)
                return

            self.root.after(500, self._tick)

    root = tk.Tk()
    SimuladorGUI(root)
    root.mainloop()


def main():
    # --gui puede combinarse con otros flags (para precargar valores) o
    # usarse solo; si no se pasa ningún argumento, también se abre la GUI
    # por comodidad en vez de fallar por falta de --server.
    ap = _construir_parser()
    if len(sys.argv) == 1:
        sys.argv.append("--gui")
    args = ap.parse_args()

    if args.gui:
        lanzar_gui({
            "server": args.server,
            "clientes": args.clientes,
            "intervalo": args.intervalo,
            "duracion": args.duracion,
            "max_requests": args.max_requests,
            "prefijo": args.prefijo,
            "con_logs": args.con_logs,
            "prob_sospechoso": args.prob_sospechoso,
            "colusion": args.colusion,
            "prob_desconexion": args.prob_desconexion,
            "seed": "" if args.seed is None else args.seed,
            "codigo_distribucion": args.codigo_distribucion,
            "nombre_script_fijo": args.nombre_script_fijo,
        })
        return

    if not args.server:
        ap.error("--server es requerido salvo que se use --gui")

    main_cli(args)


if __name__ == "__main__":
    main()