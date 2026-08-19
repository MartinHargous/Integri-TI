import time
import pyperclip

def monitorear_portapapeles():
    print("Iniciando monitor de portapapeles...")
    print("Copia cualquier texto en tu PC. Presiona Ctrl+C para detener.\n")
    
    # Guardamos el estado inicial del portapapeles para poder comparar
    estado_anterior = pyperclip.paste()

    try:
        while True:
            # Leemos el estado actual del portapapeles
            estado_actual = pyperclip.paste()
            
            # Comparamos: Si es distinto, significa que el usuario acaba de copiar algo nuevo
            if estado_actual != estado_anterior:
                timestamp = time.strftime("%H:%M:%S")
                
                # Opcional: Recortar el texto si es muy largo para no inundar la terminal
                texto_mostrar = estado_actual
                if len(texto_mostrar) > 100:
                    texto_mostrar = estado_actual[:100] + "... [TEXTO LARGO RECORTADO]"
                    
                print(f"[{timestamp}] NUEVO COPIADO DETECTADO:")
                print(f"Contenido: '{texto_mostrar}'\n")
                
                # Actualizamos la memoria
                estado_anterior = estado_actual
            
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nMonitor de portapapeles detenido.")

if __name__ == "__main__":
    monitorear_portapapeles()

    