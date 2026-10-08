"""vfs — wirtualny dysk tylko do odczytu (WinFsp) do grania od razu, w trakcie pobierania.

Gra pobierana z NAS-a (zdalnie, przez Tailscale) pojawia się na wirtualnym dysku
jako zwykłe pliki: `X:\\<id gry>\\<ścieżka jak na NAS-ie>`. Emulator czyta je od
razu; fragmenty już pobrane idą z pliku lokalnego, brakujące pobierane są na
żądanie, przed resztą kolejki (emustart/partial.py). Pobieranie w tle trwa dalej,
a następnym razem gra startuje już z pamięci podręcznej.

WinFsp (https://winfsp.dev) musi być zainstalowany — bez niego `available()`
zwraca False i EmuStart działa jak dotąd (najpierw pobranie albo „Graj teraz”
wprost z NAS-a). Wiązanie przez ctypes do winfsp-x64.dll (natywne API).
"""

from __future__ import annotations

import ctypes
import logging
import os
import string
import threading
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("emustart.vfs")

# ── NTSTATUS ──
OK = 0
STATUS_END_OF_FILE = 0xC0000011
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_BUFFER_OVERFLOW = 0x80000005
STATUS_IO_DEVICE_ERROR = 0xC0000185
STATUS_NOT_A_DIRECTORY = 0xC0000103
STATUS_INVALID_DEVICE_REQUEST = 0xC0000010
STATUS_MEDIA_WRITE_PROTECTED = 0xC00000A2

FILE_ATTRIBUTE_READONLY = 0x01
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_DIRECTORY_FILE = 0x00000001

NTSTATUS = ctypes.c_long


def _st(code: int) -> int:
    return ctypes.c_long(code & 0xFFFFFFFF).value


# ── struktury (inc/winfsp/fsctl.h; rozmiary sprawdzane niżej) ──
class VOLUME_PARAMS(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint16), ("SectorSize", ctypes.c_uint16),
        ("SectorsPerAllocationUnit", ctypes.c_uint16), ("MaxComponentLength", ctypes.c_uint16),
        ("VolumeCreationTime", ctypes.c_uint64), ("VolumeSerialNumber", ctypes.c_uint32),
        ("TransactTimeout", ctypes.c_uint32), ("IrpTimeout", ctypes.c_uint32),
        ("IrpCapacity", ctypes.c_uint32), ("FileInfoTimeout", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("Prefix", ctypes.c_wchar * 192), ("FileSystemName", ctypes.c_wchar * 16),
        ("Flags2", ctypes.c_uint32), ("VolumeInfoTimeout", ctypes.c_uint32),
        ("DirInfoTimeout", ctypes.c_uint32), ("SecurityTimeout", ctypes.c_uint32),
        ("StreamInfoTimeout", ctypes.c_uint32), ("EaTimeout", ctypes.c_uint32),
        ("FsextControlCode", ctypes.c_uint32), ("Reserved32", ctypes.c_uint32 * 1),
        ("Reserved64", ctypes.c_uint64 * 2),
    ]


# bity pola Flags (kolejność z fsctl.h, od najmłodszego)
F_CASE_PRESERVED = 1 << 1
F_UNICODE_ON_DISK = 1 << 2
F_PERSISTENT_ACLS = 1 << 3
F_READ_ONLY = 1 << 9
F_CONTEXT_PER_OPEN = 1 << 16      # UmFileContextIsUserContext2: osobny kontekst dla każdego otwarcia


class VOLUME_INFO(ctypes.Structure):
    _fields_ = [("TotalSize", ctypes.c_uint64), ("FreeSize", ctypes.c_uint64),
                ("VolumeLabelLength", ctypes.c_uint16), ("VolumeLabel", ctypes.c_wchar * 32)]


class FILE_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32),
                ("AllocationSize", ctypes.c_uint64), ("FileSize", ctypes.c_uint64),
                ("CreationTime", ctypes.c_uint64), ("LastAccessTime", ctypes.c_uint64),
                ("LastWriteTime", ctypes.c_uint64), ("ChangeTime", ctypes.c_uint64),
                ("IndexNumber", ctypes.c_uint64), ("HardLinks", ctypes.c_uint32),
                ("EaSize", ctypes.c_uint32)]


class DIR_INFO(ctypes.Structure):
    _fields_ = [("Size", ctypes.c_uint16), ("_pad", ctypes.c_uint8 * 6), ("FileInfo", FILE_INFO),
                ("Padding", ctypes.c_uint8 * 24)]


assert ctypes.sizeof(VOLUME_PARAMS) == 504
assert ctypes.sizeof(VOLUME_INFO) == 88
assert ctypes.sizeof(FILE_INFO) == 72
assert ctypes.sizeof(DIR_INFO) == 104

PVOID = ctypes.c_void_p
FS = ctypes.c_void_p
_GetVolumeInfo = ctypes.WINFUNCTYPE(NTSTATUS, FS, ctypes.POINTER(VOLUME_INFO))
_GetSecurityByName = ctypes.WINFUNCTYPE(NTSTATUS, FS, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32),
                                        PVOID, ctypes.POINTER(ctypes.c_size_t))
_Open = ctypes.WINFUNCTYPE(NTSTATUS, FS, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.POINTER(PVOID), ctypes.POINTER(FILE_INFO))
_Close = ctypes.WINFUNCTYPE(None, FS, PVOID)
# Create i Overwrite są wymagane przez WinFsp nawet na dysku tylko do odczytu
_Create = ctypes.WINFUNCTYPE(NTSTATUS, FS, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                             PVOID, ctypes.c_uint64, ctypes.POINTER(PVOID), ctypes.POINTER(FILE_INFO))
_Overwrite = ctypes.WINFUNCTYPE(NTSTATUS, FS, PVOID, ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_uint64,
                                ctypes.POINTER(FILE_INFO))
_Read = ctypes.WINFUNCTYPE(NTSTATUS, FS, PVOID, PVOID, ctypes.c_uint64, ctypes.c_ulong,
                           ctypes.POINTER(ctypes.c_ulong))
_GetFileInfo = ctypes.WINFUNCTYPE(NTSTATUS, FS, PVOID, ctypes.POINTER(FILE_INFO))
_ReadDirectory = ctypes.WINFUNCTYPE(NTSTATUS, FS, PVOID, ctypes.c_wchar_p, ctypes.c_wchar_p, PVOID,
                                    ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))

SLOTS = 64
IDX = {"GetVolumeInfo": 0, "GetSecurityByName": 2, "Create": 3, "Open": 4, "Overwrite": 5, "Close": 7, "Read": 8,
       "GetFileInfo": 11, "ReadDirectory": 18}


class INTERFACE(ctypes.Structure):
    _fields_ = [("slots", PVOID * SLOTS)]


# ── WinFsp: wykrycie i biblioteka ──
_dll = None
_dll_error = ""


def _dll_path() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp", 0,
                                    winreg.KEY_READ | view) as k:
                    d = winreg.QueryValueEx(k, "InstallDir")[0]
                    p = Path(d) / "bin" / "winfsp-x64.dll"
                    if p.is_file():
                        return p
            except OSError:
                continue
    except Exception:
        pass
    p = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "WinFsp" / "bin" / "winfsp-x64.dll"
    return p if p.is_file() else None


def available() -> bool:
    """Czy WinFsp jest zainstalowany i jego biblioteka się wczytuje."""
    global _dll, _dll_error
    if _dll is not None:
        return True
    p = _dll_path()
    if not p:
        _dll_error = "WinFsp nie jest zainstalowany"
        return False
    try:
        _dll = ctypes.WinDLL(str(p))
        _dll.FspFileSystemCreate.restype = NTSTATUS
        _dll.FspFileSystemCreate.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(VOLUME_PARAMS),
                                             ctypes.POINTER(INTERFACE), ctypes.POINTER(FS)]
        _dll.FspFileSystemSetMountPoint.restype = NTSTATUS
        _dll.FspFileSystemSetMountPoint.argtypes = [FS, ctypes.c_wchar_p]
        _dll.FspFileSystemStartDispatcher.restype = NTSTATUS
        _dll.FspFileSystemStartDispatcher.argtypes = [FS, ctypes.c_ulong]
        _dll.FspFileSystemStopDispatcher.argtypes = [FS]
        _dll.FspFileSystemRemoveMountPoint.argtypes = [FS]
        _dll.FspFileSystemDelete.argtypes = [FS]
        _dll.FspFileSystemAddDirInfo.restype = ctypes.c_ubyte
        _dll.FspFileSystemAddDirInfo.argtypes = [PVOID, PVOID, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        return True
    except OSError as ex:
        _dll, _dll_error = None, f"nie można wczytać {p.name}: {ex}"
        return False


def status_text() -> str:
    return "dostępny" if available() else (_dll_error or "niedostępny")


def _filetime(t: float) -> int:
    return int((t + 11644473600) * 10_000_000)


def _free_letter() -> str | None:
    used = ctypes.windll.kernel32.GetLogicalDrives()
    for ch in reversed(string.ascii_uppercase[3:]):          # od Z: w dół, bez A–C
        if not used & (1 << (ord(ch) - ord("A"))):
            return ch
    return None


# ── pliki na dysku ──
class _Node:
    def __init__(self, name: str, is_dir: bool, source=None, size: int = 0, mtime: float = 0):
        self.name, self.is_dir, self.source, self.size = name, is_dir, source, size
        self.mtime = mtime or time.time()
        self.children: dict = {}          # małe litery → _Node


class Disk:
    def __init__(self):
        self.fs = FS()
        self.letter = ""
        self.root = _Node("", True)
        self.lock = threading.RLock()
        self.handles: dict = {}           # id kontekstu → _Node
        self._next = 1
        self._cb = {}                     # trzymamy referencje do funkcji zwrotnych
        self._sd = self._security()

    # rejestr plików
    def add_file(self, rel: str, source, size: int, mtime: float = 0) -> None:
        """source: emustart.partial.Partial albo Path pliku lokalnego."""
        parts = [p for p in rel.replace("/", "\\").split("\\") if p]
        with self.lock:
            node = self.root
            for p in parts[:-1]:
                node = node.children.setdefault(p.lower(), _Node(p, True))
            node.children[parts[-1].lower()] = _Node(parts[-1], False, source, size, mtime)

    def remove(self, top: str) -> None:
        with self.lock:
            self.root.children.pop(top.lower(), None)

    def _lookup(self, path: str):
        node = self.root
        for p in [p for p in (path or "").split("\\") if p]:
            if not node.is_dir:
                return None
            node = node.children.get(p.lower())
            if node is None:
                return None
        return node

    def _fill(self, node: _Node, fi) -> None:
        fi.FileAttributes = FILE_ATTRIBUTE_DIRECTORY if node.is_dir else FILE_ATTRIBUTE_READONLY
        fi.ReparseTag = 0
        fi.FileSize = 0 if node.is_dir else node.size
        fi.AllocationSize = (fi.FileSize + 4095) // 4096 * 4096
        ft = _filetime(node.mtime)
        fi.CreationTime = fi.LastAccessTime = fi.LastWriteTime = fi.ChangeTime = ft
        fi.IndexNumber = id(node) & 0xFFFFFFFFFFFF
        fi.HardLinks = 0
        fi.EaSize = 0

    @staticmethod
    def _security():
        psd, size = PVOID(), ctypes.c_ulong()
        ok = ctypes.windll.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            ctypes.c_wchar_p("O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;WD)"), 1,
            ctypes.byref(psd), ctypes.byref(size))
        if not ok:
            raise OSError("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        return ctypes.string_at(psd, size.value)

    # ── wywołania WinFsp ──
    def _get_volume_info(self, fs, vi):
        try:
            v = vi.contents
            v.TotalSize = 1 << 40
            v.FreeSize = 0
            label = "EmuStart"
            v.VolumeLabel = label
            v.VolumeLabelLength = len(label) * 2
            return OK
        except Exception:
            log.exception("vfs: GetVolumeInfo")
            return _st(STATUS_IO_DEVICE_ERROR)

    def _get_security_by_name(self, fs, name, pattr, psd, psize):
        try:
            with self.lock:
                node = self._lookup(name)
            if node is None:
                return _st(STATUS_OBJECT_NAME_NOT_FOUND)
            if pattr:
                pattr[0] = FILE_ATTRIBUTE_DIRECTORY if node.is_dir else FILE_ATTRIBUTE_READONLY
            if psize:
                need = len(self._sd)
                if psize[0] < need:
                    psize[0] = need
                    return _st(STATUS_BUFFER_OVERFLOW)
                psize[0] = need
                if psd:
                    ctypes.memmove(psd, self._sd, need)
            return OK
        except Exception:
            log.exception("vfs: GetSecurityByName")
            return _st(STATUS_IO_DEVICE_ERROR)

    def _open(self, fs, name, create_options, access, pctx, pfi):
        try:
            with self.lock:
                node = self._lookup(name)
                if node is None:
                    return _st(STATUS_OBJECT_NAME_NOT_FOUND)
                if (create_options & FILE_DIRECTORY_FILE) and not node.is_dir:
                    return _st(STATUS_NOT_A_DIRECTORY)
                h = self._next
                self._next += 1
                self.handles[h] = node
            pctx[0] = h
            self._fill(node, pfi.contents)
            return OK
        except Exception:
            log.exception("vfs: Open")
            return _st(STATUS_IO_DEVICE_ERROR)

    def _close(self, fs, ctx):
        with self.lock:
            self.handles.pop(ctx or 0, None)

    def _read(self, fs, ctx, buf, offset, length, pdone):
        try:
            with self.lock:
                node = self.handles.get(ctx or 0)
            if node is None or node.is_dir:
                return _st(STATUS_INVALID_DEVICE_REQUEST)
            if offset >= node.size:
                pdone[0] = 0
                return _st(STATUS_END_OF_FILE)
            src = node.source
            if isinstance(src, Path):
                with open(src, "rb") as f:
                    f.seek(offset)
                    data = f.read(length)
            else:
                data = src.read(offset, length)
            ctypes.memmove(buf, data, len(data))
            pdone[0] = len(data)
            return OK
        except Exception as ex:
            log.warning("vfs: odczyt %s: %s", getattr(node, "name", "?"), ex)
            return _st(STATUS_IO_DEVICE_ERROR)

    def _get_file_info(self, fs, ctx, pfi):
        with self.lock:
            node = self.handles.get(ctx or 0)
        if node is None:
            return _st(STATUS_INVALID_DEVICE_REQUEST)
        self._fill(node, pfi.contents)
        return OK

    def _read_directory(self, fs, ctx, pattern, marker, buf, length, pdone):
        try:
            with self.lock:
                node = self.handles.get(ctx or 0)
                if node is None or not node.is_dir:
                    return _st(STATUS_NOT_A_DIRECTORY)
                entries = sorted(node.children.values(), key=lambda n: n.name.lower())
            names = ([] if node is self.root else [".", ".."])
            items = [(n, None) for n in names] + [(e.name, e) for e in entries]

            def key(n: str):                  # „.” i „..” zawsze pierwsze, potem nazwy
                return (0, "") if n == "." else (1, "") if n == ".." else (2, n.lower())
            for name, e in items:
                if marker and key(name) <= key(marker):
                    continue
                size = ctypes.sizeof(DIR_INFO) + len(name) * 2
                raw = (ctypes.c_byte * size)()
                di = DIR_INFO.from_buffer(raw)
                di.Size = size
                self._fill(e or node, di.FileInfo)
                ctypes.memmove(ctypes.addressof(raw) + ctypes.sizeof(DIR_INFO),
                               ctypes.create_unicode_buffer(name, len(name) + 1), len(name) * 2)
                if not _dll.FspFileSystemAddDirInfo(ctypes.addressof(raw), buf, length, pdone):
                    return OK                 # bufor pełny — system zapyta ponownie od markera
            _dll.FspFileSystemAddDirInfo(None, buf, length, pdone)
            return OK
        except Exception:
            log.exception("vfs: ReadDirectory")
            return _st(STATUS_IO_DEVICE_ERROR)

    # ── montowanie ──
    def start(self) -> str:
        if not available():
            raise OSError(_dll_error or "WinFsp niedostępny")
        vp = VOLUME_PARAMS()
        vp.Version = ctypes.sizeof(VOLUME_PARAMS)
        vp.SectorSize = 4096
        vp.SectorsPerAllocationUnit = 1
        vp.MaxComponentLength = 255
        vp.VolumeCreationTime = _filetime(time.time())
        vp.VolumeSerialNumber = int(time.time()) & 0xFFFFFFFF
        vp.FileInfoTimeout = 1000
        vp.Flags = F_CASE_PRESERVED | F_UNICODE_ON_DISK | F_PERSISTENT_ACLS | F_READ_ONLY | F_CONTEXT_PER_OPEN
        vp.FileSystemName = "EmuStart"
        iface = INTERFACE()
        cbs = {"GetVolumeInfo": _GetVolumeInfo(self._get_volume_info),
               "GetSecurityByName": _GetSecurityByName(self._get_security_by_name),
               "Open": _Open(self._open), "Close": _Close(self._close), "Read": _Read(self._read),
               "Create": _Create(lambda *a: _st(STATUS_MEDIA_WRITE_PROTECTED)),
               "Overwrite": _Overwrite(lambda *a: _st(STATUS_MEDIA_WRITE_PROTECTED)),
               "GetFileInfo": _GetFileInfo(self._get_file_info),
               "ReadDirectory": _ReadDirectory(self._read_directory)}
        for k, f in cbs.items():
            iface.slots[IDX[k]] = ctypes.cast(f, PVOID)
        self._cb = {"iface": iface, "vp": vp, **cbs}
        st = _dll.FspFileSystemCreate("WinFsp.Disk", ctypes.byref(vp), ctypes.byref(iface), ctypes.byref(self.fs))
        if st != 0:
            raise OSError(f"FspFileSystemCreate: 0x{st & 0xFFFFFFFF:08X}")
        letter = _free_letter()
        if not letter:
            _dll.FspFileSystemDelete(self.fs)
            raise OSError("brak wolnej litery dysku")
        st = _dll.FspFileSystemSetMountPoint(self.fs, f"{letter}:")
        if st != 0:
            _dll.FspFileSystemDelete(self.fs)
            raise OSError(f"FspFileSystemSetMountPoint: 0x{st & 0xFFFFFFFF:08X}")
        st = _dll.FspFileSystemStartDispatcher(self.fs, 0)
        if st != 0:
            _dll.FspFileSystemRemoveMountPoint(self.fs)
            _dll.FspFileSystemDelete(self.fs)
            raise OSError(f"FspFileSystemStartDispatcher: 0x{st & 0xFFFFFFFF:08X}")
        self.letter = letter
        log.info("dysk strumieniowy %s: zamontowany (WinFsp)", letter)
        return f"{letter}:\\"

    def stop(self) -> None:
        if self.fs:
            _dll.FspFileSystemStopDispatcher(self.fs)
            _dll.FspFileSystemRemoveMountPoint(self.fs)
            _dll.FspFileSystemDelete(self.fs)
            self.fs = FS()
            log.info("dysk strumieniowy %s: odmontowany", self.letter)


_disk: Disk | None = None
_disk_lock = threading.Lock()


def disk() -> Disk:
    """Wspólny dysk strumieniowy (montowany przy pierwszym użyciu)."""
    global _disk
    with _disk_lock:
        if _disk is None:
            d = Disk()
            d.start()
            _disk = d
        return _disk


def shutdown() -> None:
    global _disk
    with _disk_lock:
        if _disk is not None:
            try:
                _disk.stop()
            except Exception:
                log.exception("vfs: odmontowanie")
            _disk = None
