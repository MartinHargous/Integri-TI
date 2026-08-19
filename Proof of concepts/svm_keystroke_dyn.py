import tkinter as tk
import time
from collections import deque
from pynput import keyboard
import numpy as np
from sklearn.svm import OneClassSVM
from sklearn.preprocessing import StandardScaler

# --- Nuevas importaciones para el gráfico ---
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

class KeystrokeSVMVisualAgent:
    def __init__(self, root):
        self.root = root
        self.root.title("Agente de Telemetría Visual: One-Class SVM")
        self.root.geometry("800x800") # Ventana más grande para acomodar el gráfico

        self.estado_actual = "INACTIVO" 
        self.last_press_time = None
        self.press_times = {} 
        
        self.datos_calibracion = []
        self.TECLAS_CALIBRACION = 100 
        
        self.scaler = StandardScaler()
        self.svm_model = OneClassSVM(nu=0.05, kernel="rbf", gamma="scale") 
        
        self.TAMANO_VENTANA = 60
        self.ventana_anomalias = deque(maxlen=self.TAMANO_VENTANA)
        
        # Historial para el gráfico (guardará los últimos 40 scores de distancia)
        self.historial_scores = deque(maxlen=self.TAMANO_VENTANA)
        
        self.alertas_lanzadas = 0
        self.listener = None

        # --- INTERFAZ GRÁFICA (UI) ---
        self.lbl_titulo = tk.Label(root, text="Autenticación Continua con Visualización SVM", font=("Arial", 14, "bold"))
        self.lbl_titulo.pack(pady=10)

        self.lbl_estado = tk.Label(root, text="Estado: Inactivo\nEsperando calibración...", font=("Arial", 12), fg="gray")
        self.lbl_estado.pack(pady=5)

        self.btn_frame = tk.Frame(root)
        self.btn_frame.pack(pady=5)

        self.btn_calibrar = tk.Button(self.btn_frame, text="Iniciar Calibración Automática", bg="lightblue", font=("Arial", 11), command=self.iniciar_calibracion)
        self.btn_calibrar.pack(side=tk.LEFT, padx=10)

        self.btn_detener = tk.Button(self.btn_frame, text="Detener Agente", bg="lightcoral", font=("Arial", 11), command=self.detener_todo)
        self.btn_detener.pack(side=tk.LEFT, padx=10)

        self.lbl_metricas = tk.Label(root, text="Métricas aparecerán aquí.", font=("Courier", 10), justify=tk.LEFT)
        self.lbl_metricas.pack(pady=10)

        self.lbl_alerta = tk.Label(root, text="", font=("Arial", 12, "bold"), fg="red")
        self.lbl_alerta.pack(pady=5)

        # --- CONFIGURACIÓN DEL GRÁFICO (MATPLOTLIB) ---
        self.fig, self.ax = plt.subplots(figsize=(7, 3), dpi=100)
        self.configurar_grafico_base()
        
        # Integrar el gráfico de Matplotlib dentro de Tkinter
        self.canvas = FigureCanvasTkAgg(self.fig, master=root)
        self.canvas.get_tk_widget().pack(pady=10)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def configurar_grafico_base(self):
        self.ax.clear()
        self.ax.set_title("Distancia a la Frontera de Soporte SVM", fontsize=10)
        self.ax.set_ylabel("Score (+ Normal / - Anomalía)", fontsize=8)
        self.ax.axhline(0, color='black', linewidth=1.5, linestyle='--') # Línea divisoria (0)
        
        # Colorear las zonas (Verde = Inlier, Rojo = Outlier)
        self.ax.axhspan(0, 5, facecolor='lightgreen', alpha=0.2)
        self.ax.axhspan(-5, 0, facecolor='salmon', alpha=0.2)
        
        self.ax.set_ylim(-3, 3) # Límites estáticos iniciales
        self.ax.set_xlim(0, self.TAMANO_VENTANA)
        self.ax.grid(True, linestyle=':', alpha=0.6)

    def actualizar_grafico(self):
        self.configurar_grafico_base()
        
        # Graficar los puntos actuales en memoria
        y_data = list(self.historial_scores)
        x_data = range(len(y_data))
        
        # Dibujar la línea principal
        self.ax.plot(x_data, y_data, color='blue', marker='o', markersize=4, linewidth=1.5)
        
        # Ajustar dinámicamente el eje Y si los valores se escapan
        if y_data:
            min_y, max_y = min(y_data), max(y_data)
            self.ax.set_ylim(min(-3, min_y - 1), max(3, max_y + 1))
            
        self.canvas.draw()

    # --- LÓGICA BIOMÉTRICA (Mantenida idéntica al código anterior) ---
    def extract_char(self, key):
        try: return key.char
        except AttributeError: return str(key)

    def iniciar_calibracion(self):
        self.detener_todo()
        self.estado_actual = "CALIBRANDO"
        self.datos_calibracion.clear()
        self.press_times.clear()
        self.last_press_time = None
        self.lbl_estado.config(text=f"Extrayendo vectores... 0/{self.TECLAS_CALIBRACION}", fg="blue")
        self.lbl_alerta.config(text="")
        
        self.listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self.listener.start()

    def iniciar_monitoreo(self):
        self.estado_actual = "AUDITANDO"
        self.press_times.clear()
        self.last_press_time = None
        self.ventana_anomalias.clear()
        self.historial_scores.clear()
        self.actualizar_grafico()
        
        self.lbl_estado.config(text="Auditoría SVM ACTIVADA.\nEvaluando vectores bidimensionales en tiempo real.", fg="green")
        self.lbl_alerta.config(text="")
        
        if self.listener is None:
            self.listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
            self.listener.start()

    def detener_todo(self):
        self.estado_actual = "INACTIVO"
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def on_key_press(self, key):
        if self.estado_actual == "INACTIVO": return
        current_time = time.time()
        char = self.extract_char(key)
        if char and char not in self.press_times:
            self.press_times[char] = current_time

    def on_key_release(self, key):
        if self.estado_actual == "INACTIVO": return
        current_time = time.time()
        char = self.extract_char(key)
        
        if char and char in self.press_times:
            hold_time = current_time - self.press_times[char]
            del self.press_times[char]
            
            if hold_time > 0.5: return

            if self.last_press_time is not None:
                flight_time = current_time - self.last_press_time
                if flight_time < 1.5:
                    vector_actual = [flight_time, hold_time]
                    
                    if self.estado_actual == "CALIBRANDO":
                        self.procesar_calibracion(vector_actual)
                    elif self.estado_actual == "AUDITANDO":
                        self.procesar_auditoria(vector_actual)
                        
            self.last_press_time = current_time

    def procesar_calibracion(self, vector):
        self.datos_calibracion.append(vector)
        progreso = len(self.datos_calibracion)
        self.root.after(0, self.lbl_estado.config, {"text": f"Extrayendo vectores... {progreso}/{self.TECLAS_CALIBRACION}"})
        
        if progreso >= self.TECLAS_CALIBRACION:
            self.root.after(0, self.entrenar_pipeline_svm)

    def entrenar_pipeline_svm(self):
        self.detener_todo()
        X_train = np.array(self.datos_calibracion)
        X_train_scaled = self.scaler.fit_transform(X_train)
        self.svm_model.fit(X_train_scaled)
        
        self.lbl_estado.config(text="Modelo SVM Entrenado.\nIniciando auditoría automáticamente...", fg="blue")
        self.lbl_metricas.config(text=f"Pipeline listo.\nFrontera calculada sobre {self.TECLAS_CALIBRACION} vectores.")
        self.root.after(1500, self.iniciar_monitoreo)

    def procesar_auditoria(self, vector):
        X_nuevo = np.array([vector])
        X_nuevo_scaled = self.scaler.transform(X_nuevo)
        
        prediccion = self.svm_model.predict(X_nuevo_scaled)[0]
        
        # Extraer la distancia exacta para el gráfico
        distancia_frontera = self.svm_model.decision_function(X_nuevo_scaled)[0]
        self.historial_scores.append(distancia_frontera)
        
        es_anomalo = 1 if prediccion == -1 else 0
        self.ventana_anomalias.append(es_anomalo)
        
        anomalias_actuales = sum(self.ventana_anomalias)
        estado_vector = "ANOMALÍA" if es_anomalo else "NORMAL"
        
        metricas_txt = (
            f"Clasificación actual: {estado_vector} (Distancia: {distancia_frontera:.3f})\n"
            f"Anomalías en ventana: {anomalias_actuales}/{len(self.ventana_anomalias)}"
        )
        
        # Actualizar UI y Gráfico
        self.root.after(0, self.lbl_metricas.config, {"text": metricas_txt})
        self.root.after(0, self.actualizar_grafico)
        
        if len(self.ventana_anomalias) == self.TAMANO_VENTANA:
            if anomalias_actuales >= (self.TAMANO_VENTANA * 0.25):
                self.root.after(0, self.lanzar_alerta)

    def lanzar_alerta(self):
        self.alertas_lanzadas += 1
        self.lbl_alerta.config(text=f"¡ALERTA DE SEGURIDAD! (x{self.alertas_lanzadas})\nEl SVM detectó un cambio de operador.")
        self.ventana_anomalias.clear()

    def on_closing(self):
        self.detener_todo()
        self.root.quit()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = KeystrokeSVMVisualAgent(root)
    root.mainloop()