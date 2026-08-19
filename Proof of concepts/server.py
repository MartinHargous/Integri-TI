from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import time
import uvicorn

app = FastAPI(title="Panel del Profesor - Integridad de Exámenes")

comando_global = "ESPERANDO"  
clientes_conectados = {}     

class Heartbeat(BaseModel):
    client_id: str
    estado_local: str

@app.post("/heartbeat")
def receive_heartbeat(data: Heartbeat):
    # Actualizamos el estado del alumno y marcamos el instante exacto en que hizo ping
    clientes_conectados[data.client_id] = {
        "estado_local": data.estado_local,
        "ultimo_latido": time.time()
    }
    # Siempre le respondemos con el comando global actual
    return {"comando_global": comando_global}

# --- ENDPOINTS PARA EL PANEL (PROFESOR) ---
@app.post("/cambiar_estado")
def cambiar_estado(nuevo_estado: str = Form(...)):
    global comando_global
    comando_global = nuevo_estado
    return {"mensaje": "Estado actualizado", "estado": comando_global}

@app.get("/estado_red")
def obtener_estado_red():
    # Calcula quién está online (ping hace menos de 10 segundos) y quién se cayó
    tiempo_actual = time.time()
    red = []
    
    for client_id, info in clientes_conectados.items():
        segundos_inactivo = tiempo_actual - info["ultimo_latido"]
        status = "🟢 Online" if segundos_inactivo <= 10 else "🔴 Desconectado"
        
        red.append({
            "client_id": client_id,
            "estado_local": info["estado_local"],
            "status_conexion": status,
            "inactividad": round(segundos_inactivo, 1)
        })
    
    return {
        "comando_global": comando_global,
        "clientes": red
    }

# --- INTERFAZ GRÁFICA DEL PROFESOR (DASHBOARD) ---
@app.get("/", response_class=HTMLResponse)
def panel_profesor():
    html_content = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Panel del Profesor - Telemetría</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; color: #333; margin: 0; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
            .controles { display: flex; gap: 10px; margin: 20px 0; }
            button { padding: 10px 20px; border: none; border-radius: 5px; font-weight: bold; cursor: pointer; color: white; transition: background 0.3s; }
            .btn-esperar { background-color: #f39c12; }
            .btn-grabar { background-color: #27ae60; }
            .btn-fin { background-color: #e74c3c; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background-color: #34495e; color: white; }
            #estado-global { font-size: 1.2em; font-weight: bold; color: #2980b9; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Orquestador de Telemetría</h1>
            <p>Estado Global de la Prueba: <span id="estado-global">Cargando...</span></p>
            
            <div class="controles">
                <button class="btn-esperar" onclick="cambiarEstado('ESPERANDO')">Pausar / Esperar</button>
                <button class="btn-grabar" onclick="cambiarEstado('GRABANDO')">INICIAR PRUEBA</button>
                <button class="btn-fin" onclick="cambiarEstado('FINALIZADO')">FINALIZAR PRUEBA</button>
            </div>

            <h2>Monitoreo de Red</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID Alumno</th>
                        <th>Estado Agente</th>
                        <th>Conexión</th>
                        <th>Último Ping (s)</th>
                    </tr>
                </thead>
                <tbody id="tabla-clientes">
                    <tr><td colspan="4">Cargando datos de la red...</td></tr>
                </tbody>
            </table>
        </div>

        <script>
            // Función para enviar comandos al servidor
            async function cambiarEstado(nuevoEstado) {
                const formData = new FormData();
                formData.append('nuevo_estado', nuevoEstado);
                await fetch('/cambiar_estado', { method: 'POST', body: formData });
                actualizarPanel();
            }

            // Función para sondear el estado de la red (Polling)
            async function actualizarPanel() {
                try {
                    const response = await fetch('/estado_red');
                    const data = await response.json();
                    
                    document.getElementById('estado-global').innerText = data.comando_global;
                    
                    const tbody = document.getElementById('tabla-clientes');
                    tbody.innerHTML = '';
                    
                    if (data.clientes.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="4">No hay agentes conectados.</td></tr>';
                        return;
                    }

                    data.clientes.forEach(cliente => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><b>${cliente.client_id}</b></td>
                            <td>${cliente.estado_local}</td>
                            <td>${cliente.status_conexion}</td>
                            <td>Hace ${cliente.inactividad}s</td>
                        `;
                        tbody.appendChild(tr);
                    });
                } catch (error) {
                    console.error("Error al actualizar la red:", error);
                }
            }

            // Actualizar la tabla cada 2 segundos automáticamente
            setInterval(actualizarPanel, 2000);
            actualizarPanel();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)