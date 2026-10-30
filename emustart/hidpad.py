"""hidpad — bierny odczyt padów HID, które nie są padami XInput.

DualShock 4 / DualSense, Switch Pro, GameSir / 8BitDo w trybie Bluetooth HID nie są widoczne
w XInput. Czytamy je przez hid.dll: urządzenie otwarte do odczytu ze współdzieleniem — Windows
daje każdemu otwartemu uchwytowi własną kopię raportów, więc emulator czyta pada równolegle,
a my niczego do pada nie wysyłamy (bez raportów wyjściowych i funkcji).

Przyciski zamieniamy na maskę XInput (xinput.A…) według położenia na padzie: dolny = A,
prawy = B — niezależnie od napisu. Układy jak w Lumenie (src/main/input/hidLayout.ts).
Pady XInput (ścieżka z „IG_”) pomijamy — czyta je xinput.py, nic nie liczy się podwójnie.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
import zlib
from ctypes import wintypes

from emustart import xinput

log = logging.getLogger("emustart.hidpad")

LT, RT = 0x10000, 0x20000      # spusty (poza maską XInput, tylko do sterowania UI)

# numer przycisku HID (od 1) → przycisk XInput / spust
LAYOUTS = {
    # DualShock 4 / DualSense: Kwadrat, Krzyżyk, Kółko, Trójkąt, L1, R1, L2, R2, Share, Options, L3, R3, PS
    "sony": {1: xinput.X, 2: xinput.A, 3: xinput.B, 4: xinput.Y, 5: xinput.LB, 6: xinput.RB, 7: LT, 8: RT,
             9: xinput.BACK, 10: xinput.START, 11: xinput.L3, 12: xinput.R3},
    # Switch Pro: B (dolny), A (prawy), Y (lewy), X (górny), L, R, ZL, ZR, Minus, Plus, L3, R3
    "nintendo": {1: xinput.A, 2: xinput.B, 3: xinput.X, 4: xinput.Y, 5: xinput.LB, 6: xinput.RB, 7: LT, 8: RT,
                 9: xinput.BACK, 10: xinput.START, 11: xinput.L3, 12: xinput.R3},
    # pad „android” (tryb Bluetooth HID GameSira, 8BitDo…): A, B, C, X, Y, Z, L1, R1, L2, R2,
    # Select, Start, Home, L3, R3 — C i Z zwykle nieużywane (GameSir Nova 2 Lite sprawdzony)
    "android": {1: xinput.A, 2: xinput.B, 4: xinput.X, 5: xinput.Y, 7: xinput.LB, 8: xinput.RB, 9: LT, 10: RT,
                11: xinput.BACK, 12: xinput.START, 14: xinput.L3, 15: xinput.R3},
    # pady DirectInput w układzie Xbox: A, B, X, Y, LB, RB, Back, Start, L3, R3
    "xbox": {1: xinput.A, 2: xinput.B, 3: xinput.X, 4: xinput.Y, 5: xinput.LB, 6: xinput.RB,
             7: xinput.BACK, 8: xinput.START, 9: xinput.L3, 10: xinput.R3},
}
# przycisk Home / PS (do mapowania SDL), numer HID
GUIDE = {"sony": 13, "nintendo": 13, "android": 13, "xbox": 0}


def layout_for(vid: int, buttons: int) -> str:
    """Układ przycisków: wg producenta, inaczej wg liczby przycisków (jak hidLayout.ts)."""
    if vid == 0x054C:
        return "sony"
    if vid == 0x057E:
        return "nintendo"
    return "android" if buttons >= 15 else "sony" if buttons >= 12 else "xbox"


def number_of(layout: str, mask: int) -> int:
    """Numer przycisku HID (od 1) dla przycisku XInput w danym układzie; 0 = brak."""
    return next((n for n, m in LAYOUTS[layout].items() if m == mask), 0)


# kierunek hat (0 = góra, zgodnie z zegarem) → krzyżak
_HAT = (xinput.UP, xinput.UP | xinput.RIGHT, xinput.RIGHT, xinput.DOWN | xinput.RIGHT, xinput.DOWN,
        xinput.DOWN | xinput.LEFT, xinput.LEFT, xinput.UP | xinput.LEFT)


def hat_direction(value: int, lo: int, hi: int):
    """Wartość hat → kierunek 0–7, None = środek (wartość spoza zakresu to „puszczony”)."""
    d = value - lo
    if d < 0 or value > hi:
        return None
    if hi - lo == 7:
        return d
    if hi - lo == 3:
        return d * 2                         # hat 4-kierunkowy
    return None


def to_mask(layout: str, pressed, x: float = 0.0, y: float = 0.0, hat=None) -> int:
    """Wciśnięte numery przycisków + gałka (-1..1, Y w dół) + hat → maska XInput (+ LT/RT)."""
    m = 0
    lay = LAYOUTS[layout]
    for b in pressed:
        m |= lay.get(b, 0)
    if hat is not None:
        m |= _HAT[hat]
    dz = 0.5
    if y < -dz:
        m |= xinput.UP
    elif y > dz:
        m |= xinput.DOWN
    if x < -dz:
        m |= xinput.LEFT
    elif x > dz:
        m |= xinput.RIGHT
    return m


# ── WinAPI ──
class _GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]


class _IfData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("guid", _GUID), ("flags", wintypes.DWORD),
                ("reserved", ctypes.c_size_t)]


class _Attrs(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT), ("ProductID", wintypes.USHORT),
                ("VersionNumber", wintypes.USHORT)]


class _Caps(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT), ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT), ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.USHORT), ("NumberInputButtonCaps", wintypes.USHORT),
                ("NumberInputValueCaps", wintypes.USHORT), ("NumberInputDataIndices", wintypes.USHORT),
                ("rest", wintypes.USHORT * 6)]


class _Range(ctypes.Structure):
    _fields_ = [("UsageMin", wintypes.USHORT), ("UsageMax", wintypes.USHORT), ("r", wintypes.USHORT * 6)]


class _ButtonCaps(ctypes.Structure):
    _fields_ = [("UsagePage", wintypes.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", wintypes.USHORT), ("LinkCollection", wintypes.USHORT),
                ("LinkUsage", wintypes.USHORT), ("LinkUsagePage", wintypes.USHORT),
                ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte),
                ("ReportCount", wintypes.USHORT), ("Reserved2", wintypes.USHORT),
                ("Reserved", wintypes.ULONG * 9), ("Range", _Range)]


class _ValueCaps(ctypes.Structure):
    _fields_ = [("UsagePage", wintypes.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", wintypes.USHORT), ("LinkCollection", wintypes.USHORT),
                ("LinkUsage", wintypes.USHORT), ("LinkUsagePage", wintypes.USHORT),
                ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte),
                ("HasNull", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte), ("BitSize", wintypes.USHORT),
                ("ReportCount", wintypes.USHORT), ("Reserved2", wintypes.USHORT * 5),
                ("UnitsExp", wintypes.ULONG), ("Units", wintypes.ULONG),
                ("LogicalMin", wintypes.LONG), ("LogicalMax", wintypes.LONG),
                ("PhysicalMin", wintypes.LONG), ("PhysicalMax", wintypes.LONG), ("Range", _Range)]


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


GENERIC_READ = 0x80000000
SHARE_RW = 0x1 | 0x2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID = ctypes.c_void_p(-1).value
HIDP_INPUT = 0
HIDP_STATUS_SUCCESS = 0x00110000
_GUID_HID = _GUID(0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))

_hid = _k32 = _sapi = None
if os.name == "nt":
    try:
        _hid = ctypes.WinDLL("hid")
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _sapi = ctypes.WinDLL("setupapi", use_last_error=True)
        _k32.CreateFileW.restype = wintypes.HANDLE
        _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        _k32.CreateEventW.restype = wintypes.HANDLE
        _k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
        _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL]
        _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        _k32.ResetEvent.argtypes = [wintypes.HANDLE]
        _sapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
        _sapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
        _sapi.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                                      wintypes.DWORD, ctypes.c_void_p]
        _sapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                                           wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
        _sapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
        _hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        _hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        _hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
        _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _hid.HidP_GetButtonCaps.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        _hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        _hid.HidP_GetUsages.argtypes = [ctypes.c_int, wintypes.USHORT, wintypes.USHORT, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
        _hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, wintypes.USHORT, wintypes.USHORT, wintypes.USHORT,
                                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
        _hid.HidP_GetCaps.restype = _hid.HidP_GetUsages.restype = _hid.HidP_GetUsageValue.restype = wintypes.LONG
    except (OSError, AttributeError):
        _hid = None


def available() -> bool:
    return _hid is not None


def _paths() -> list:
    """Ścieżki wszystkich obecnych interfejsów HID."""
    info = _sapi.SetupDiGetClassDevsW(ctypes.byref(_GUID_HID), None, None, 0x2 | 0x10)
    if info in (None, INVALID):
        return []
    out = []
    try:
        i = 0
        while True:
            d = _IfData()
            d.cbSize = ctypes.sizeof(_IfData)
            if not _sapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(_GUID_HID), i, ctypes.byref(d)):
                break
            i += 1
            need = wintypes.DWORD()
            _sapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(d), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value + 4)
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if _sapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(d), buf, need, None, None):
                out.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        _sapi.SetupDiDestroyDeviceInfoList(info)
    return out


class Pad:
    """Opis pada HID: ścieżka, VID/PID, nazwa, liczba przycisków, układ, zakresy osi."""

    def __init__(self, path, vid, pid, name, buttons, report_len, values, version=0):
        self.path, self.vid, self.pid, self.name = path, vid, pid, name
        self.buttons, self.report_len, self.values, self.version = buttons, report_len, values, version
        self.layout = layout_for(vid, buttons)
        self.bluetooth = "{00001124-" in path.lower()

    @property
    def id(self) -> str:
        """Tożsamość pada (kolejność padów): VID:PID + skrót ścieżki urządzenia."""
        return f"hid:{self.vid:04x}:{self.pid:04x}:{zlib.crc32(self.path.lower().encode()):08x}"

    def info(self) -> dict:
        return {"id": self.id, "vid": f"{self.vid:04x}", "pid": f"{self.pid:04x}", "name": self.name,
                "layout": self.layout, "bluetooth": self.bluetooth, "buttons": self.buttons}


def _parent_id(path: str) -> str:
    """Identyfikator urządzenia nadrzędnego (np. BTHENUM\\…_<MAC>_C00000000) dla ścieżki HID."""
    parts = path.split("#")                     # \\?\hid#<sprzęt>#<instancja>#{interfejs}
    if len(parts) != 4:
        return ""
    inst = "\\".join((parts[0].rsplit("\\", 1)[-1], parts[1], parts[2]))
    try:
        cm = ctypes.WinDLL("cfgmgr32")
        node, par = wintypes.DWORD(), wintypes.DWORD()
        if cm.CM_Locate_DevNodeW(ctypes.byref(node), inst, 0) or cm.CM_Get_Parent(ctypes.byref(par), node, 0):
            return ""
        buf = ctypes.create_unicode_buffer(512)
        return "" if cm.CM_Get_Device_IDW(par, buf, 512, 0) else buf.value
    except (OSError, AttributeError):
        return ""


def _bt_name(path: str) -> str:
    """Nazwa pada Bluetooth (sterownik HID jej nie podaje) — z rejestru stosu Bluetooth wg adresu MAC."""
    import re
    import winreg
    parent = _parent_id(path)
    m = re.search(r"[&_]([0-9a-f]{12})_c0", parent.lower())
    if not m:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices\{m.group(1)}") as k:
            return bytes(winreg.QueryValueEx(k, "Name")[0]).split(b"\0")[0].decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _describe(path: str):
    h = _k32.CreateFileW(path, 0, SHARE_RW, None, OPEN_EXISTING, 0, None)
    if h in (None, INVALID):
        return None
    ppd = ctypes.c_void_p()
    try:
        a = _Attrs()
        a.Size = ctypes.sizeof(_Attrs)
        if not _hid.HidD_GetAttributes(h, ctypes.byref(a)) or not _hid.HidD_GetPreparsedData(h, ctypes.byref(ppd)):
            return None
        caps = _Caps()
        if _hid.HidP_GetCaps(ppd, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
            return None
        if caps.UsagePage != 1 or caps.Usage not in (4, 5):        # joystick / gamepad
            return None
        n = wintypes.USHORT(caps.NumberInputButtonCaps)
        bcaps = (_ButtonCaps * max(1, n.value))()
        buttons = 0
        if n.value and _hid.HidP_GetButtonCaps(HIDP_INPUT, bcaps, ctypes.byref(n), ppd) == HIDP_STATUS_SUCCESS:
            for c in bcaps[:n.value]:
                if c.UsagePage == 9:
                    buttons = max(buttons, c.Range.UsageMax if c.IsRange else c.Range.UsageMin)
        n = wintypes.USHORT(caps.NumberInputValueCaps)
        vcaps = (_ValueCaps * max(1, n.value))()
        values = {}
        if n.value and _hid.HidP_GetValueCaps(HIDP_INPUT, vcaps, ctypes.byref(n), ppd) == HIDP_STATUS_SUCCESS:
            for c in vcaps[:n.value]:
                if c.UsagePage != 1:
                    continue
                lo, hi = c.LogicalMin, c.LogicalMax
                if hi < lo:                                        # zakres bez znaku zapisany jako ujemny
                    hi &= (1 << c.BitSize) - 1
                for u in range(c.Range.UsageMin, (c.Range.UsageMax if c.IsRange else c.Range.UsageMin) + 1):
                    values.setdefault(u, (lo, hi, c.BitSize))
        name = ctypes.create_unicode_buffer(128)
        _hid.HidD_GetProductString(h, name, ctypes.sizeof(name))
        return Pad(path, a.VendorID, a.ProductID, name.value.strip() or _bt_name(path) or f"Pad HID {a.VendorID:04X}:{a.ProductID:04X}",
                   buttons, caps.InputReportByteLength, values, a.VersionNumber)
    finally:
        if ppd:
            _hid.HidD_FreePreparsedData(ppd)
        _k32.CloseHandle(h)


_cache: dict = {}                # ścieżka → Pad | None (opis urządzenia się nie zmienia)
_cache_lock = threading.Lock()


def pads() -> list:
    """Podłączone pady HID (bez padów XInput), w kolejności Windows."""
    if not _hid:
        return []
    out = []
    for p in _paths():
        if "ig_" in p.lower():
            continue
        with _cache_lock:
            if p not in _cache:
                try:
                    _cache[p] = _describe(p)
                except OSError:
                    _cache[p] = None
            pad = _cache[p]
        if pad:
            out.append(pad)
    return out


def _signed(v: int, lo: int, bits: int) -> int:
    if lo < 0 and bits and v >= 1 << (bits - 1):
        v -= 1 << bits
    return v


def _axis(v: int, lo: int, hi: int) -> float:
    if hi <= lo:
        return 0.0
    v = min(hi, max(lo, v))
    return (v - lo) / (hi - lo) * 2 - 1


class _Reader:
    """Wątek czytający raporty jednego pada; `mask` to ostatni stan."""

    def __init__(self, pad: Pad):
        self.pad = pad
        self.mask = 0
        self.alive = True
        self.stop = False
        threading.Thread(target=self._run, daemon=True, name="hidpad").start()

    def _run(self) -> None:
        pad = self.pad
        h = _k32.CreateFileW(pad.path, GENERIC_READ, SHARE_RW, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if h in (None, INVALID):
            log.info("pad HID %s: nie można otworzyć do odczytu (%d)", pad.name, ctypes.get_last_error())
            self.alive = False
            return
        ppd = ctypes.c_void_p()
        ev = _k32.CreateEventW(None, True, False, None)
        try:
            if not _hid.HidD_GetPreparsedData(h, ctypes.byref(ppd)):
                return
            buf = ctypes.create_string_buffer(max(pad.report_len, 64))
            usages = (wintypes.USHORT * 64)()
            ov = _Overlapped()
            ov.hEvent = ev
            got = wintypes.DWORD()
            while not self.stop:
                _k32.ResetEvent(ev)
                if not _k32.ReadFile(h, buf, pad.report_len, None, ctypes.byref(ov)):
                    if ctypes.get_last_error() != 997:              # ERROR_IO_PENDING
                        break                                       # pad odłączony
                    if _k32.WaitForSingleObject(ev, 200) != 0:
                        _k32.CancelIoEx(h, ctypes.byref(ov))
                        _k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(got), True)
                        continue
                if not _k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(got), False):
                    break
                self._parse(ppd, buf, got.value, usages)
        finally:
            if ppd:
                _hid.HidD_FreePreparsedData(ppd)
            _k32.CloseHandle(ev)
            _k32.CloseHandle(h)
            self.mask = 0
            self.alive = False

    def _parse(self, ppd, buf, length: int, usages) -> None:
        n = wintypes.ULONG(len(usages))
        st = _hid.HidP_GetUsages(HIDP_INPUT, 9, 0, usages, ctypes.byref(n), ppd, buf, length)
        if st != HIDP_STATUS_SUCCESS:
            return                                                  # inny raport (np. bateria)
        pressed = list(usages[:n.value])
        vals = {}
        for u in (0x30, 0x31, 0x39):
            if u not in self.pad.values:
                continue
            v = wintypes.ULONG()
            if _hid.HidP_GetUsageValue(HIDP_INPUT, 1, 0, u, ctypes.byref(v), ppd, buf, length) == HIDP_STATUS_SUCCESS:
                lo, hi, bits = self.pad.values[u]
                vals[u] = _signed(v.value, lo, bits)
        x = _axis(vals[0x30], *self.pad.values[0x30][:2]) if 0x30 in vals else 0.0
        y = _axis(vals[0x31], *self.pad.values[0x31][:2]) if 0x31 in vals else 0.0
        hat = hat_direction(vals[0x39], *self.pad.values[0x39][:2]) if 0x39 in vals else None
        self.mask = to_mask(self.pad.layout, pressed, x, y, hat)


class _Hub:
    """Czytniki wszystkich padów HID. Działa tylko wtedy, gdy ktoś pyta o stan (menu, skróty):
    po 2 s bez pytań czytniki się zamykają, lista padów odświeża się co 2 s."""

    IDLE = 2.0

    def __init__(self):
        self._readers: dict = {}
        self._lock = threading.Lock()
        self._last_ask = 0.0
        self._scan_at = 0.0
        self._watch = None

    def masks(self) -> list:
        """[(Pad, maska)] — otwiera czytniki przy pierwszym pytaniu."""
        now = time.monotonic()
        with self._lock:
            self._last_ask = now
            if now >= self._scan_at:
                self._scan_at = now + 2.0
                current = {p.path: p for p in pads()}
                for path in list(self._readers):
                    r = self._readers[path]
                    if path not in current or not r.alive:
                        r.stop = True
                        del self._readers[path]
                for path, p in current.items():
                    if path not in self._readers:
                        self._readers[path] = _Reader(p)
                if self._watch is None:
                    self._watch = threading.Thread(target=self._idle, daemon=True, name="hidpad-idle")
                    self._watch.start()
            return [(r.pad, r.mask) for r in self._readers.values()]

    def _idle(self) -> None:
        while True:
            time.sleep(0.5)
            with self._lock:
                if time.monotonic() - self._last_ask > self.IDLE and self._readers:
                    for r in self._readers.values():
                        r.stop = True
                    self._readers.clear()
                    self._scan_at = 0.0


_hub = _Hub()


def states() -> list:
    """[(Pad, maska XInput z LT/RT)] podłączonych padów HID."""
    return _hub.masks() if _hid else []


def all_buttons() -> int:
    """Suma przycisków wszystkich padów HID (maska XInput, bez spustów)."""
    out = 0
    for _p, m in states():
        out |= m
    return out & 0xFFFF


# ── DirectInput: kolejność urządzeń (numery padów w RetroArchu, sterownik xinput = XInput + DirectInput) ──
class _DIInstance(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("guidInstance", _GUID), ("guidProduct", _GUID),
                ("dwDevType", wintypes.DWORD), ("tszInstanceName", wintypes.WCHAR * 260),
                ("tszProductName", wintypes.WCHAR * 260), ("guidFFDriver", _GUID),
                ("wUsagePage", wintypes.WORD), ("wUsage", wintypes.WORD)]


_IID_DI8W = _GUID(0xBF798031, 0x483A, 0x4DA2, (ctypes.c_ubyte * 8)(0xAA, 0x99, 0x5D, 0x64, 0xED, 0x36, 0x97, 0x00))


def dinput_devices() -> list:
    """[(vid, pid, nazwa)] kontrolerów gier w kolejności DirectInput — tak numeruje je RetroArch
    (pady XInput też są na tej liście, na swoich miejscach)."""
    if os.name != "nt":
        return []
    try:
        di8 = ctypes.WinDLL("dinput8")
        hinst = ctypes.WinDLL("kernel32").GetModuleHandleW
        hinst.restype = wintypes.HMODULE
        di8.DirectInput8Create.argtypes = [wintypes.HMODULE, wintypes.DWORD, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        obj = ctypes.c_void_p()
        if di8.DirectInput8Create(hinst(None), 0x0800, ctypes.byref(_IID_DI8W), ctypes.byref(obj), None) != 0:
            return []
    except (OSError, AttributeError):
        return []
    vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    cb_t = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.POINTER(_DIInstance), ctypes.c_void_p)
    enum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD, cb_t, ctypes.c_void_p,
                              wintypes.DWORD)(vtbl[4])
    release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])
    out = []

    def cb(inst, _ref):
        d1 = inst.contents.guidProduct.d1
        out.append((d1 & 0xFFFF, d1 >> 16, inst.contents.tszProductName))
        return 1                                                    # DIENUM_CONTINUE

    try:
        enum(obj, 4, cb_t(cb), None, 1)                             # DI8DEVCLASS_GAMECTRL, DIEDFL_ATTACHEDONLY
    finally:
        release(obj)
    return out
