import os
import sys
import time
import datetime
import threading
from threading import Timer, Thread
import re
import psutil
from usbmonitor import USBMonitor

# Configuración de compatibilidad de consola UTF-8
if sys.platform.startswith('win'):
    import ctypes
    from ctypes import wintypes
    import win32file
    import win32con
    import win32process
    import win32api
    import win32com.client
    import pythoncom
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
elif sys.platform.startswith('linux'):
    import ctypes
    import ctypes.util
    import subprocess

# Variables globales y gestores de eventos
active_events = {}
active_watchers = {}
watchers_lock = threading.Lock()
last_alert_times = {}
ALERT_COOLDOWN_SECONDS = 3.0  # Evita alertas repetidas consecutivas para el mismo archivo

# Extensiones de archivos de usuario a vigilar
USER_FILE_EXTENSIONS = {
    # Documentos y libros
    '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.txt', '.rtf', '.odt', '.csv',
    # Imágenes y fotos
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.raw',
    # Audio y Video
    '.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.m4a', '.aac',
    # Archivos comprimidos
    '.zip', '.rar', '.7z', '.tar', '.gz',
    # Código fuente y scripts
    '.py', '.java', '.c', '.cpp', '.cs', '.html', '.css', '.js', '.ts', '.sql', '.sh', '.bat', '.ps1'
}

# Regex optimizada para extraer nombres de archivo desde títulos de ventana
_EXT_REGEX_PART = '|'.join(ext.lstrip('.') for ext in USER_FILE_EXTENSIONS)
FILE_TITLE_REGEX = re.compile(
    r'([a-zA-Z0-9_\-\. ()\[\]]+\.(?:' + _EXT_REGEX_PART + r'))(?:\b|[\s\u200e\-]|$)',
    re.IGNORECASE
)

def log_integrity_alert(device_type, device_name, file_path, action, process_info="Desconocido"):
    """Emite una alerta clara de integridad cuando un estudiante accede a un archivo."""
    key = (str(file_path).lower(), str(action).lower())
    now = time.time()
    
    # Debounce de alertas repetidas para el mismo archivo
    if key in last_alert_times and (now - last_alert_times[key]) < ALERT_COOLDOWN_SECONDS:
        return
    last_alert_times[key] = now

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_file = str(file_path).replace('\u200e', '').strip()
    clean_proc = str(process_info).replace('\u200e', '').strip()

    print("\n" + "=" * 70, flush=True)
    print(f"[ALERTA INTEGRIDAD] Acceso a archivo en dispositivo externo", flush=True)
    print(f"  - Dispositivo: {device_type} ({device_name})", flush=True)
    print(f"  - Archivo:     {clean_file}", flush=True)
    print(f"  - Acción:      {action}", flush=True)
    print(f"  - Proceso:     {clean_proc}", flush=True)
    print(f"  - Fecha/Hora:  {timestamp}", flush=True)
    print("=" * 70 + "\n", flush=True)


# =====================================================================
# VIGILANTES PARA WINDOWS
# =====================================================================

class WindowsDirectoryWatcher(threading.Thread):
    """Monitorea en tiempo real cambios, creaciones y escrituras en una unidad USB montada (ReadDirectoryChangesW)."""
    def __init__(self, mount_path, device_name):
        super().__init__(daemon=True)
        self.mount_path = mount_path
        self.device_name = device_name
        self.running = True
        self.h_dir = None

    def run(self):
        try:
            self.h_dir = win32file.CreateFile(
                self.mount_path,
                win32con.GENERIC_READ,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS,
                None
            )
        except Exception as e:
            print(f"[ERROR] No se pudo abrir manejador para {self.mount_path}: {e}", flush=True)
            return

        action_names = {
            1: "ARCHIVO CREADO / COPIADO AL USB",
            2: "ARCHIVO ELIMINADO",
            3: "ARCHIVO MODIFICADO / ESCRITO",
            4: "ARCHIVO RENOMBRADO (ORIGINAL)",
            5: "ARCHIVO RENOMBRADO (NUEVO)"
        }

        while self.running:
            try:
                results = win32file.ReadDirectoryChangesW(
                    self.h_dir,
                    4096,
                    True,  # Subárbol completo
                    win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
                    win32con.FILE_NOTIFY_CHANGE_DIR_NAME |
                    win32con.FILE_NOTIFY_CHANGE_LAST_WRITE |
                    win32con.FILE_NOTIFY_CHANGE_SIZE,
                    None,
                    None
                )
                for action, filename in results:
                    if "system volume information" in filename.lower() or "$recycle.bin" in filename.lower():
                        continue
                    full_path = os.path.join(self.mount_path, filename)
                    act_desc = action_names.get(action, f"ACCION_{action}")
                    log_integrity_alert(
                        device_type="Pendrive/Disco USB",
                        device_name=self.device_name,
                        file_path=full_path,
                        action=act_desc,
                        process_info="Sistema de Archivos NTFS/FAT"
                    )
            except Exception:
                break

    def stop(self):
        self.running = False
        if self.h_dir:
            try:
                win32file.CloseHandle(self.h_dir)
            except Exception:
                pass


class WindowsOpenHandleWatcher(threading.Thread):
    """Inspecciona procesos de usuario para detectar cuando abren un archivo directamente del USB montado."""
    def __init__(self, mount_path, device_name):
        super().__init__(daemon=True)
        self.mount_path = mount_path.rstrip("\\/").lower()
        self.device_name = device_name
        self.running = True

    def run(self):
        COMMON_APPS = {
            'notepad.exe', 'wordpad.exe', 'winword.exe', 'excel.exe', 'powerpnt.exe',
            'acrobat.exe', 'acrord32.exe', 'code.exe', 'devenv.exe', 'chrome.exe',
            'msedge.exe', 'firefox.exe', 'brave.exe', 'vlc.exe', 'powershell.exe',
            'cmd.exe', 'python.exe', 'explorer.exe'
        }

        while self.running:
            try:
                target_pids = set()
                try:
                    hwnd = ctypes.windll.user32.GetForegroundWindow()
                    if hwnd:
                        fg_pid = ctypes.c_ulong()
                        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(fg_pid))
                        if fg_pid.value:
                            target_pids.add(fg_pid.value)
                except Exception:
                    pass

                for p in psutil.process_iter(['name', 'pid']):
                    try:
                        if p.info['name'] and p.info['name'].lower() in COMMON_APPS:
                            target_pids.add(p.info['pid'])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                for pid in target_pids:
                    try:
                        proc = psutil.Process(pid)
                        proc_name = proc.name()
                        for f in proc.open_files():
                            fpath = f.path
                            if fpath.lower().startswith(self.mount_path):
                                if "system volume information" in fpath.lower() or "$recycle.bin" in fpath.lower():
                                    continue
                                log_integrity_alert(
                                    device_type="Pendrive/Disco USB",
                                    device_name=self.device_name,
                                    file_path=fpath,
                                    action="LECTURA / ARCHIVO ABIERTO",
                                    process_info=f"{proc_name} (PID: {pid})"
                                )
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception:
                pass

            time.sleep(1.0)

    def stop(self):
        self.running = False


class WindowsShellExplorerWatcher(threading.Thread):
    """Monitorea en tiempo real las ventanas de File Explorer para detectar navegación y selección de archivos en smartphones (MTP)."""
    def __init__(self, device_name):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.running = True

    def run(self):
        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            while self.running:
                try:
                    windows = shell.Windows()
                    count = windows.Count
                    for i in range(count):
                        try:
                            w = windows.Item(i)
                            loc = str(getattr(w, 'LocationName', ''))
                            doc = getattr(w, 'Document', None)
                            if not doc:
                                continue

                            folder = getattr(doc, 'Folder', None)
                            folder_title = str(getattr(folder, 'Title', '')) if folder else loc
                            folder_path = ""
                            if folder and hasattr(folder, 'Self'):
                                folder_path = str(getattr(folder.Self, 'Path', ''))

                            # Identificar si la ventana está navegando en el smartphone
                            dev_tokens = [t.lower() for t in self.device_name.split() if len(t) > 2]
                            is_mtp = (
                                folder_path.startswith("::{") or
                                "usb#vid_" in folder_path.lower() or
                                "wpd" in folder_path.lower() or
                                any(t in folder_title.lower() for t in dev_tokens) or
                                any(k in folder_title.lower() for k in ["almacenamiento interno", "internal shared storage", "dcim", "camera"])
                            )

                            if not is_mtp:
                                continue

                            # 1. Detectar el archivo enfocado/clicado por el usuario en el teléfono
                            focused = getattr(doc, 'FocusedItem', None)
                            if focused:
                                item_name = str(getattr(focused, 'Name', '')).replace('\u200e', '').strip()
                                if item_name and not any(k in item_name.lower() for k in ["almacenamiento interno", "disco"]):
                                    _, ext = os.path.splitext(item_name)
                                    if ext.lower() in USER_FILE_EXTENSIONS:
                                        log_integrity_alert(
                                            device_type="Smartphone MTP",
                                            device_name=self.device_name,
                                            file_path=item_name,
                                            action="SELECCIÓN / APERTURA EN EL TELÉFONO",
                                            process_info=f"Explorador de Windows (Carpeta: {folder_title})"
                                        )

                            # 2. Detectar si seleccionó archivos para copiar o abrir
                            selected = getattr(doc, 'SelectedItems', None)
                            if selected and callable(selected):
                                for s in selected():
                                    s_name = str(getattr(s, 'Name', '')).replace('\u200e', '').strip()
                                    _, ext = os.path.splitext(s_name)
                                    if ext.lower() in USER_FILE_EXTENSIONS:
                                        log_integrity_alert(
                                            device_type="Smartphone MTP",
                                            device_name=self.device_name,
                                            file_path=s_name,
                                            action="ARCHIVO SELECCIONADO EN DISPOSITIVO",
                                            process_info=f"Explorador de Windows (Carpeta: {folder_title})"
                                        )
                        except Exception:
                            continue
                except Exception:
                    pass
                time.sleep(0.8)
        finally:
            pythoncom.CoUninitialize()

    def stop(self):
        self.running = False


class WindowsForegroundWindowWatcher(threading.Thread):
    """Monitorea la ventana en primer plano del estudiante para detectar cuando un archivo es visualizado en pantalla."""
    def __init__(self, device_name, device_type):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.device_type = device_type
        self.running = True

    def run(self):
        user32 = ctypes.windll.user32
        last_title = ""

        while self.running:
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value.strip()
                        if title and title != last_title:
                            last_title = title
                            self._analyze_window(hwnd, title)
            except Exception:
                pass
            time.sleep(0.5)

    def _analyze_window(self, hwnd, title):
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = "Desconocido"
        if pid.value:
            try:
                proc_name = psutil.Process(pid.value).name()
            except Exception:
                pass

        # 1. Comprobar si el título contiene un archivo de usuario (ej: Fotos, Adobe Reader, Word, Bloc de Notas)
        m = FILE_TITLE_REGEX.search(title)
        if m:
            filename = m.group(1).strip()
            # Ignorar el propio script en desarrollo
            if "visual studio code" in title.lower() and filename.lower().endswith("usb_detection.py"):
                return
            log_integrity_alert(
                device_type=self.device_type,
                device_name=self.device_name,
                file_path=filename,
                action="LECTURA / ARCHIVO VISUALIZADO EN PANTALLA",
                process_info=f"{proc_name} (Ventana: {title})"
            )
            return

        # 2. Comprobar si el estudiante tiene abierta la ventana del teléfono en el explorador
        lower_title = title.lower()
        dev_tokens = [t.lower() for t in self.device_name.split() if len(t) > 2]
        is_device_window = any(t in lower_title for t in dev_tokens) or \
            any(k in lower_title for k in ["almacenamiento interno", "dcim", "internal storage", "mtp"])
        if is_device_window and "explorer" in proc_name.lower():
            log_integrity_alert(
                device_type=self.device_type,
                device_name=self.device_name,
                file_path=title,
                action="NAVEGACIÓN ACTIVA EN CARPETA DEL DISPOSITIVO",
                process_info=f"{proc_name} (PID: {pid.value})"
            )

    def stop(self):
        self.running = False


class WindowsTransferWatcher(threading.Thread):
    """Monitorea carpetas típicas donde los archivos del celular son copiados o extraídos (Escritorio, Descargas, Temp)."""
    def __init__(self, device_name):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.running = True
        self.handles = []

    def _get_target_dirs(self):
        target_dirs = []
        userprofile = os.environ.get('USERPROFILE') or os.path.expanduser('~')
        if userprofile:
            desktop = os.path.join(userprofile, 'Desktop')
            downloads = os.path.join(userprofile, 'Downloads')
            if os.path.exists(desktop):
                target_dirs.append(desktop)
            if os.path.exists(downloads):
                target_dirs.append(downloads)

        localappdata = os.environ.get('LOCALAPPDATA')
        if localappdata:
            temp_dir = os.path.join(localappdata, 'Temp')
            if os.path.exists(temp_dir):
                target_dirs.append(temp_dir)

            packages = os.path.join(localappdata, 'Packages')
            if os.path.exists(packages):
                try:
                    for p in os.listdir(packages):
                        if any(k in p.lower() for k in ['photo', 'media', 'viewer']):
                            tstate = os.path.join(packages, p, 'TempState')
                            if os.path.exists(tstate):
                                target_dirs.append(tstate)
                except Exception:
                    pass
        return target_dirs

    def run(self):
        dirs = self._get_target_dirs()
        for d in dirs:
            try:
                h = win32file.CreateFile(
                    d,
                    win32con.GENERIC_READ,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    None,
                    win32con.OPEN_EXISTING,
                    win32con.FILE_FLAG_BACKUP_SEMANTICS,
                    None
                )
                self.handles.append((h, d))
            except Exception:
                pass

        for h, d in self.handles:
            t = threading.Thread(target=self._watch_dir, args=(h, d), daemon=True)
            t.start()

        while self.running:
            time.sleep(1.0)

    def _watch_dir(self, h, dir_path):
        while self.running:
            try:
                results = win32file.ReadDirectoryChangesW(
                    h,
                    4096,
                    True,
                    win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
                    win32con.FILE_NOTIFY_CHANGE_LAST_WRITE |
                    win32con.FILE_NOTIFY_CHANGE_SIZE,
                    None,
                    None
                )
                for action, filename in results:
                    base_name = os.path.basename(filename)
                    _, ext = os.path.splitext(base_name)
                    ext = ext.lower()
                    if ext in USER_FILE_EXTENSIONS:
                        log_integrity_alert(
                            device_type="Smartphone MTP",
                            device_name=self.device_name,
                            file_path=base_name,
                            action="ARCHIVO EXTRAÍDO O COPIADO AL EQUIPO",
                            process_info=f"Guardado en: {os.path.join(dir_path, filename)}"
                        )
            except Exception:
                break

    def stop(self):
        self.running = False
        for h, _ in self.handles:
            try:
                win32file.CloseHandle(h)
            except Exception:
                pass


# =====================================================================
# VIGILANTES PARA LINUX (inotify)
# =====================================================================

class LinuxInotifyWatcher(threading.Thread):
    """Monitorea aperturas, accesos y escrituras en Linux usando el subsistema inotify del kernel."""
    def __init__(self, watch_path, device_type, device_name):
        super().__init__(daemon=True)
        self.watch_path = watch_path
        self.device_type = device_type
        self.device_name = device_name
        self.running = True
        self.inotify_fd = None

    def run(self):
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            self.inotify_fd = libc.inotify_init1(0)
            if self.inotify_fd < 0:
                print(f"[ERROR] No se pudo iniciar inotify en Linux", flush=True)
                return

            IN_ACCESS = 0x00000001
            IN_MODIFY = 0x00000002
            IN_CLOSE_WRITE = 0x00000008
            IN_OPEN = 0x00000020
            IN_CREATE = 0x00000100
            IN_DELETE = 0x00000200
            IN_MOVED_TO = 0x00000080

            mask = IN_OPEN | IN_ACCESS | IN_CREATE | IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_TO
            wd = libc.inotify_add_watch(self.inotify_fd, self.watch_path.encode('utf-8'), mask)
            if wd < 0:
                print(f"[ERROR] No se pudo agregar reloj inotify a: {self.watch_path}", flush=True)
                return

            print(f"    [*] Vigilante inotify activo en: {self.watch_path}", flush=True)
            
            buf_size = 4096
            while self.running:
                buf = os.read(self.inotify_fd, buf_size)
                if not buf:
                    break
                offset = 0
                while offset < len(buf):
                    import struct
                    wd, ev_mask, cookie, length = struct.unpack_from("iIII", buf, offset)
                    name_bytes = buf[offset + 16: offset + 16 + length]
                    filename = name_bytes.split(b"\x00", 1)[0].decode('utf-8', errors='ignore')
                    offset += 16 + length

                    if not filename:
                        continue

                    action = "ACCESO"
                    if ev_mask & IN_OPEN or ev_mask & IN_ACCESS:
                        action = "LECTURA / ARCHIVO ABIERTO"
                    elif ev_mask & IN_CREATE or ev_mask & IN_MOVED_TO or ev_mask & IN_CLOSE_WRITE:
                        action = "ARCHIVO CREADO / COPIADO"
                    elif ev_mask & IN_DELETE:
                        action = "ARCHIVO ELIMINADO"

                    full_path = os.path.join(self.watch_path, filename)
                    log_integrity_alert(
                        device_type=self.device_type,
                        device_name=self.device_name,
                        file_path=full_path,
                        action=action,
                        process_info="Kernel Linux (inotify)"
                    )
        except Exception as e:
            print(f"[ERROR] Excepción en inotify Linux: {e}", flush=True)

    def stop(self):
        self.running = False
        if self.inotify_fd:
            try:
                os.close(self.inotify_fd)
            except Exception:
                pass


# =====================================================================
# GESTIÓN DE DISPOSITIVOS Y ASIGNACIÓN DE VIGILANTES
# =====================================================================

def start_watchers_for_device(dev_key, dev_type, dev_name, target_path=None):
    """Inicia los observadores correspondientes según el tipo de dispositivo y sistema operativo."""
    with watchers_lock:
        if dev_key in active_watchers:
            return

        watchers = []
        if dev_type == 'pendrive':
            if sys.platform.startswith('win') and target_path:
                print(f"[+] Activando vigilancia On-Access para Pendrive: {target_path} ({dev_name})", flush=True)
                w1 = WindowsDirectoryWatcher(target_path, dev_name)
                w2 = WindowsOpenHandleWatcher(target_path, dev_name)
                w3 = WindowsForegroundWindowWatcher(dev_name, "Pendrive/Disco USB")
                w1.start()
                w2.start()
                w3.start()
                watchers.extend([w1, w2, w3])
            elif sys.platform.startswith('linux') and target_path:
                print(f"[+] Activando vigilancia inotify para Pendrive: {target_path} ({dev_name})", flush=True)
                w = LinuxInotifyWatcher(target_path, "Pendrive/Disco USB", dev_name)
                w.start()
                watchers.append(w)

        elif dev_type == 'smartphone':
            if sys.platform.startswith('win'):
                print(f"[+] Activando vigilancia On-Access para Smartphone MTP: {dev_name}", flush=True)
                print(f"    - Vigilante 1: Explorador de Archivos (Shell COM)", flush=True)
                print(f"    - Vigilante 2: Ventana en Primer Plano (Fotos, Lectores PDF, Documentos)", flush=True)
                print(f"    - Vigilante 3: Extracción y Copia (Escritorio, Descargas y Caché)", flush=True)
                w1 = WindowsShellExplorerWatcher(dev_name)
                w2 = WindowsForegroundWindowWatcher(dev_name, "Smartphone MTP")
                w3 = WindowsTransferWatcher(dev_name)
                w1.start()
                w2.start()
                w3.start()
                watchers.extend([w1, w2, w3])
            elif sys.platform.startswith('linux'):
                gvfs_path = find_linux_mtp_mount()
                if gvfs_path:
                    print(f"[+] Activando vigilancia inotify para Smartphone MTP en: {gvfs_path}", flush=True)
                    w = LinuxInotifyWatcher(gvfs_path, "Smartphone MTP", dev_name)
                    w.start()
                    watchers.append(w)

        active_watchers[dev_key] = watchers


def stop_watchers_for_device(dev_key):
    """Detiene y remueve los vigilantes asociados a un dispositivo desconectado."""
    with watchers_lock:
        if dev_key in active_watchers:
            print(f"[-] Deteniendo vigilancia para dispositivo: {dev_key}", flush=True)
            for w in active_watchers[dev_key]:
                try:
                    w.stop()
                except Exception:
                    pass
            del active_watchers[dev_key]


def find_linux_mtp_mount():
    """Busca las rutas dinámicas donde Linux (GVFS/jmtpfs) monta los smartphones."""
    user_id = os.getuid() if hasattr(os, 'getuid') else 1000
    gvfs_path = f"/run/user/{user_id}/gvfs"
    if os.path.exists(gvfs_path):
        subdirs = [os.path.join(gvfs_path, d) for d in os.listdir(gvfs_path) if d.startswith("mtp:")]
        if subdirs:
            return subdirs[0]
    return None


def get_windows_removable_drive():
    """Retorna la letra de una unidad USB extraíble conectada (ej: E:\\)."""
    for partition in psutil.disk_partitions(all=False):
        if 'removable' in partition.opts:
            return partition.mountpoint
    return None


def get_windows_mtp_device():
    """Verifica si hay un dispositivo MTP presente en Este Equipo."""
    try:
        pythoncom.CoInitialize()
        shell = win32com.client.Dispatch("Shell.Application")
        comp = shell.Namespace(17)
        for item in comp.Items():
            is_fs = getattr(item, 'IsFileSystem', True)
            name = getattr(item, 'Name', '')
            if not is_fs and item.IsFolder and name not in ["Red", "Papelera de reciclaje", "Panel de control"]:
                return name
    except Exception:
        pass
    finally:
        pythoncom.CoUninitialize()
    return None


def process_debounced_event(hardware_signature, event_type, device_info):
    """Procesa la conexión o desconexión física de un dispositivo USB."""
    vid, pid = hardware_signature
    name = device_info.get('ID_MODEL', 'Dispositivo USB')
    dev_key = f"{vid}:{pid}"

    if event_type == 'connect':
        print(f"\n[+] USB FÍSICO CONECTADO -> VID: {vid} | PID: {pid} ({name})", flush=True)
        time.sleep(1.0)  # Margen para que el sistema registre la unidad o dispositivo

        if sys.platform.startswith('win'):
            # 1. Comprobar si es un pendrive con letra de unidad
            mount_point = get_windows_removable_drive()
            if mount_point:
                start_watchers_for_device(dev_key, 'pendrive', name, mount_point)
            else:
                # 2. Si no tiene letra, registrar como Smartphone MTP
                mtp_name = get_windows_mtp_device() or name
                start_watchers_for_device(dev_key, 'smartphone', mtp_name)

        elif sys.platform.startswith('linux'):
            # 1. Comprobar si es Smartphone vía GVFS
            gvfs_mtp = find_linux_mtp_mount()
            if gvfs_mtp:
                start_watchers_for_device(dev_key, 'smartphone', name, gvfs_mtp)
            else:
                # 2. Buscar punto de montaje en /media o /mnt
                base_paths = ['/media', '/mnt', f'/run/media/{os.environ.get("USER", "")}']
                mount_point = None
                for base in base_paths:
                    if os.path.exists(base):
                        for root, dirs, files in os.walk(base):
                            if root != base:
                                mount_point = root
                                break
                    if mount_point:
                        break
                if mount_point:
                    start_watchers_for_device(dev_key, 'pendrive', name, mount_point)

    elif event_type == 'disconnect':
        print(f"\n[-] USB FÍSICO DESCONECTADO -> VID: {vid} | PID: {pid}", flush=True)
        stop_watchers_for_device(dev_key)

    if hardware_signature in active_events:
        del active_events[hardware_signature]


def route_event(device_info, event_type):
    """Filtra y agrupa las múltiples señales lógicas del hardware."""
    vid = device_info.get('ID_VENDOR_ID')
    pid = device_info.get('ID_MODEL_ID')
    if not vid or not pid:
        return

    hardware_signature = (vid, pid)
    if hardware_signature in active_events:
        active_events[hardware_signature]['timer'].cancel()

    t = Timer(1.5, process_debounced_event, args=[hardware_signature, event_type, device_info])
    active_events[hardware_signature] = {
        'type': event_type,
        'device_info': device_info,
        'timer': t
    }
    t.start()


def check_existing_devices():
    """Comprueba si ya hay unidades de almacenamiento o smartphones conectados al iniciar."""
    if sys.platform.startswith('win'):
        # 1. Revisar pendrives ya montados
        removable = get_windows_removable_drive()
        if removable:
            print(f"[*] Pendrive ya conectado detectado en: {removable}", flush=True)
            start_watchers_for_device("initial_pendrive", 'pendrive', "Pendrive Extraíble", removable)

        # 2. Revisar smartphones MTP ya conectados
        mtp_device = get_windows_mtp_device()
        if mtp_device:
            print(f"[*] Smartphone MTP ya conectado detectado: {mtp_device}", flush=True)
            start_watchers_for_device("initial_smartphone", 'smartphone', mtp_device)

    elif sys.platform.startswith('linux'):
        # 1. Revisar MTP en GVFS
        gvfs_path = find_linux_mtp_mount()
        if gvfs_path:
            print(f"[*] Smartphone MTP ya conectado en: {gvfs_path}", flush=True)
            start_watchers_for_device("initial_smartphone", 'smartphone', "Smartphone MTP", gvfs_path)


def main():
    monitor = USBMonitor()
    print("=" * 70, flush=True)
    print("--- Guardián de Integridad USB Iniciado (Presione Ctrl+C para salir) ---", flush=True)
    print("Modo: On-Access (Alertas en tiempo real al abrir, leer o copiar archivos)", flush=True)
    print("=" * 70, flush=True)

    check_existing_devices()

    print("\n[*] Monitoreando conexiones USB físicas...", flush=True)
    monitor.start_monitoring(
        on_connect=lambda dev_id, dev_info: route_event(dev_info, 'connect'),
        on_disconnect=lambda dev_id, dev_info: route_event(dev_info, 'disconnect')
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nApagando guardián USB...", flush=True)
        for event in active_events.values():
            event['timer'].cancel()
        monitor.stop_monitoring()
        with watchers_lock:
            for dev_key, watchers in active_watchers.items():
                for w in watchers:
                    w.stop()
        print("[OK] Guardián detenido limpiamente.", flush=True)


if __name__ == "__main__":
    main()
