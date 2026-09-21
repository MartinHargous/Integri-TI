import os
import sys
import time
import threading
import psutil

if sys.platform.startswith('linux'):
    import ctypes
    import ctypes.util
    import subprocess


class LinuxInotifyWatcher(threading.Thread):
    """Monitorea aperturas, accesos y escrituras en Linux recursivamente usando el subsistema inotify del kernel."""
    def __init__(self, watch_path, device_type, device_name, on_alert):
        super().__init__(daemon=True)
        self.watch_path = os.path.abspath(watch_path)
        self.device_type = device_type
        self.device_name = device_name
        self.on_alert = on_alert
        self.running = True
        self.inotify_fd = None
        self.wd_to_path = {}
        self.libc = None

    def _add_watch_single(self, path):
        if not os.path.isdir(path):
            return
        try:
            IN_ACCESS = 0x00000001
            IN_MODIFY = 0x00000002
            IN_CLOSE_WRITE = 0x00000008
            IN_OPEN = 0x00000020
            IN_CREATE = 0x00000100
            IN_DELETE = 0x00000200
            IN_MOVED_TO = 0x00000080
            mask = IN_OPEN | IN_ACCESS | IN_CREATE | IN_MODIFY | IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_TO
            wd = self.libc.inotify_add_watch(self.inotify_fd, path.encode('utf-8'), mask)
            if wd >= 0:
                self.wd_to_path[wd] = path
        except Exception:
            pass

    def _add_watch_recursive(self, base_path):
        count = 0
        for root, dirs, files in os.walk(base_path):
            if any(ignore in root for ignore in [".Trash", "$RECYCLE.BIN", ".git"]):
                continue
            self._add_watch_single(root)
            count += 1
        return count

    def run(self):
        try:
            self.libc = ctypes.CDLL(ctypes.util.find_library("c"))
            self.inotify_fd = self.libc.inotify_init1(0)
            if self.inotify_fd < 0:
                print(f"[ERROR] No se pudo iniciar inotify en Linux", flush=True)
                return

            total_dirs = self._add_watch_recursive(self.watch_path)
            print(f"    [*] Vigilante inotify recursivo activo en {total_dirs} carpetas de: {self.watch_path}", flush=True)

            IN_ACCESS = 0x00000001
            IN_MODIFY = 0x00000002
            IN_CLOSE_WRITE = 0x00000008
            IN_OPEN = 0x00000020
            IN_CREATE = 0x00000100
            IN_DELETE = 0x00000200
            IN_MOVED_TO = 0x00000080
            IN_ISDIR = 0x40000000

            buf_size = 8192
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

                    # Ignorar archivos temporales y basura
                    if filename.startswith('.') or filename.endswith('.tmp'):
                        continue

                    dir_path = self.wd_to_path.get(wd, self.watch_path)
                    full_path = os.path.join(dir_path, filename)

                    # Si se crea o mueve un subdirectorio nuevo, agregarlo recursivamente a inotify
                    if (ev_mask & IN_ISDIR) and (ev_mask & (IN_CREATE | IN_MOVED_TO)):
                        self._add_watch_recursive(full_path)
                        continue

                    if ev_mask & IN_ISDIR:
                        continue

                    action = "ACCESO"
                    if ev_mask & IN_OPEN or ev_mask & IN_ACCESS:
                        action = "LECTURA / ARCHIVO ABIERTO"
                    elif ev_mask & IN_CREATE or ev_mask & IN_MOVED_TO:
                        action = "ARCHIVO CREADO / COPIADO"
                    elif ev_mask & IN_CLOSE_WRITE or ev_mask & IN_MODIFY:
                        action = "ARCHIVO MODIFICADO / GUARDADO"
                    elif ev_mask & IN_DELETE:
                        action = "ARCHIVO ELIMINADO"

                    self.on_alert(
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

def get_linux_mount_points():
    """Retorna un conjunto con todos los puntos de montaje actuales en Linux."""
    mounts = set()
    if os.path.exists('/proc/mounts'):
        try:
            with open('/proc/mounts', 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        mounts.add(parts[1])
        except Exception:
            pass
    try:
        for p in psutil.disk_partitions(all=True):
            mounts.add(p.mountpoint)
    except Exception:
        pass
    return mounts


def find_linux_usb_mount(initial_mounts=None, timeout=3.5):
    """Busca el punto de montaje real de una unidad USB en Linux (/media/user/..., /run/media/user/..., /mnt/...)."""
    start_time = time.time()
    valid_prefixes = ('/media/', '/run/media/', '/mnt/')

    while time.time() - start_time < timeout:
        current_mounts = get_linux_mount_points()
        if initial_mounts is not None:
            new_mounts = [m for m in current_mounts if m not in initial_mounts and m.startswith(valid_prefixes)]
            if new_mounts:
                candidate_mounts = [
                    m for m in new_mounts
                    if len([p for p in m.split('/') if p]) >= 3 or m.startswith('/mnt/')
                ]
                if candidate_mounts:
                    return max(candidate_mounts, key=len)
                return max(new_mounts, key=len)

        for m in sorted(current_mounts, key=len, reverse=True):
            if m.startswith(valid_prefixes):
                parts = [p for p in m.split('/') if p]
                if len(parts) >= 3 or m.startswith('/mnt/'):
                    return m

        time.sleep(0.3)
    return None


def find_linux_mtp_mount():
    """Busca las rutas dinámicas donde Linux (GVFS/jmtpfs) monta los smartphones."""
    user_id = os.getuid() if hasattr(os, 'getuid') else 1000
    gvfs_path = f"/run/user/{user_id}/gvfs"
    if os.path.exists(gvfs_path):
        subdirs = [os.path.join(gvfs_path, d) for d in os.listdir(gvfs_path) if d.startswith("mtp:")]
        if subdirs:
            return subdirs[0]
    return None


def start_linux_watchers(
    dev_type,
    dev_name,
    target_path,
    on_alert_callback
):
    """Inicia y retorna la lista de vigilantes específicos de Linux optimizados según la configuración."""
    watchers = []
    if dev_type == 'pendrive' and target_path:
        print(f"[+] Activando vigilancia On-Access para Pendrive en Linux: {target_path} ({dev_name})", flush=True)
        # Watcher 1: Kernel inotify recursivo (Basado 100% en eventos del kernel, 0% CPU en reposo)
        w1 = LinuxInotifyWatcher(target_path, "Pendrive/Disco USB", dev_name, on_alert_callback)
        w1.start()
        watchers.append(w1)


    elif dev_type == 'smartphone':
        gvfs_path = find_linux_mtp_mount()
        if gvfs_path:
            print(f"[+] Activando vigilancia para Smartphone MTP en Linux: {gvfs_path} ({dev_name})", flush=True)
            w1 = LinuxInotifyWatcher(gvfs_path, "Smartphone MTP", dev_name, on_alert_callback)
            w1.start()
            watchers.append(w1)

    return watchers

