# Informe de QA y Marcha Blanca — Integri-TI

## 1. Introducción

En el presente documento se describen las pruebas y cambios realizados durante el proceso de marcha blanca y control de calidad de un sistema para auditoría de integridad académica multi-agente, donde se recopila telemetría de las estaciones de trabajo de cada alumno, para después analizar y procesar la información con el fin de detectar infracciones, tales como el uso de inteligencia artificial generativa, colaboración no permitida entre alumnos, suplantación, uso de dispositivos USB no autorizados y la exfiltración de datos.

### Objetivos de las pruebas

El proceso se basó en la iteración entre pruebas controladas con varios usuarios de forma simultánea, el cual era seguido por un proceso de análisis de resultados, donde por último se aplicaban los cambios pertinentes a la solución. El proceso consistió en imitar de forma controlada una evaluación de programación en Python, donde todos los participantes intentaban resolver un ejercicio o examen.

1. Verificar la detección de actos de deshonestidad académica.
2. Analizar cómo se reflejan estos actos en los logs de telemetría recopilados para así ajustar las reglas de detección.
3. Identificar casos borde.
4. Errores en la ejecución de la solución.
5. Puntos ciegos de los módulos.
6. Evaluar precisión del análisis por un LLM para identificar y diagnosticar el proceso cognitivo a la hora de resolver un problema.

---

## 2. Pruebas realizadas

La marcha blanca combinó pruebas controladas sobre la arquitectura (sin alumnos reales de por medio, mediante un simulador de carga) con pruebas directas en equipos reales (Windows y Linux) durante el desarrollo del instalador y el agente.

### 2.1 Simulación de carga con logs sintéticos realistas

Se construyó un simulador (`simulador_carga.py`, independiente del repositorio del proyecto) que levanta varios clientes simulados en paralelo, cada uno generando líneas de log con el mismo formato exacto que produce cada módulo real (`Keylogger`, `Paperclip`, `Program Monitor`, `Sniffer`, `Error Detection`, `Usb Detection`), en vez de líneas genéricas de prueba. El simulador:

- Alterna actividad "normal" de un alumno con, probabilísticamente, una de las 6 secuencias de ataque que modelan las reglas por defecto del correlator (R-01 a R-06), incluyendo un mecanismo de código compartido entre dos clientes para simular colusión.
- Incorpora una probabilidad configurable de que un cliente **se salte ciclos de sincronización completos** (1 o 2 ciclos seguidos, con pesos 60/30/10), simulando cortes de WiFi intermitentes en vez de una desconexión total y permanente.
- Se validó generando **10.598 líneas sintéticas** y comparándolas contra las expresiones regulares reales que usan `correlator.py` y `comparator.py` para parsear logs: **0 líneas sin matchear**. En el proceso se encontró y corrigió un bug real del propio simulador (un fragmento de código Python con comilla simple embebida rompía el formato `CLIPBOARD_CHANGED content='...'` generado con `repr()`).

Esto permitió poner a prueba el correlator, el comparador y el dashboard bajo carga de varios alumnos simultáneos sin depender de una sesión real cada vez que se quería probar un cambio.

### 2.2 Pruebas de instalación en equipos reales

Se probó el instalador de Windows repetidas veces en equipos reales con configuraciones distintas:

- Un equipo con **solo Python 3.12.10** instalado (sin el launcher `py` apuntando a 3.14, la versión que el instalador exigía originalmente).
- Reinstalaciones sucesivas sobre el mismo equipo, para detectar residuos de instalaciones anteriores (venv, tareas programadas, hooks globales de Python).
- Instalación/desinstalación con el agente corriendo en primer plano para verificar que el proceso quedara realmente detenido.

### 2.3 Pruebas de interrupción y recuperación del agente

- **Suspensión manual del proceso** (no cierre) desde el Administrador de tareas, para validar que el vigilante (`watchdog.ps1`) lo detectara y reiniciara — a diferencia de un cierre normal, un proceso suspendido sigue "vivo" para cualquier chequeo que solo mire si el PID existe.
- **Corte real de WiFi** durante un examen en estado `GRABANDO`: se capturó el log completo del agente durante el incidente (reintentos fallidos, caída a búsqueda automática, rechazo repetido del servidor), que sirvió como evidencia directa para el rediseño descrito en 3.1 y 3.4.
- Kill directo del proceso durante `GRABANDO`, para verificar que el reinicio del agente retomara el examen correctamente vía el estado cacheado.

### 2.4 Pruebas del dashboard con múltiples clientes

Examen simulado con 2+ agentes conectados simultáneamente (uno real, otros vía el simulador de carga) para validar los paneles de Agentes, Alertas y Desconexiones del dashboard, incluyendo el flujo completo GRABANDO → corte de red → reconexión → FINALIZADO.

### 2.5 Observación de telemetría real del keylogger

Revisión manual de logs de teclado generados en un equipo Windows real con layout de teclado **Español Latinoamericano**, que permitió detectar que una letra puntual ("r") se registraba sistemáticamente como un carácter de tecla muerta (`´`) — ver 3.2.

---

## 3. Cambios aplicados

### 3.1 Cambios de arquitectura

- **Resolución dinámica de versión de Python compatible.** El instalador de Windows exigía originalmente Python 3.14 exacto vía el launcher `py`. Se reemplazó por una función (`Resolver-PythonCompatible`) que enumera las versiones instaladas con `py -0p` (soportando los dos formatos de salida del launcher, anterior y posterior a la reescritura de `py` en 3.13), elige la más nueva que sea ≥ 3.12, cae a un `python` genérico del PATH si no hay launcher, y solo como último recurso descarga e instala Python 3.14 en silencio. Esto desacopla la instalación de una versión exacta hardcodeada, ya que el proyecto en realidad solo requiere ≥ 3.12 (3.14 se eligió únicamente por mejoras de rendimiento del launcher, no por una dependencia funcional real).
- **Vigilancia del proceso agente vía Task Scheduler.** Una sola tarea programada con dos triggers (al iniciar sesión + repetición cada 1 minuto), ambos apuntando a un `.vbs` generado en tiempo de instalación con las rutas reales del equipo. El `.vbs` lanza `watchdog.ps1` sin crear ninguna ventana de consola (`WScript.Shell.Run(cmd, 0, False)`), a diferencia de `powershell.exe -WindowStyle Hidden`, que sí llega a crear la consola antes de ocultarla (parpadeo visible).
- **Distinción arquitectónica entre error de transporte y rechazo del servidor.** El ciclo de sincronización del agente (`SyncManager`) ahora separa explícitamente un error de red (`RequestException`: sin DNS, timeout, WiFi caído) de un rechazo explícito y definitivo del servidor (`401`). Solo el segundo caso dispara el abandono de la sesión cacheada y la vuelta a `discovery()`. El detalle completo y la motivación están en 3.4.
- **Reuso del reconstructor de texto tecleado en el motor de reglas.** El comparador de similitud (`comparator.py`) ya contaba con una función (`reconstruir_texto`) que simula el cursor real — backspace, flechas, mayúsculas sostenidas — para reconstruir lo que el alumno realmente escribió a partir del log crudo del keylogger. Esa función no se usaba en el motor de correlación de reglas (`correlator.py`), que hasta ahora matcheaba los patrones de lenguaje natural directamente contra el texto crudo. Se integró como una segunda pasada: si el patrón no matchea contra el texto crudo (necesario para mantener intactos los patrones de atajos de teclado, ej. `\[CTRL\]\+c`), se reintenta contra el texto reconstruido, exclusivamente para eventos de `keylogger`.

### 3.2 Corrección de errores encontrados

| # | Problema | Causa raíz | Corrección |
|---|---|---|---|
| 1 | El desinstalador de Windows reportaba "sin procesos residuales" aun con el agente vivo y sincronizando | `[regex]::Escape($RutaCliente)` combinado con el operador `-like`: `Regex.Escape()` duplica cada backslash (pensado para `-match`/regex), pero `-like` no es un motor de regex — el patrón buscaba dobles backslashes que nunca aparecen en un `CommandLine` real | Se reemplazó por `.Contains()` (comparación literal, sin ambigüedad de escapado), con dos pasadas separadas por 2s para cubrir una posible relanzada del vigilante justo antes de desinstalar |
| 2 | `ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'` al correr el agente en un equipo con Python 3.12.10, tras haberlo instalado antes con 3.14 | `python -m venv venv` no vacía un directorio ya existente; los binarios `.pyd` de `numpy` compilados para `cp314` quedaban en el venv aunque éste se recreara con `cp312`, y `pip` no los reinstalaba porque el dist-info ya figuraba como "satisfecho" | `python -m venv --clear venv` |
| 3 | El instalador generaba una tarea programada con un nombre distinto al que el desinstalador buscaba (`Agente_IntegriTI_*` vs `Agente_ProyectoTitulo_*`) | Drift entre una copia de referencia desactualizada y los archivos reales del proyecto, que ya habían sido renombrados | Unificado a `Agente_ProyectoTitulo_$UsuarioReal` en ambos scripts |
| 4 | Parpadeo visible de una ventana de consola al iniciar el agente vía el vigilante | `powershell.exe -WindowStyle Hidden` crea la consola antes de aplicarle el estilo oculto | Lanzamiento vía `.vbs` con `WScript.Shell.Run(cmd, 0, False)`, que nunca asigna una consola |
| 5 | En Windows, con layout de teclado Español Latinoamericano, la letra **"r"** se registraba sistemáticamente en el log como el carácter de una tecla muerta de acento (**"´"**) | Bug conocido de `pynput` en su backend de Windows: arma una tabla estática `scan_code → carácter` llamando a `ToUnicodeEx` una vez por cada combinación de modificadores; con layouts que tienen varias teclas muertas para acentos, `MapVirtualKeyEx` puede devolver un scan code que colisiona entre una tecla muerta y una letra normal, haciendo que esa letra herede el carácter de la tecla muerta | Para A-Z y 0-9 se usa directamente `key.vk` (virtual-key code de Windows, fijo e independiente del layout activo — `VK_A..VK_Z = 0x41..0x5A`, `VK_0..VK_9 = 0x30..0x39`) en vez de `key.char`, evitando por completo la tabla de teclas muertas. El resto de los caracteres (acentos, ñ, símbolos) sigue usando `key.char` |
| 6 | Frases en lenguaje natural dirigidas a una IA (ej. "me da error acá") no eran detectadas por las reglas del keylogger pese a estar realmente escritas en pantalla | El keylogger no borra nada al presionar backspace: solo agrega un marcador `[BASKSPACE]` al final del texto acumulado. Una sola corrección de tipeo en medio de la frase (ej. `eror[BASKSPACE][BASKSPACE]rror`) rompe cualquier coincidencia literal contra el texto crudo, aunque en pantalla se haya visto "error" con normalidad | Ver 3.1 — reuso de `reconstruir_texto()` en el motor de reglas |

### 3.3 Adiciones al sistema

- **Simulador de carga con logs sintéticos realistas** y probabilidad configurable de desconexión por cliente (ver 2.1).
- **Panel "Últimas Desconexiones"** en el dashboard, como pestaña propia (igual que el Comparador), que reutiliza el mismo feed de alertas existente sin necesidad de un endpoint nuevo. Detecta los eventos de desconexión/reconexión por **contenido** (no solo por `regla_id == "SISTEMA"`), de forma que sobreviven a un reanálisis manual de las reglas y a reglas de correlación propias que incorporen la misma línea del sistema.
- **Regla de correlación ampliada (R-22)** para detectar frases en español del tipo "me da/tira/sale/marca/manda error [...] acá/aquí", típicas de un alumno describiéndole un error puntual a un asistente de IA.
- **Resolución universal de intérprete Python** en el instalador (ver 3.1), en vez de exigir una versión exacta.

### 3.4 Edge cases encontrados

- **Corte de WiFi prolongado durante `GRABANDO`.** El agente, tras 3 fallos de sincronización consecutivos, abandonaba la sesión cacheada (secreto, `server_url`) y caía a `discovery()` — pero el servidor rechaza `GET /api/discovery` con `403` siempre que `comando_global != "ESPERANDO"`, sin excepción para un `client_id` ya conocido. Durante un examen en curso esto era un **callejón sin salida permanente**, resoluble únicamente cuando el profesor finalizaba el examen manualmente. Se evaluaron dos alternativas: (a) relajar la compuerta de `discovery` del lado del servidor para clientes ya conocidos, descartada porque `client_id` se deriva de usuario/hostname y es razonablemente adivinable dentro de una red de sala compartida — relajar esa compuerta permitiría a un tercero obtener el secreto de otro alumno fuera de la ventana de admisión controlada por el profesor; (b) un fix exclusivamente del lado cliente, adoptada: el agente ahora distingue un error de transporte (reintenta indefinidamente contra la misma sesión cacheada, con backoff acotado) de un `401` explícito (única señal real de que el secreto ya no sirve, típicamente tras un reset del profesor).
- **Vector de integridad secundario descubierto junto con el anterior**: como el agente detenía todos los módulos de captura (`stop_all()`) ante cualquier pérdida de conexión —incluso transitoria—, un alumno podía cortar su propio WiFi a propósito para pausar el monitoreo local (keylogger, portapapeles, monitor de contexto — ninguno de los cuales necesita red) sin que el proceso fuera detectado como suspendido ni terminado por el vigilante. Quedó cerrado como efecto colateral del fix anterior: ya no se detiene la captura ante un error de transporte puro.
- **Proceso suspendido (no terminado) externamente.** Un vigilante que solo chequee "¿existe el PID?" no detecta esto — el proceso sigue vivo pero no ejecuta nada. Requirió inspeccionar el estado real de sus hilos (`ThreadState = Wait` + `WaitReason = Suspended`, el marcador específico que deja `SuspendThread`/`NtSuspendProcess`, distinto de una espera normal de I/O).
- **Residuos entre reinstalaciones**: venv huérfano (bloqueado por DLLs nativas aún cargadas — Npcap, hooks de `pynput` — hasta que el proceso realmente termina), hook global `sitecustomize.py`/`PYTHONPATH` sin revertir, tarea programada fantasma.
- **Caso abierto, no resuelto**: se reportó una vez "el agente no inicia tras instalar, no aparece en el Administrador de tareas", sin poder reproducirse sin acceso a una máquina Windows real en el momento. Se agregó instrumentación de diagnóstico (`vigilante_log.txt`, registrando cada paso del vigilante) para la próxima vez que ocurra; la causa raíz sigue sin confirmarse.

### 3.5 Cambios al sistema de instalación

- Creación del entorno virtual con `--clear`, para no arrastrar binarios compilados de una versión de Python distinta entre reinstalaciones.
- Resolución dinámica de Python ≥ 3.12 en vez de exigir 3.14 exacto, con una verificación explícita post-creación del venv (`Test-Path venv\Scripts\python.exe`) que aborta limpiamente en vez de continuar en silencio si la creación falló.
- Desinstalador: verificación explícita de que la tarea programada se eliminó realmente (con reintento), kill de respaldo de procesos residuales corregido (`.Contains()` en dos pasadas), borrado del venv con reintentos crecientes (las DLLs nativas tardan en liberarse), exclusión de `config.txt` del borrado de residuos (para no perder la configuración del túnel en cada reinstalación), liberación del ACL de `state.json` antes de borrarlo (el agente lo bloquea contra el propio alumno).
- Lanzamiento del agente sin ventana de consola visible, vía un `.vbs` generado dinámicamente en el momento de instalar con las rutas reales del equipo.

---

## 4. Análisis de código estático

Se corrió **Bandit** sobre todo el código Python del proyecto (`Server/` y `Client/`), como continuación de una auditoría previa hecha con Semgrep sobre el mismo código. Los resultados de ambas herramientas se presentan juntos, con el estado real verificado contra el repositorio al momento de escribir este informe (commit `9034cf9`).

### 4.1 Hallazgos de la auditoría previa (Semgrep) — estado actual

| Hallazgo | Veredicto original | Estado verificado ahora |
|---|---|---|
| `wildcard-cors` en `Server/main.py` (`allow_origins=["*"]` junto con `allow_credentials=True`) | Real, innecesario — ningún flujo del proyecto requiere CORS cross-origin (dashboard same-origin, agentes vía `requests`, no navegador) | **Sigue presente en el repositorio.** Un registro anterior lo daba por corregido, pero el middleware CORS wildcard continúa en `Server/main.py` líneas 25-31 |
| HTTP sin cifrar en el discovery por LAN (`client.py`) | Riesgo de diseño aceptado — escaneo de subred local, sin DNS/certificado disponible | Sin cambios — aceptado |
| `insecure-file-permissions` (`chmod 0o666`) en `error_detection.py` | Pendiente — por confirmar si el permiso amplio es necesario por el hook global (corre bajo la sesión del alumno, no del agente elevado) | **Sigue pendiente** — confirmado también por Bandit (`B103`, severidad ALTA) en esta pasada |
| `missing-integrity` en scripts CDN (Tailwind, marked.js) | Parcialmente corregible — SRI viable para `marked.min.js`, no viable para el CDN dinámico de Tailwind | Sin cambios |
| `GET /profesor/comando/{nuevo_comando}` cambia el estado del examen sin protección CSRF | La dependencia `solo_local` no mitiga esto: una petición CSRF disparada desde el propio navegador del profesor también llega como loopback puro | **Sigue pendiente** — confirmado, el endpoint sigue siendo un `GET` que cambia estado, protegido solo por `solo_local` |

### 4.2 Resultados de Bandit (pasada nueva)

77 hallazgos en total sobre `Server/` y `Client/`:

| Severidad | Cantidad | Detalle |
|---|---|---|
| ALTA | 1 | `B103` — `chmod 0o666` en `error_detection.py` (mismo hallazgo que Semgrep, ver 4.1) |
| MEDIA | 1 | `B104` — bind a `0.0.0.0` en `Server/main.py`. **Evaluado como intencional y mitigado**: el servidor necesita aceptar conexiones LAN de los agentes y del túnel de Cloudflare; los endpoints sensibles (dashboard, administración, reglas) están protegidos por la dependencia `solo_local`, que rechaza explícitamente cualquier request con headers de proxy o que no venga de loopback |
| BAJA | 75 | Mayormente manejo de excepciones silencioso (`B110`/`B112`, 51 ocurrencias — `try/except: pass` o `continue`, deliberado en varios módulos de captura para no interrumpir el monitoreo ante un error puntual de un módulo) y llamadas a `subprocess` con ruta parcial (`B603`/`B607`/`B404`, 23 ocurrencias, en su mayoría invocaciones a `py`/`python`/`icacls`/`setx` ya validadas por el propio flujo del instalador) |

**Nota metodológica:** la auditoría previa usó Semgrep con reglas de la comunidad (`p/python`, `p/security-audit`); esta pasada se hizo con Bandit porque, en el entorno donde se escribió este informe, el registro de reglas de Semgrep no era alcanzable por red. El alcance de Bandit está limitado a Python — no cubre el JavaScript del dashboard ni los scripts de PowerShell del instalador, que quedan pendientes de una pasada con una herramienta específica para esos lenguajes.

### 4.3 Pendientes de esta sección

1. Resolver el CSRF de `GET /profesor/comando/{nuevo_comando}`.
2. Decidir y documentar (o corregir) el `chmod 0o666` en `error_detection.py`.
3. Quitar o acotar el middleware CORS wildcard de `Server/main.py`, que sigue sin corregirse pese a estar marcado como tal en un registro anterior.
4. Correr una auditoría equivalente sobre el JavaScript del dashboard y los scripts `.ps1` del instalador.

---

## 5. Falsos positivos y negativos

`[PENDIENTE DE DATOS REALES]` — esta sección todavía no cuenta con una tasa medida de falsos positivos/negativos contra uso real de alumnos; lo que sigue es la validación hecha hasta ahora y los riesgos conocidos por diseño de cada mecanismo de detección.

### 5.1 Validación hecha hasta ahora

La validación realizada (sección 2.1) confirma la **corrección del formato/parseo** de los logs sintéticos contra las expresiones regulares reales (10.598 líneas, 0 fallos de matcheo), pero esto mide fidelidad de formato, no la tasa de acierto de las reglas de detección frente a comportamiento real y variado de alumnos — son dos preguntas distintas.

### 5.2 Riesgos de falso positivo conocidos por diseño

- Un alumno que pega código **propio** (copiado de un archivo/sesión anterior del mismo editor, no de una IA) junto con una ventana de navegador abierta en segundo plano puede disparar reglas como R-01/R-02 (correlación de portapapeles + sitio de IA) sin haber consultado realmente a una IA.
- La regla ampliada en 3.3 (frases como "me da error acá") puede dispararse si el alumno le escribe eso a un **compañero por un canal permitido** (si existiera uno) o lo deja como comentario/nota para sí mismo, no necesariamente a una IA.
- `\[CTRL\]\+v` (pegado) se dispara también por acciones legítimas del editor (autocompletado, deshacer/rehacer encadenado, snippets).

### 5.3 Riesgos de falso negativo conocidos por diseño

- Colaboración o consulta a IA **verbal** o en un dispositivo externo (celular, otra laptop) no deja ningún rastro digital en el equipo monitoreado.
- Un alumno que **parafrasea** manualmente una respuesta de IA en vez de copiarla y pegarla literalmente evade toda regla basada en portapapeles.
- El vector de "cortar WiFi para pausar la captura" (3.4) quedó cerrado, pero cualquier otro mecanismo que no dependa de la red (ej. anular un módulo específico sin tocar la conexión) no está cubierto por ese fix puntual.

### 5.4 Metodología propuesta para medirlo

1. Ejecutar un examen controlado con el número real de alumnos esperado, con un subconjunto instruido explícitamente a intentar infracciones conocidas (para medir falsos negativos) y otro subconjunto resolviendo el examen de forma legítima pero con patrones de trabajo "ruidosos" (para medir falsos positivos: pegar código propio, tener el navegador abierto, etc.).
2. Comparar las alertas generadas por el sistema contra un registro manual de lo que efectivamente ocurrió en cada equipo durante la prueba.
3. Calcular precisión/recall por regla (no solo agregado), para poder recalibrar umbrales puntuales (ej. `ventana_segundos` de cada regla, `umbral_critico_score`/`umbral_alto_jaccard` del comparador) en vez de ajustar todo el sistema a ciegas.

---

## 6. Benchmarking de la solución

`[PENDIENTE DE DATOS REALES]` — no se ha corrido todavía una medición de desempeño bajo carga real. Lo que sigue es lo que ya se puso a prueba indirectamente y el plan de medición.

### 6.1 Lo ya ejercitado indirectamente

- El simulador de carga (2.1) validó la **correctitud** del pipeline completo (generación → `/sync` → correlator → dashboard) con varios clientes concurrentes, pero no se instrumentó para medir tiempos de respuesta, uso de CPU/memoria del servidor ni del agente bajo esa carga.
- El polling del dashboard está fijo en un intervalo de 2s (`setInterval(refrescarDashboard, 2000)`, `Server/static/js/main.js`) y el de sincronización del agente en 15s por defecto (`ADR-005`, `architerture.md`) — ambos valores de diseño, no calibrados todavía contra una medición real de carga.

### 6.2 Métricas a levantar

1. **Latencia y estabilidad de `/sync`** con *N* agentes reales sincronizando en paralelo cada 15s, variando *N* hasta encontrar el punto de degradación.
2. **Uso de CPU/memoria del agente cliente** en reposo y durante `GRABANDO` (los 7 módulos de telemetría corriendo simultáneamente), en un equipo de las especificaciones mínimas esperadas en sala de clases.
3. **Uso de CPU/memoria del servidor** con el comparador de similitud corriendo su análisis automático (por defecto cada 60s) sobre el total de alumnos conectados.
4. **Comportamiento del watchdog de silencio** (umbral actual: 60s) bajo condiciones reales de WiFi inestable — ver también 5.4, ya que esto se cruza con la calibración de falsos positivos de desconexión.
5. Tiempo real de instalación/desinstalación de punta a punta en un equipo de sala, incluyendo la descarga de Python cuando corresponde (peor caso del flujo descrito en 3.1).

### 6.3 Próximo paso

Ejecutar la marcha blanca con el número real de clientes esperado (ver también 5.4) registrando estas métricas desde el principio, en vez de medirlas por separado — ambas secciones (5 y 6) dependen de la misma corrida real con alumnos.