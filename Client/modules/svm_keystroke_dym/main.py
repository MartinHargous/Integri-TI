import datetime
import os
import site
import time
from collections import deque
from pathlib import Path

import numpy as np
from pynput import keyboard
from sklearn import callback
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM


class KeystrokeSVM:
	DEFAULTS = {
		"enabled": "true",
		"show_interface": "true",
		"log_file": "alerts.log",
		"train_chars": "100",
		"time_window": "60",
		"alert_threshold": "0.25",
		"max_hold_time": "0.5",
		"max_flight_time": "1.5",
		"svm_nu": "0.05",
		"svm_kernel": "rbf",
		"svm_gamma": "scale",
	}

	def __init__(self, root=None, config_path=None):
		self.root = root
		self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
		self.config = self._read_config()
		self.log_path = self._resolve_path(self.config["log_file"])

		self.state = "INACTIVE"
		self.last_press_time = None
		self.press_times = {}
		self.calibration_data = []
		self.listener = None
		self.alerts_launched = 0
		self.anomaly_window = deque(maxlen=self._int("time_window"))
		self.score_history = deque(maxlen=self._int("time_window"))
		self.scaler = StandardScaler()
		self.svm_model = OneClassSVM(
			nu=self._float("svm_nu"),
			kernel=self.config["svm_kernel"],
			gamma=self.config["svm_gamma"],
		)

		self.lbl_state = None
		self.lbl_metrics = None
		self.lbl_alert = None
		self.canvas = None
		self.ax = None
		if root is not None:
			self._build_ui()

	def _read_config(self):
		values = self.DEFAULTS.copy()
		if self.config_path.exists():
			for raw_line in self.config_path.read_text(encoding="utf-8-sig").splitlines():
				line = raw_line.strip()
				if not line or line.startswith(("#", ";")) or "=" not in line:
					continue
				key, value = line.split("=", 1)
				if key.strip().lower() in values:
					values[key.strip().lower()] = value.strip()
		return values

	def _bool(self, key):
		return self.config[key].lower() in {"1", "true", "yes", "on"}

	def _int(self, key):
		return int(self.config[key])

	def _float(self, key):
		return float(self.config[key])

	def _resolve_path(self, value):
		path = Path(os.path.expandvars(os.path.expanduser(value)))
		if not path.is_absolute():
			path = self.config_path.parent / path
		return path.resolve()

	def _build_ui(self):
		import tkinter as tk
		from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
		from matplotlib.figure import Figure

		self.root.title("Keystroke SVM Monitor")
		self.root.geometry("800x800")
		tk.Label(self.root, text="Continuous Keystroke Authentication", font=("Arial", 14, "bold")).pack(pady=10)
		self.lbl_state = tk.Label(self.root, text="State: Inactive\nWaiting for calibration...", fg="gray")
		self.lbl_state.pack(pady=5)
		buttons = tk.Frame(self.root)
		buttons.pack(pady=5)
		tk.Button(buttons, text="Start calibration", command=self.start_calibration).pack(side=tk.LEFT, padx=10)
		tk.Button(buttons, text="Stop agent", command=self.stop).pack(side=tk.LEFT, padx=10)
		self.lbl_metrics = tk.Label(self.root, text="Metrics will appear here.", justify=tk.LEFT)
		self.lbl_metrics.pack(pady=10)
		self.lbl_alert = tk.Label(self.root, text="", fg="red", font=("Arial", 12, "bold"))
		self.lbl_alert.pack(pady=5)
		figure = Figure(figsize=(7, 3), dpi=100)
		self.ax = figure.add_subplot(111)
		self.canvas = FigureCanvasTkAgg(figure, master=self.root)
		self.canvas.get_tk_widget().pack(pady=10)
		self._update_chart()
		self.root.protocol("WM_DELETE_WINDOW", self.close)

	def _ui(self, callback, *args):
		if self.root is not None:
			self.root.after(0, callback, *args)
		else:
            # Si no hay interfaz gráfica, ejecutamos la función directamente
			callback(*args)

	def _set_state_label(self, text, color="gray"):
		if self.lbl_state is not None:
			self.lbl_state.config(text=text, fg=color)

	def _log_alert(self, score, anomaly_count):
		self.log_path.parent.mkdir(parents=True, exist_ok=True)
		timestamp = datetime.datetime.now().isoformat(timespec="seconds")
		message = (
			f"[{timestamp}] ALERT score={score:.6f} "
			f"anomalies={anomaly_count}/{len(self.anomaly_window)}\n"
		)
		with self.log_path.open("a", encoding="utf-8") as log_file:
			log_file.write(message)

	def extract_key(self, key):
		try:
			return key.char
		except AttributeError:
			return str(key)

	def start_calibration(self):
		if not self._bool("enabled"):
			self._set_state_label("Disabled in config.txt", "red")
			return False
		self.stop()
		self.state = "CALIBRATING"
		self.calibration_data.clear()
		self.press_times.clear()
		self.last_press_time = None
		self._set_state_label(f"Collecting vectors... 0/{self._int('train_chars')}", "blue")
		self._start_listener()
		return True

	def start_monitoring(self):
		self.state = "AUDITING"
		self.press_times.clear()
		self.last_press_time = None
		self.anomaly_window.clear()
		self.score_history.clear()
		self._set_state_label("SVM auditing active.", "green")
		self._update_chart()
		self._start_listener()

	def _start_listener(self):
		if self.listener is None:
			self.listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
			self.listener.start()

	def stop(self):
		self.state = "INACTIVE"
		if self.listener is not None:
			self.listener.stop()
			self.listener = None

	def on_key_press(self, key):
		if self.state == "INACTIVE":
			return
		current_time = time.time()
		key_id = self.extract_key(key)
		if key_id and key_id not in self.press_times:
			self.press_times[key_id] = current_time

	def on_key_release(self, key):
		if self.state == "INACTIVE":
			return
		current_time = time.time()
		key_id = self.extract_key(key)
		if not key_id or key_id not in self.press_times:
			return
		hold_time = current_time - self.press_times.pop(key_id)
		if hold_time > self._float("max_hold_time"):
			return
		if self.last_press_time is not None:
			flight_time = current_time - self.last_press_time
			if flight_time < self._float("max_flight_time"):
				vector = [flight_time, hold_time]
				if self.state == "CALIBRATING":
					self.process_calibration(vector)
				elif self.state == "AUDITING":
					self.process_audit(vector)
		self.last_press_time = current_time

	def process_calibration(self, vector):
		self.calibration_data.append(vector)
		count = len(self.calibration_data)
		self._ui(self._set_state_label, f"Collecting vectors... {count}/{self._int('train_chars')}", "blue")
		if count >= self._int("train_chars"):
			self._ui(self.train)

	def train(self):
		if len(self.calibration_data) < 2:
			return False
		self.stop()
		data = self.scaler.fit_transform(np.asarray(self.calibration_data))
		self.svm_model.fit(data)
		self._set_state_label("SVM trained. Starting audit...", "blue")

		if self.root is not None:
			self.root.after(1500, self.start_monitoring)
		else:
			print("[INFO] SVM entrenado. Iniciando auditoría silenciosa...")
			self.start_monitoring()
        
		return True
	def process_audit(self, vector):
		scaled = self.scaler.transform(np.asarray([vector]))
		prediction = self.svm_model.predict(scaled)[0]
		score = float(self.svm_model.decision_function(scaled)[0])
		is_anomaly = int(prediction == -1)
		self.score_history.append(score)
		self.anomaly_window.append(is_anomaly)
		anomaly_count = sum(self.anomaly_window)
		if self.lbl_metrics is not None:
			self.lbl_metrics.config(text=f"Classification: {'ANOMALY' if is_anomaly else 'NORMAL'} (score: {score:.3f})\nAnomalies: {anomaly_count}/{len(self.anomaly_window)}")
		self._ui(self._update_chart)
		window_ready = len(self.anomaly_window) == self._int("time_window")
		threshold_reached = anomaly_count >= self._int("time_window") * self._float("alert_threshold")
		if window_ready and threshold_reached:
			self._ui(self.raise_alert, score, anomaly_count)
		return {"is_anomaly": bool(is_anomaly), "score": score, "anomalies": anomaly_count}

	def raise_alert(self, score=0.0, anomaly_count=0):
		self.alerts_launched += 1
		self._log_alert(score, anomaly_count)
		if self.lbl_alert is not None:
			self.lbl_alert.config(text=f"SECURITY ALERT (x{self.alerts_launched})\nPossible operator change detected.")
		self.anomaly_window.clear()

	def _update_chart(self):
		if self.ax is None:
			return
		self.ax.clear()
		self.ax.axhline(0, color="black", linewidth=1, linestyle="--")
		self.ax.plot(range(len(self.score_history)), list(self.score_history), color="blue", marker="o", markersize=3)
		self.ax.set_title("SVM decision score")
		self.ax.set_xlim(0, self._int("time_window"))
		self.ax.grid(True, linestyle=":", alpha=0.6)
		if self.canvas is not None:
			self.canvas.draw()

	def close(self):
		self.stop()
		if self.root is not None:
			self.root.destroy()

	def start(self):
		if not self._bool("enabled"):
			print("[INFO] Keystroke SVM agent is disabled in the configuration.")
			return
		if not self._bool("show_interface"):
			self.start_calibration()
			print("[INFO] Keystroke SVM agent started in background (no GUI).")
			
			try:
				while self.state != "INACTIVE" or self.listener is not None:
					time.sleep(1)
			except KeyboardInterrupt:
				self.stop()
			return

		if self.root is None:
			import tkinter as tk
			self.root = tk.Tk()
			self._build_ui()
		self.root.mainloop()


KeystrokeSVMVisualAgent = KeystrokeSVM


if __name__ == "__main__":
	KeystrokeSVM().start()