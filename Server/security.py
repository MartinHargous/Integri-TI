"""
Restricción de acceso por origen para los endpoints del profesor.

Los endpoints del dashboard, administración, reglas, módulos, logs, insights
y comparador solo deben ser accesibles desde el propio equipo del servidor.
Solo /sync y /api/discovery (los que usan los agentes) quedan abiertos.

Por qué NO basta con mirar request.client.host == "127.0.0.1":
cloudflared corre en la misma máquina que este servidor y le entrega las
peticiones por loopback. Todo el tráfico que llega por el túnel, incluido el
de internet, aparece aquí con IP origen 127.0.0.1. Por eso primero se rechazan
las peticiones que traen headers de proxy (los agrega siempre el edge de
Cloudflare y el cliente no puede quitarlos), y recién después se exige que la
IP TCP sea loopback (lo que bloquea a los alumnos en modo LAN, que llegan
directo al puerto 8000 con su IP de red).

Límite conocido: si algún día se pone otro proxy inverso en esta misma
máquina (nginx, etc.) que NO reenvíe estos headers, sus peticiones parecerían
locales. Con la arquitectura actual (solo cloudflared) no ocurre.
"""
from fastapi import Request, HTTPException

_HEADERS_DE_PROXY = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded")
_LOOPBACK = ("127.0.0.1", "::1")


def solo_local(request: Request):
    """Dependencia de FastAPI: rechaza (403) todo lo que no sea acceso directo desde este equipo."""
    for header in _HEADERS_DE_PROXY:
        if header in request.headers:
            raise HTTPException(
                status_code=403,
                detail="Este endpoint solo está disponible desde el equipo del servidor."
            )

    host = request.client.host if request.client else None
    if host not in _LOOPBACK:
        raise HTTPException(
            status_code=403,
            detail="Este endpoint solo está disponible desde el equipo del servidor."
        )