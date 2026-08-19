import os
import sys
import ctypes
import re
from scapy.all import sniff, Raw, TCP
import tldextract  # Verifica si el sufijo/extensión del dominio es real en internet

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def extract_valid_domains(payload):
    """
    Busca cadenas de texto con forma de dominio y valida si su extensión (.com, .org, etc.) es real.
    """
    # Captura cualquier texto que parezca un dominio (letras, números y puntos)
    domain_regex = re.compile(b'(?:[a-zA-Z0-9-]+\\.)+[a-zA-Z]{2,10}')
    matches = domain_regex.findall(payload)
    
    valid_domains = []
    for match in matches:
        try:
            # Decodificamos y limpiamos caracteres extraños pegados por la encriptación
            raw_domain = match.decode('utf-8', errors='ignore').lower().strip()
            
            # Filtro básico de longitud
            if len(raw_domain) < 5:
                continue

            # tldextract desarma el dominio en: subdominio, dominio y extensión (.com, .net)
            extracted = tldextract.extract(raw_domain)
            
            if extracted.domain and extracted.suffix:
                # Recomponemos el dominio limpio libre de símbolos raros (+, &, ")
                clean_domain = f"{extracted.domain}.{extracted.suffix}"
                if extracted.subdomain:
                    clean_domain = f"{extracted.subdomain}.{clean_domain}"
                
                # Evitamos duplicados y telemetría basura de Windows
                if clean_domain not in valid_domains:
                    if not any(x in clean_domain for x in ["microsoft", "telemetry", "clouddatahub", "windows"]):
                        valid_domains.append(clean_domain)
        except:
            pass
    return valid_domains

def process_packet(packet):
    if packet.haslayer(TCP) and packet.haslayer(Raw):
        payload = packet[Raw].load
        
        # Filtramos pasivamente el tráfico
        domains = extract_valid_domains(payload)
        for domain in domains:
            print(f"[Página Real] -> {domain}")

if __name__ == "__main__":
    if is_admin():
        print("=" * 60)
        print(" Sniffer (Validación TLD) ")
        print("=" * 60)
        print("[-] Capturando tráfico web legítimo en tiempo real...\n")
        print("[*] Abre tu navegador e ingresa a ://google.com o cualquier web.\n")
        
        # Escuchamos el puerto universal seguro (443)
        sniff(filter="tcp port 443", prn=process_packet, store=False)
    else:
        print("[-] No se está ejecutando como administrador. Solicitando privilegios...")
        
        script_path = f'"{os.path.abspath(__file__)}"'
        
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, script_path, None, 1)
