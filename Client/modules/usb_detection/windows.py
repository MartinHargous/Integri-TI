import os
import sys
import time
import threading
import psutil

if sys.platform.startswith('win'):
    import ctypes
    from ctypes import wintypes
    import win32file
    import win32con
    import win32process
    import win32api
    import win32com.client
    import pythoncom


class WindowsDirectoryWatcher(threading.Thread):
    """Monitorea en tiempo real cambios, creaciones y escrituras en una unidad USB montada (ReadDirectoryChangesW)."""
    def __init__(self, mount_path, device_name, on_alert):
        super().__init__(daemon=True)
        self.mount_path = mount_path
        self.device_name = device_name
        self.on_alert = on_alert
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
                    True,  # Subárbol completo recursivo
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
                    self.on_alert(
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
                ctypes.windll.kernel32.CancelIoEx(ctypes.c_void_p(int(self.h_dir)), None)
            except Exception:
                pass
            try:
                win32file.CloseHandle(self.h_dir)
            except Exception:
                pass


class WindowsShellExplorerWatcher(threading.Thread):
    """Monitorea en tiempo real las ventanas de File Explorer para detectar navegación y selección de archivos en smartphones (MTP)."""
    def __init__(self, device_name, on_alert, user_extensions, poll_seconds=3.0):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.on_alert = on_alert
        self.user_extensions = user_extensions
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.running = True

    def run(self):
        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            last_seen_selections = {}  # (hwnd, folder_title) -> set of filenames
            while self.running:
                try:
                    windows = shell.Windows()
                    count = windows.Count
                    current_keys = set()
                    for i in range(count):
                        try:
                            w = windows.Item(i)
                            hwnd = getattr(w, 'HWND', i)
                            loc = str(getattr(w, 'LocationName', ''))
                            doc = getattr(w, 'Document', None)
                            if not doc:
                                continue

                            folder = getattr(doc, 'Folder', None)
                            folder_title = str(getattr(folder, 'Title', '')) if folder else loc
                            folder_path = ""
                            if folder and hasattr(folder, 'Self'):
                                folder_path = str(getattr(folder.Self, 'Path', ''))

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

                            win_key = (hwnd, folder_title)
                            current_keys.add(win_key)

                            current_files = set()

                            # 1. Detectar archivos seleccionados
                            selected = getattr(doc, 'SelectedItems', None)
                            if selected and callable(selected):
                                try:
                                    for s in selected():
                                        s_name = str(getattr(s, 'Name', '')).replace('\u200e', '').strip()
                                        if s_name and not any(k in s_name.lower() for k in ["almacenamiento interno", "disco"]):
                                            _, ext = os.path.splitext(s_name)
                                            if ext.lower() in self.user_extensions:
                                                current_files.add(s_name)
                                except Exception:
                                    pass

                            # 2. Si no hubo elementos en SelectedItems, revisar FocusedItem
                            if not current_files:
                                focused = getattr(doc, 'FocusedItem', None)
                                if focused:
                                    try:
                                        item_name = str(getattr(focused, 'Name', '')).replace('\u200e', '').strip()
                                        if item_name and not any(k in item_name.lower() for k in ["almacenamiento interno", "disco"]):
                                            _, ext = os.path.splitext(item_name)
                                            if ext.lower() in self.user_extensions:
                                                current_files.add(item_name)
                                    except Exception:
                                        pass

                            prev_files = last_seen_selections.get(win_key, set())
                            new_files = current_files - prev_files

                            for s_name in new_files:
                                self.on_alert(
                                    device_type="Smartphone MTP",
                                    device_name=self.device_name,
                                    file_path=s_name,
                                    action="ARCHIVO SELECCIONADO EN DISPOSITIVO",
                                    process_info=f"Explorador de Windows (Carpeta: {folder_title})"
                                )

                            last_seen_selections[win_key] = current_files
                        except Exception:
                            continue

                    # Limpiar ventanas cerradas para evitar fugas de memoria
                    for k in list(last_seen_selections.keys()):
                        if k not in current_keys:
                            last_seen_selections.pop(k, None)

                except Exception:
                    pass
                step = 0.2
                elapsed = 0.0
                while self.running and elapsed < self.poll_seconds:
                    time.sleep(step)
                    elapsed += step
        finally:
            pythoncom.CoUninitialize()

    def stop(self):
        self.running = False


class WindowsTransferWatcher(threading.Thread):
    """Monitorea carpetas típicas donde los archivos del celular son copiados o extraídos (Escritorio, Descargas, Temp)."""
    def __init__(self, device_name, on_alert, user_extensions):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.on_alert = on_alert
        self.user_extensions = user_extensions
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
            # NUNCA monitorear la raíz de Temp (donde PowerShell, navegadores y el sistema generan archivos temporales constantes)
            # Únicamente monitorear la subcarpeta WPDNSE (Windows Portable Devices Namespace Extension) si existe
            wpdnse = os.path.join(localappdata, 'Temp', 'WPDNSE')
            if os.path.exists(wpdnse):
                target_dirs.append(wpdnse)

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
                    lower_base = base_name.lower()
                    # Ignorar archivos del sistema, scripts de verificación de políticas y archivos temporales del SO
                    if lower_base.startswith(("__psscriptpolicytest", "~$", ".tmp", "temp_", "desktop.ini", "{")):
                        continue
                    _, ext = os.path.splitext(base_name)
                    ext = ext.lower()
                    if ext in self.user_extensions:
                        self.on_alert(
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
                ctypes.windll.kernel32.CancelIoEx(ctypes.c_void_p(int(h)), None)
            except Exception:
                pass
            try:
                win32file.CloseHandle(h)
            except Exception:
                pass


def get_windows_removable_drive():
    """Retorna la letra de una unidad USB extraíble conectada (ej: E:\\)."""
    for partition in psutil.disk_partitions(all=False):
        if 'removable' in partition.opts:
            return partition.mountpoint
    return None


def get_drive_letter_for_usb(vid, pid):
    """Busca si este dispositivo USB específico (VID y PID) tiene una partición de disco con letra de unidad asignada."""
    try:
        import wmi
        c = wmi.WMI()
        vid_hex = f"VID_{str(vid).upper()}"
        pid_hex = f"PID_{str(pid).upper()}"
        for disk in c.Win32_DiskDrive(InterfaceType="USB"):
            pnp = (disk.PNPDeviceID or "").upper()
            if vid_hex in pnp and pid_hex in pnp:
                for partition in disk.associators("Win32_DiskDriveToDiskPartition"):
                    for logical in partition.associators("Win32_LogicalDiskToPartition"):
                        if logical.Caption:
                            return logical.Caption + "\\"
    except Exception:
        pass
    return None


def get_windows_wpd_device_for_vid_pid(vid, pid):
    """Verifica si este dispositivo USB específico (VID y PID) corresponde a un dispositivo WPD/MTP registrado."""
    try:
        import wmi
        c = wmi.WMI()
        vid_hex = f"VID_{str(vid).upper()}"
        pid_hex = f"PID_{str(pid).upper()}"
        for item in c.Win32_PnPEntity(PNPClass="WPD"):
            pnp = (item.PNPDeviceID or "").upper()
            if vid_hex in pnp and pid_hex in pnp:
                return str(item.Name or "")
    except Exception:
        pass
    return None


def get_windows_mtp_device():
    """Verifica si hay un dispositivo MTP presente en Este Equipo."""
    try:
        pythoncom.CoInitialize()
        shell = win32com.client.Dispatch("Shell.Application")
        comp = shell.Namespace(17)
        ignored_names = {
            "red", "papelera de reciclaje", "panel de control", 
            "bibliotecas", "dispositivos e impresoras", "herramientas de windows",
            "este equipo", "escritorio"
        }
        for item in comp.Items():
            is_fs = getattr(item, 'IsFileSystem', True)
            name = str(getattr(item, 'Name', '')).strip()
            path = str(getattr(item, 'Path', '')).lower()
            if not is_fs and item.IsFolder and name and name.lower() not in ignored_names:
                if path.startswith("::{") or "wpd" in path or "usb" in path or not path:
                    return name
    except Exception:
        pass
    finally:
        pythoncom.CoUninitialize()
    return None


def start_windows_watchers(
    dev_type,
    dev_name,
    target_path,
    on_alert_callback,
    user_extensions,
    shell_poll_seconds=3.0
):
    """Inicia y retorna la lista de vigilantes específicos de Windows optimizados según la configuración."""
    watchers = []
    if dev_type == 'pendrive' and target_path:
        print(f"[+] Activando vigilancia On-Access para Pendrive: {target_path} ({dev_name})", flush=True)
        # Watcher 1: ReadDirectoryChangesW (100% basado en eventos del kernel NTFS/FAT, consumo 0% CPU en reposo)
        w1 = WindowsDirectoryWatcher(target_path, dev_name, on_alert_callback)
        w1.start()
        watchers.append(w1)

    elif dev_type == 'smartphone':
        print(f"[+] Activando vigilancia On-Access para Smartphone MTP: {dev_name}", flush=True)
        # Watcher 1: Explorador de Archivos Shell COM (Intervalo relajado a 3s para bajo consumo)
        w1 = WindowsShellExplorerWatcher(dev_name, on_alert_callback, user_extensions, poll_seconds=shell_poll_seconds)
        w1.start()
        watchers.append(w1)

        # Watcher 2: Transferencias y Copia a Escritorio / Descargas (Basado en eventos del kernel, 0% CPU en reposo)
        w2 = WindowsTransferWatcher(dev_name, on_alert_callback, user_extensions)
        w2.start()
        watchers.append(w2)

    return watchers

