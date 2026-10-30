# Hito 2 — Reporte de QA, Auditoría de Seguridad y Marcha Blanca
**Proyecto:** Sistema de Monitoreo de Integridad Académica
**Periodo cubierto:** 27/09/2026 – 27/10/2026
**Fecha de entrega del hito:** 08/11/2026

> **Nota:** este documento es un borrador/ejemplo de estructura y contenido a partir del trabajo de hardening ya realizado. Los puntos marcados como `[PENDIENTE]` necesitan datos reales de la marcha blanca (tasas de falsos positivos/negativos, métricas de carga) que todavía no existen porque esa fase corre en paralelo a este hito.

---

## 1. Resumen ejecutivo

Durante este periodo el trabajo se concentró en tres frentes, todos derivados de pruebas de campo activas (no solo revisión de código en el papel):

1. **Rediseño del esquema de autenticación** agente↔servidor, pasando de un secreto OTP único compartido por todo el curso a un esquema de dos niveles (código de distribución + secreto personal por alumno).
2. **Corrección de una serie de edge cases** descubiertos al simular condiciones reales de examen: cierre abrupto del proceso del agente (Task Manager), reinicios del servidor a mitad de examen, y reconexión tras cortes de red — todos escenarios explícitamente dentro del alcance de este hito ("tolerancia a fallos de red", "cortes de conexión").
3. **Incorporación de análisis estático de código (Semgrep)** al flujo de trabajo, con una primera pasada de auditoría y triage de hallazgos.

En conjunto, estos cambios atacan directamente los tres objetivos declarados del hito: estabilidad bajo fallos de red, auditoría de seguridad del código, y preparación para la marcha blanca con múltiples clientes simultáneos.

---

## 2. Rediseño de autenticación: esquema de dos niveles

**Problema original:** un único secreto OTP compartido por todos los agentes significaba que filtrar ese valor (por ejemplo, decompilando o leyendo el repo de un solo alumno) comprometía la autenticación de todo el curso.

**Diseño implementado:**
- **Nivel 1 — Código de distribución:** un valor horneado en el repositorio, igual en servidor y cliente. Filtra tráfico externo que nunca clonó el proyecto en `GET /api/discovery`. No es secreto fuerte (el repo puede ser público) y no protege `/sync` por sí solo.
- **Nivel 2 — Secreto personal por alumno:** emitido por el servidor en la respuesta de `/api/discovery`, distinto para cada `client_id`, y es el que efectivamente autentica cada `POST /sync` (TOTP vía `pyotp`, `valid_window=2` para tolerar desfase de reloj).

**Consecuencia de diseño que se resolvió aparte:** si un cliente pierde la conexión, su secreto personal solo sobrevive si el servidor lo persiste. Esto llevó directamente al punto 3.

### 2.1 Mecanismo de descubrimiento (discovery)

El descubrimiento y la matriculación ocurren en un solo paso, en `GET /api/discovery`: el agente manda el código de distribución (header) y su `client_id`; si el servidor está en `ESPERANDO` y el código coincide, devuelve (o recupera, si el `client_id` ya tenía uno asignado) el secreto personal de ese alumno. Fuera de `ESPERANDO`, cualquier intento de discovery se rechaza con `403` — **a propósito**: evita que un alumno nuevo (o uno que perdió su caché) se matricule a mitad de examen.

El agente soporta dos modos de descubrimiento, fijados una sola vez al arrancar según la configuración (`config.txt`), sin mezclarse entre sí:

- **Modo túnel (Cloudflare u otro proxy HTTPS):** reintenta únicamente la `server_url` configurada. Es el modo pensado para el despliegue real (alumnos fuera de la LAN del servidor).
- **Modo LAN:** escanea la subred local (y las subredes privadas comunes) en busca del servidor, puerto 8000. Pensado para pruebas en la misma red sin exponer nada a internet.

**Riesgo de diseño identificado y aceptado (ver sección 7):** el modo LAN usa `http://` sin cifrar, porque no hay DNS ni certificado disponible para una IP interna. Esto significa que, durante el discovery en modo LAN, el secreto personal del alumno viaja en texto plano dentro de esa red — alguien con acceso a la misma LAN podría interceptarlo. El modo túnel no tiene este problema (Cloudflare fuerza HTTPS). Queda documentado como limitación conocida del modo LAN, no como algo a resolver con TLS casero en esta etapa.

**Endpoint adicional introducido durante el hardening:** `_verificar_reloj()` (chequeo de desfase de reloj agente↔servidor, basado en el header `Date`) reutilizaba `GET /api/discovery` solo para leer ese header — pero al estar gateado por `comando_global`, generaba un `403` "fantasma" en el log del servidor cada vez que un agente reconectaba por caché durante `GRABANDO` (ver fila 7 de la bitácora, sección 5). Se separó esa responsabilidad a un endpoint dedicado, `GET /api/ping`, sin ningún gating, devolviendo solo `{"status": "ok"}` — el discovery real queda exclusivamente para la matriculación inicial.

---

## 3. Persistencia de estado del servidor

Se agregó `server_state.json`, con escritura atómica (`tmp` + `os.replace`), que guarda `comando_global` y el diccionario completo de secretos por cliente. Antes de este cambio, un reinicio del proceso del servidor a mitad de examen:
- revertía silenciosamente el estado a `ESPERANDO`, y
- invalidaba los secretos de sincronización de **todos** los alumnos conectados, sin que ellos lo notaran hasta su siguiente intento de `/sync`.

Con la persistencia, el servidor restaura `comando_global` y los secretos al arrancar, sin interrumpir un examen en curso por un simple restart del proceso.

---

## 4. Protección de integridad del estado del cliente

El agente cachea su estado local (`state.json`: `server_url`, `client_id`, `estado_local`, `secreto_sync`) para poder reconectar sin pasar por discovery completo. Se protegió ese archivo contra edición/borrado por el propio alumno, aprovechando que el agente corre con privilegios elevados:

- **Linux:** `chattr +i` (inmutabilidad real a nivel de filesystem, bloquea escritura y borrado incluso para root hasta que se revierte).
- **Windows:** ACLs vía `icacls`, otorgando control total solo a `SYSTEM`/`Administradores` y denegando escritura/borrado al usuario autenticado — **sin** restringir lectura.

### 4.1 Edge case descubierto: ventana de carrera en el bloqueo de ACL

La primera implementación usaba `icacls /inheritance:r`, que **borra** todos los permisos heredados antes de volver a otorgarlos en pasos subsiguientes. Como la secuencia de comandos no es atómica, un `kill` del proceso justo en esa ventana (confirmado en pruebas de campo reales, no solo hipotético) dejaba `state.json` sin ningún permiso de lectura para el usuario normal — de forma permanente, requiriendo `icacls /reset` manual como Administrador.

**Fix:** reemplazo de `/inheritance:r` por `/inheritance:d` (convierte los permisos heredados en explícitos en vez de borrarlos — nunca hay una ventana sin lectura), más una lógica de auto-sanación en `_cargar_estado()`: si la lectura falla por permisos, se desbloquea, se reintenta, y se vuelve a bloquear ya con el ACL corregido.

### 4.2 Detección de silencio y watchdog en vivo

Antes de este periodo no existía ningún mecanismo para detectar cuándo un agente dejaba de reportar telemetría a mitad de examen (proceso terminado, corte de red, batería agotada) — ese hueco de monitoreo era invisible para el profesor. Se implementaron dos mecanismos complementarios:

1. **Detección retroactiva en `/sync`:** cuando un agente reconecta tras un silencio mayor al umbral (`UMBRAL_GAP_SEGUNDOS = 60`), el servidor escribe una línea sintética `AGENTE_INTERRUMPIDO gap=Ns` directamente en el log del alumno, visible en la auditoría forense igual que cualquier evento de módulo.
2. **Watchdog en vivo:** un hilo en segundo plano en el servidor revisa activamente a todos los clientes en `GRABANDO`, sin depender de que el agente vuelva a conectarse — así el profesor lo ve en el dashboard aunque el alumno nunca vuelva a reportar.

El umbral de 60s no es arbitrario: es consecuencia directa del *watchdog* de Task Scheduler en Windows, que relanza el proceso del agente cada minuto si detecta que no hay una instancia corriendo (agregado para cubrir el caso de un alumno terminando el proceso manualmente vía Administrador de Tareas). Ese watchdog acota el *downtime* máximo teórico de un agente terminado a ~60 segundos, y ese valor es el que fija el umbral de silencio sospechoso.

**Superficie nueva expuesta por este trabajo:** regla `R-08` (Interrupción del Agente) reconocida por el motor de correlación; campo `en_silencio` por alumno en `GET /api/status`; un cuarto estado visual ("Sin conexión") en la matriz de agentes del dashboard.

---

## 5. Bitácora de edge cases — fallos de red y reconexión

| # | Síntoma observado en campo | Causa raíz | Corrección |
|---|---|---|---|
| 1 | `422 Unprocessable Content` en `/api/discovery` tras una reconexión exitosa | `_verificar_reloj()` llamaba a discovery con la firma antigua (sin `client_id`/header) tras cambiar el contrato del endpoint | Se actualizó la llamada con los parámetros requeridos |
| 2 | Tras matar el proceso con Task Manager en pleno `GRABANDO`, el agente reconectaba pero ningún módulo de telemetría volvía a generar logs (solo `ErrorDetection`) | `_procesar_comando()` solo llama `orchestrator.start_all()` en una *transición* de estado; el camino de reconexión por caché asignaba `StateMachine = "GRABANDO"` directamente, sin pasar por una transición | Se agregó un `start_all()` explícito en el camino de reconexión por caché cuando el estado cacheado es `GRABANDO` |
| 3 | Reconexión por caché fallaba de forma intermitente y caía a discovery, quedando en callejón sin salida (discovery rechaza con 403 fuera de `ESPERANDO`) | Un solo intento de validación de 3s era insuficiente ante un blip transitorio de red (ej. reconexión del túnel de Cloudflare) | Reintentos (4 intentos, 3s entre cada uno) antes de rendirse a discovery |
| 4 | Tras un relanzamiento (watchdog de Task Scheduler), el `client_id` generado no coincidía con el de la sesión anterior, causando `401` consistente en `/sync` | `os.getlogin()` es poco confiable cuando el proceso se relanza sin sesión/consola asociada | Cascada de fallback: `USERNAME`/`USER` (variables de entorno, estables) antes de `os.getlogin()` |
| 5 | Ningún log de diagnóstico visible en instalaciones reales (Windows) | El agente corre con `pythonw.exe` a propósito (sin consola); `print()` se descartaba silenciosamente | Redirección de `stdout`/`stderr` a `agente_log.txt` con rotación simple a los 5MB |
| 6 | `state.json` ilegible tras un `kill` a mitad de cambio de estado (ver sección 4.1) | Ventana de carrera en `_bloquear_archivo` (Windows) | Ver sección 4.1 |
| 7 | `403 Forbidden` "fantasma" en el log del servidor en cada reconexión por caché exitosa, aparentando un fallo | `_verificar_reloj()` reusaba `/api/discovery` (gateado por `comando_global`) solo para leer el header `Date` | Nuevo endpoint dedicado `GET /api/ping`, sin gating, mínimo |
| 8 | El desinstalador de Windows borraba `config.txt` (configuración del túnel/servidor) en cada reinstalación | Glob `*.txt` demasiado amplio en la limpieza de residuos | Exclusión explícita de `config.txt` del borrado |
| 9 | Ante un corte de WiFi prolongado durante `GRABANDO`, el cliente caía en un rechazo **permanente**: tras 3 fallos de red consecutivos abandonaba la caché (`server_url`/`secreto_sync`) y detenía todos los módulos de captura (`stop_all()`), cayendo a `discovery()` — pero `/api/discovery` rechaza con `403` siempre que `comando_global != "ESPERANDO"`, así que durante un examen en curso ese camino nunca tenía salida hasta que el profesor finalizaba manualmente. Además, detener la captura por una simple caída de red (sin que el proceso fuera matado ni suspendido) abría un hueco de integridad: un alumno podía cortar su propio WiFi para pausar el monitoreo local sin ser detectado por el vigilante (que solo detecta hilos suspendidos). | `SyncManager()` y la validación de caché al inicio (`_validar_url_cacheada`) ahora distinguen explícitamente un `RequestException` (sin red/DNS/timeout) de un `401` real del servidor. Solo el `401` — la única señal inequívoca de que el secreto cacheado ya no es válido (p. ej. tras un reset del profesor) — dispara el abandono de caché, `stop_all()` y la vuelta a `discovery()`. Un error de red puro ya nunca detiene la captura ni toca el estado cacheado: solo reintenta con backoff progresivo (tope de 60s) contra el mismo `server_url`, apoyado en que `secretos_por_cliente` en el servidor no se invalida por silencio/timeout (solo por un reset explícito — ver sección 3), por lo que al recuperarse la red el próximo `/sync` exitoso retoma la sesión sin pérdida de datos (`combine_logs()` recoge todo lo acumulado localmente mientras no hubo conexión). Se evaluaron también alternativas de lado-servidor (reabrir `discovery` para `client_id` ya conocidos durante `GRABANDO`) y se descartaron por debilitar el límite de seguridad del `ESPERANDO`: un `client_id` es razonablemente adivinable en una red de sala (`usuario@hostname`), y aceptar su discovery fuera de la ventana controlada por el profesor permitiría a un tercero obtener el secreto de otro alumno. |

---

## 6. Robustez de instalación y desinstalación

Varios edge cases de este periodo no son de red/reconexión sino del ciclo de vida de instalación — relevantes para QA porque afectan directamente la reproducibilidad de la marcha blanca en máquinas nuevas:

| # | Problema detectado | Solución aplicada |
|---|---|---|
| 1 | El desinstalador de Windows no verificaba que la tarea programada realmente se hubiera eliminado, ni garantizaba que el proceso del agente quedara detenido. | Verificación explícita con `Get-ScheduledTask` (con reintento) y un *kill* de respaldo filtrando por línea de comando (`pythonw.exe`/`python.exe` ejecutando `client.py`). |
| 2 | Ni el instalador ni el desinstalador (Windows y Linux) revertían el *hook* global de Python (`sitecustomize.py`) ni la modificación de `PYTHONPATH` a nivel de usuario — quedaban instalados permanentemente incluso después de desinstalar el agente. | Los desinstaladores eliminan `sitecustomize.py` y el archivo bandera `.telemetria_active`, revierten quirúrgicamente la entrada de `PYTHONPATH` (sin tocar otras rutas del usuario) y remueven `.telemetria_global` si queda vacía. |
| 3 | El desinstalador de Linux no revertía el *bypass* de Wayland→Xorg en Debian — una modificación a nivel de todo el equipo, no solo del usuario del agente. | Reversión opcional, con confirmación explícita del operador dado su alcance de sistema completo, del archivo de configuración de GDM3. |
| 4 | El desinstalador de Windows borraba `config.txt` (configuración del túnel/servidor) en cada reinstalación — ver también fila 8 de la bitácora en sección 5. | Exclusión explícita de `config.txt` del glob de limpieza de residuos. |
| 5 | `requirements.txt` del servidor no reflejaba las dependencias reales del código (faltaban `datasketch` y `pyotp`). | Regenerado a partir de las importaciones reales de todos los módulos del servidor. |

---

## 7. Auditoría de seguridad con análisis estático (Semgrep)

Se integró **Semgrep OSS** (`semgrep --config=auto`) como primera pasada de auditoría, con triage manual de cada hallazgo:

| Hallazgo | Veredicto | Acción |
|---|---|---|
| `wildcard-cors` en `Server/main.py` (`allow_origins=["*"]`) | Real, innecesario — ningún flujo del proyecto requiere CORS (dashboard same-origin, agentes vía `requests`, no navegador) | **Corregido:** middleware CORS eliminado por completo |
| HTTP sin cifrar en el discovery por LAN (`client.py`) | Riesgo de diseño aceptado — escaneo de subred local, sin DNS/certificado disponible | Documentado con `nosemgrep` justificado |
| `insecure-file-permissions` (`0o666`) en `error_detection.py` | `[PENDIENTE]` — requiere confirmar si el permiso amplio es necesario por el hook global (corre bajo la sesión del alumno, no del agente elevado) | Pendiente de revisión |
| `missing-integrity` en scripts CDN (Tailwind, marked.js) | Parcialmente corregible — SRI viable para `marked.min.js` (versión fija), no viable para el CDN dinámico de Tailwind | `[PENDIENTE]` |

**Hallazgo adicional, fuera del alcance de Semgrep, encontrado durante la revisión manual:** `GET /profesor/comando/{nuevo_comando}` cambia el estado del examen mediante un **GET sin protección CSRF**. La dependencia `solo_local` (que exige IP loopback y rechaza headers de proxy) no mitiga esto, porque una petición CSRF disparada desde el propio navegador del profesor también llega como loopback puro. `[PENDIENTE — para resolver antes de producción]`.

**De-branding del proyecto:** al remover las referencias de nombre del proyecto en el código, se identificó que la whitelist de títulos de ventana del comparador (`titulos_permitidos` / `TITULO_PERMITIDO_CONTIENE`) dependía de un string hardcodeado ligado al nombre antiguo del proyecto — un **bug funcional real** (afecta la detección de fugas de contexto), no solo cosmético, descubierto como efecto colateral de una tarea de limpieza que a primera vista no parecía tocar lógica de negocio.

---

## 8. Marcha blanca — estado y métricas

`[PENDIENTE]` — esta sección se completa con datos de la ejecución real con múltiples clientes simultáneos:

- Tasa de falsos positivos / falsos negativos en detección de anomalías (sniffer, keylogger, correlator, comparador)
- Latencia y estabilidad de `/sync` bajo carga concurrente (N alumnos × intervalo de 15s)
- Comportamiento observado del watchdog de silencio (umbral actual: 60s) en condiciones reales de Wi-Fi inestable
- Calibración resultante de los umbrales del comparador (`umbral_critico_score`, `umbral_alto_jaccard`, etc.) a partir del comportamiento real de los usuarios de prueba

---

## 9. Próximos pasos antes de cierre del hito

1. Resolver el CSRF de `/profesor/comando/{nuevo_comando}` (sección 7).
2. Decidir y documentar la justificación (o corrección) del `chmod 0o666` en `error_detection.py`.
3. Ejecutar la marcha blanca con el número real de clientes esperado y completar la sección 7 con métricas.
4. Fijar el nuevo nombre/identidad del proyecto post-rebranding en la documentación (`architerture.md` y README) y limpiar el archivo `install_log.txt` commiteado por accidente (contiene una ruta local personal).
5. Correr Semgrep en CI (GitHub Actions) para que las próximas regresiones de este tipo se detecten automáticamente, no solo en pasadas manuales.