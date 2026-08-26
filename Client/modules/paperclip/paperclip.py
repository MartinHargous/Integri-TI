import datetime
import os
import threading
import time
from pathlib import Path

import pyperclip


class Paperclip:
	DEFAULTS = {
		"enabled": "true",
		"log_file": "paperclip.log",
		"poll_seconds": "0.5",
		"log_content": "true",
		"max_content_length": "100",
	}

	def __init__(self, config_path=None):
		self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
		self.config = self._read_config()
		self.log_path = self._resolve_path(self.config["log_file"])
		self.monitoring = False
		self._stop_event = threading.Event()
		self._monitor_thread = None
		self._last_clipboard = None

	def _read_config(self):
		values = self.DEFAULTS.copy()
		if self.config_path.exists():
			for raw_line in self.config_path.read_text(encoding="utf-8-sig").splitlines():
				line = raw_line.strip()
				if not line or line.startswith(("#", ";")) or "=" not in line:
					continue
				key, value = line.split("=", 1)
				key = key.strip().lower()
				if key in values:
					values[key] = value.strip()
		return values

	def _bool(self, key):
		return self.config[key].lower() in {"1", "true", "yes", "on"}

	def _resolve_path(self, value):
		path = Path(os.path.expandvars(os.path.expanduser(value)))
		if not path.is_absolute():
			path = self.config_path.parent / path
		return path.resolve()

	def _write_log(self, message):
		self.log_path.parent.mkdir(parents=True, exist_ok=True)
		timestamp = datetime.datetime.now().isoformat(timespec="seconds")
		with self.log_path.open("a", encoding="utf-8") as log_file:
			log_file.write(f"[{timestamp}] {message}\n")

	def _describe_clipboard(self, content):
		if not self._bool("log_content"):
			return f"CLIPBOARD_CHANGED length={len(content)}"
		limit = int(self.config["max_content_length"])
		preview = content[:limit].replace("\r", "\\r").replace("\n", "\\n")
		if len(content) > limit:
			preview += "..."
		return f"CLIPBOARD_CHANGED content={preview!r} length={len(content)}"

	def _monitor(self):
		while not self._stop_event.is_set():
			try:
				current = pyperclip.paste()
			except Exception as error:
				self._write_log(f"CLIPBOARD_READ_ERROR type={type(error).__name__}")
				self._stop_event.wait(float(self.config["poll_seconds"]))
				continue

			if current != self._last_clipboard:
				self._write_log(self._describe_clipboard(current))
				self._last_clipboard = current
			self._stop_event.wait(float(self.config["poll_seconds"]))

	def start(self):
		if not self._bool("enabled"):
			print("[AVISO] El monitor del portapapeles esta desactivado en config.txt.")
			return False
		if self.monitoring:
			return False
		try:
			self._last_clipboard = pyperclip.paste()
		except Exception as error:
			self._write_log(f"CLIPBOARD_READ_ERROR type={type(error).__name__}")
			return False
		self._stop_event.clear()
		self.monitoring = True
		self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
		self._monitor_thread.start()
		print("[OK] Monitor del portapapeles iniciado.")
		return True

	def stop(self):
		if not self.monitoring:
			return False
		self._stop_event.set()
		self.monitoring = False
		if self._monitor_thread is not None:
			self._monitor_thread.join(timeout=1.0)
			self._monitor_thread = None
		print("[OK] Monitor del portapapeles detenido.")
		return True

	def run(self):
		if not self.start():
			return
		try:
			while self.monitoring:
				time.sleep(0.5)
		except KeyboardInterrupt:
			self.stop()


if __name__ == "__main__":
	Paperclip().run()
    