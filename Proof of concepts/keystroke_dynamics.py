import tkinter as tk
import time
import statistics
from collections import deque
from pynput import keyboard

class RealTimeKeystrokeAgent:
    def __init__(self, root):
        self.root = root
        self.root.title("Agente de Telemetría: Z-Score Dinámico")
        self.root.geometry("600x450")

        # --- Variables Biométricas ---
        self.estado_actual = "INACTIVO" 
        self.last_press_time = None
        
        # Calibración (Construcción de la Huella)
        self.datos_calibracion = []
        self.baseline_mean = 0.0
        self.baseline_std = 0.0
        self.TECLAS_CALIBRACION = 50 # Cuántas teclas necesitamos para la base
        
        # Ventana Móvil (Memoria a corto plazo)
        self.TAMANO_VENTANA = 40     # Evalúa lotes de 40 teclas
        self.UMBRAL_Z = 2.5          # Umbral Z-Score (2.5 desviaciones estándar)
        self.ventana_anomalias = deque(maxlen=self.TAMANO_VENTANA)
        self.alertas_lanzadas = 0
        
        self.listener = None

        # --- UI ---
        self.lbl_titulo = tk.Label(root, text="Auditoría Continua (Ventana Móvil)", font=("Arial", 14, "bold"))
        self.lbl_titulo.pack(pady=10)

        self.lbl_estado = tk.Label(root, text="Estado: Inactivo\nEsperando inicio de calibración...", font=("Arial", 12), fg="gray")
        self.lbl_estado.pack(pady=10)

        self.btn_frame = tk.Frame(root)
        self.btn_frame.pack(pady=10)

        self.btn_calibrar = tk.Button(self.btn_frame, text="Iniciar Agente (Calibración Automática)", bg="lightblue", font=("Arial", 11), command=self.iniciar_calibracion)
        self.btn_calibrar.pack(side=tk.LEFT, padx=10)

        # Dejé el botón de monitoreo por si quieres forzar un reinicio manual de la auditoría
        self.btn_monitorear = tk.Button(self.btn_frame, text="Forzar Auditoría", bg="lightgreen", font=("Arial", 11), state="disabled", command=self.iniciar_monitoreo)
        self.btn_monitorear.pack(side=tk.LEFT, padx=10)

        self.btn_detener = tk.Button(self.btn_frame, text="Detener Todo", bg="lightcoral", font=("Arial", 11), command=self.detener_todo)
        self.btn_detener.pack(side=tk.LEFT, padx=10)

        # Panel de telemetría en vivo
        self.lbl_metricas = tk.Label(root, text="Métricas en vivo aparecerán aquí.", font=("Courier", 11), justify=tk.LEFT)
        self.lbl_metricas.pack(pady=15)

        self.lbl_alerta = tk.Label(root, text="", font=("Arial", 12, "bold"), fg="red")
        self.lbl_alerta.pack(pady=10)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def iniciar_calibracion(self):
        self.detener_todo()
        self.estado_actual = "CALIBRANDO"
        self.datos_calibracion.clear()
        self.last_press_time = None
        self.lbl_estado.config(text=f"Calibrando... 0/{self.TECLAS_CALIBRACION} teclas", fg="blue")
        self.lbl_alerta.config(text="")
        
        self.listener = keyboard.Listener(on_press=self.on_key_press)
        self.listener.start()

    def iniciar_monitoreo(self):
        self.detener_todo()
        self.estado_actual = "AUDITANDO"
        self.last_press_time = None
        self.ventana_anomalias.clear()
        
        self.lbl_estado.config(text="Auditoría en tiempo real ACTIVADA.\nMonitoreando ritmo de tecleo de fondo.", fg="green")
        self.lbl_alerta.config(text="")
        
        self.listener = keyboard.Listener(on_press=self.on_key_press)
        self.listener.start()

    def detener_todo(self):
        self.estado_actual = "INACTIVO"
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def on_key_press(self, key):
        current_time = time.time()
        
        if self.last_press_time is not None:
            flight_time = current_time - self.last_press_time
            
            # FILTRO DE PAUSAS: Solo registramos escritura fluida (< 1.5s)
            if flight_time < 1.5:
                
                # --- FASE 1: CALIBRACIÓN ---
                if self.estado_actual == "CALIBRANDO":
                    self.datos_calibracion.append(flight_time)
                    progreso = len(self.datos_calibracion)
                    
                    self.root.after(0, self.lbl_estado.config, {"text": f"Calibrando... {progreso}/{self.TECLAS_CALIBRACION} teclas válidas"})
                    
                    if progreso >= self.TECLAS_CALIBRACION:
                        self.root.after(0, self.finalizar_calibracion)
                        
                # --- FASE 2: AUDITORÍA ---
                elif self.estado_actual == "AUDITANDO":
                    z_score = abs(flight_time - self.baseline_mean) / self.baseline_std
                    
                    es_anomalo = 1 if z_score > self.UMBRAL_Z else 0
                    self.ventana_anomalias.append(es_anomalo)
                    
                    anomalias_actuales = sum(self.ventana_anomalias)
                    metricas_txt = f"Latencia actual: {flight_time:.3f}s\nZ-Score: {z_score:.2f}\nAnomalías en ventana: {anomalias_actuales}/{len(self.ventana_anomalias)}"
                    self.root.after(0, self.lbl_metricas.config, {"text": metricas_txt})
                    
                    if len(self.ventana_anomalias) == self.TAMANO_VENTANA:
                        if anomalias_actuales >= (self.TAMANO_VENTANA * 0.2):
                            self.root.after(0, self.lanzar_alerta)

        self.last_press_time = current_time

    def finalizar_calibracion(self):
        self.detener_todo()
        
        # Construir la base estadística
        self.baseline_mean = statistics.mean(self.datos_calibracion)
        self.baseline_std = statistics.stdev(self.datos_calibracion)
        
        # Prevención de error por desviación cero
        if self.baseline_std == 0: 
            self.baseline_std = 0.01 
            
        self.btn_monitorear.config(state="normal")
        self.lbl_estado.config(text="Calibración completada.\nIniciando auditoría automáticamente...", fg="blue")
        self.lbl_metricas.config(text=f"Ritmo Base ({self.TECLAS_CALIBRACION} muestras):\nPromedio (μ): {self.baseline_mean:.3f}s\nDesviación (σ): {self.baseline_std:.3f}s")
        
        # TRANSICIÓN AUTOMÁTICA: Espera 1.5 segundos para que se lea el texto y salta a la auditoría
        self.root.after(1500, self.iniciar_monitoreo)

    def lanzar_alerta(self):
        self.alertas_lanzadas += 1
        self.lbl_alerta.config(text=f"¡ALERTA SILENCIOSA ENVIADA! (x{self.alertas_lanzadas})\nPatrón de tecleo alterado detectado.")
        
        self.ventana_anomalias.clear()

    def on_closing(self):
        self.detener_todo()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = RealTimeKeystrokeAgent(root)
    root.mainloop()