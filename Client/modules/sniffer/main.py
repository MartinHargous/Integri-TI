import os
import re
from pathlib import Path
import sys
import ctypes
import datetime
from scapy.all import sniff, TCP, UDP, DNS, DNSQR, Raw, load_layer
import time
load_layer("tls")
from scapy.layers.tls.handshake import TLSClientHello
from scapy.layers.tls.extensions import TLS_Ext_ServerName

import tldextract 
import threading

class Sniffer:
    DEFAULTS = {
        "enabled": "true",
        "log_file": "sniffer.log",
        "method": "regex",  # Puede ser "sni" o "regex"
        "cooldown_seconds": "1",
    }

    def __init__(self):
        self.config = self._read_config()
        self.log_path = self._resolve_path(self.config["log_file"])
        self.os_type = sys.platform
        self.sniffer = None
        self.cooldown = float(self.config.get("cooldown_seconds", 1))
        self.last_seen = {}
    def _read_config(self):
        values = self.DEFAULTS.copy()
        config_path = Path(__file__).with_name("config.txt")
        if config_path.exists():
            for raw_line in config_path.read_text(encoding="utf-8-sig").splitlines():
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
            path = Path(__file__).parent / path
        return path.resolve()

    def is_admin(self):
        if self.os_type == "win32":
            try:
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                return False
        elif self.os_type in ("linux", "darwin"):
            return os.geteuid() == 0        

    def clean_and_validate_domain(self, raw_domain):
        raw_domain = raw_domain.lower().strip()
        
        if len(raw_domain) <= 5:
            return None

        try:
            extracted = tldextract.extract(raw_domain)
            if extracted.domain and extracted.suffix:
                clean_domain = f"{extracted.domain}.{extracted.suffix}"
                if extracted.subdomain:
                    clean_domain = f"{extracted.subdomain}.{clean_domain}"
                
                # Filtro de telemetría basura
                if not any(x in clean_domain for x in ["microsoft", "telemetry", "clouddatahub", "windows"]):
                    return clean_domain
        except Exception:
            pass
            
        return None

    def extract_domains_regex(self, payload):
        domain_regex = re.compile(b'(?:[a-zA-Z0-9-]+\\.)+[a-zA-Z]{2,10}')
        matches = domain_regex.findall(payload)
        
        valid_domains = []
        for match in matches:
            try:
                raw_domain = match.decode('utf-8', errors='ignore')
                clean = self.clean_and_validate_domain(raw_domain)
                if clean and clean not in valid_domains:
                    valid_domains.append(clean)
            except Exception:
                pass
        return valid_domains

    def process_packet(self, packet):
        method = self.config.get("method", "sni").lower()
        domain_found = None

        if packet.haslayer(DNS) and packet.haslayer(DNSQR):
            try:
                qname = packet[DNSQR].qname.decode('utf-8', errors='ignore').rstrip('.')
                domain_found = qname
            except Exception:
                pass
            
            if domain_found:
                clean_domain = self.clean_and_validate_domain(domain_found)
                if clean_domain:
                    self.write_log(clean_domain)
                return

        if method == "regex":
            if packet.haslayer(Raw):
                payload = packet[Raw].load
                domains = self.extract_domains_regex(payload)
                for domain in domains:
                    self.write_log(domain)

        elif method == "sni":
            if packet.haslayer(TLSClientHello) and packet.haslayer(TLS_Ext_ServerName):
                try:
                    for servername in packet[TLS_Ext_ServerName].servernames:
                        domain_found = servername.servername.decode('utf-8', errors='ignore')
                        break
                except Exception:
                    pass

                if domain_found:
                    clean_domain = self.clean_and_validate_domain(domain_found)
                    if clean_domain:
                        self.write_log(clean_domain)

    def write_log(self, domain):
        now = time.time()
        
        if domain in self.last_seen:
            if now - self.last_seen[domain] < self.cooldown:
                return 
        self.last_seen[domain] = now
        
        if len(self.last_seen) > 1000:
            self.last_seen = {k: v for k, v in self.last_seen.items() if now - v < self.cooldown}

        with open(self.log_path, "a", encoding="utf-8") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"[{timestamp}] {domain}\n")

    def start_sniffing(self):
        if not self._bool("enabled"):
            print("[AVISO] El sniffer está desactivado en config.txt.")
            return False
        if not self.is_admin():
            print("[ERROR] Se requieren privilegios de administrador.")
            return False
        
        method = self.config.get("method", "sni").upper()
        print(f"[INFO] Sniffer iniciado. Método activo: {method}")
        print(f"[INFO] Escuchando TCP, UDP (QUIC) y DNS. Guardando en: {self.log_path}")
        
        self.sniffer = threading.Thread(
            target=sniff, 
            kwargs={"filter": "port 443 or port 53", "prn": self.process_packet, "store": False}
        )
        self.sniffer.start()

    def stop_sniffing(self):
        if hasattr(self, 'sniffer') and self.sniffer:
            self.sniffer.join()

if __name__ == "__main__":
    sniffer = Sniffer()
    if sniffer.is_admin():
        print("=" * 60)
        print(" Sniffer (TCP + QUIC/UDP + DNS) ")
        print("=" * 60)
        print("[-] Capturando tráfico en tiempo real...\n")
        
        sniffer.start_sniffing()
    else:
        print("[-] Solicitando privilegios de administrador...")
        script_path = f'"{os.path.abspath(__file__)}"'
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, script_path, None, 1)