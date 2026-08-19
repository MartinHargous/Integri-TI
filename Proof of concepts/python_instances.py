import psutil

def buscar_procesos_python():
    print("Buscando procesos de Python en ejecución...\n")
    print(f"{'PID':<10} | {'Nombre':<15} | {'Comando / Script'}")
    print("-" * 70)
    
    for proceso in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            nombre = proceso.info['name']
            
            if nombre and 'python' in nombre.lower():
                pid = proceso.info['pid']
                
                cmdline = proceso.info['cmdline']
                comando = " ".join(cmdline) if cmdline else "Desconocido"
                
                print(f"{pid:<10} | {nombre:<15} | {comando}")
                
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            
            pass

if __name__ == "__main__":
    buscar_procesos_python()