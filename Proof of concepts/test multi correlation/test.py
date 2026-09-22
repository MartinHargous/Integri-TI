import re

def limpiar_keylogger_avanzado(log_crudo):
    """
    Reconstruye el texto simulando la posición exacta del cursor,
    soportando navegación con flechas y borrado dinámico.
    """
    texto_final = []
    cursor = 0  # Rastrea dónde está parpadeando la barra de escritura
    
    # Separamos el texto crudo aislando las etiquetas [ALGO]
    partes = re.split(r'(\[.*?\])', log_crudo)
    
    for parte in partes:
        if not parte:
            continue
            
        # Normalizamos a mayúsculas para evitar problemas con [left] o [LEFT]
        parte_upper = parte.upper()
        
        # 1. BORRAR HACIA ATRÁS
        # (Incluí [BASKSPACE] porque en tu log de ejemplo tenía ese error de tipeo)
        if parte_upper in ('[BACKSPACE]', '[BASKSPACE]'):
            if cursor > 0:
                cursor -= 1
                texto_final.pop(cursor)
                
        # 2. NAVEGAR A LA IZQUIERDA
        elif parte_upper == '[LEFT]':
            if cursor > 0:
                cursor -= 1
                
        # 3. NAVEGAR A LA DERECHA
        elif parte_upper == '[RIGHT]':
            if cursor < len(texto_final):
                cursor += 1
                
        # 4. SALTO DE LÍNEA
        elif parte_upper == '[ENTER]':
            texto_final.insert(cursor, '\n')
            cursor += 1
                
        # 5. OTRAS TECLAS ESPECIALES (Ignorarlas)
        elif parte.startswith('[') and parte.endswith(']'):
            # Aquí caen [CTRL], [SHIFT], [TAB], etc.
            pass
            
        # 6. TEXTO NORMAL
        else:
            # Insertamos letra por letra en la posición actual del cursor
            for char in parte:
                texto_final.insert(cursor, char)
                cursor += 1
                
    return "".join(texto_final)

# === PRUEBA DE CONCEPTO EXTREMA ===

# El alumno intenta escribir "import requests"
# Escribe "imprt", se da cuenta que falta la 'o', va a la izquierda, la agrega, 
# vuelve a la derecha, escribe " reuq", borra "uq" y escribe "quests"

log_caotico = "imprt[LEFT][LEFT]o[RIGHT][RIGHT] reuq[BACKSPACE][BACKSPACE]quests[ENTER]"

texto_reconstruido = limpiar_keylogger_avanzado(log_caotico)

print("Log Crudo:")
print(log_caotico)
print("-" * 40)
print("Texto Final Reconstruido:")
print(repr(texto_reconstruido))  # Usamos repr() para que se vea el \n del [ENTER]