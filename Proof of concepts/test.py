import re

linea = "[2026-08-25T16:09:49] Este es el texto que quiero guardar aparte"

# Ponemos paréntesis ( ) alrededor de la fecha y alrededor del resto del texto
patron = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]\s*(.*)$")

coincidencia = patron.match(linea)

if coincidencia:
    # group(1) es lo que capturó el primer paréntesis (la fecha)
    fecha = coincidencia.group(1) 
    
    # group(2) es lo que capturó el segundo paréntesis (el texto)
    texto = coincidencia.group(2) 
    
    print(f"Variable fecha: {fecha}")
    print(f"Variable texto: {texto}")
else:
    print("La línea no tiene el formato esperado.")