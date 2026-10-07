"""winutil — drobne wywołania WinAPI przez ctypes (bez pywin32)."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

IS_WIN = os.name == "nt"

FILE_ATTRIBUTE_TEMPORARY = 0x100
GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
INVALID_HANDLE = ctypes.c_void_p(-1).value

if IS_WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
    _u32 = ctypes.WinDLL("user32", use_last_error=True)


def open_temp_write(path: str):
    """Plik do zapisu z FILE_ATTRIBUTE_TEMPORARY: menedżer pamięci trzyma go w RAM
    i nie spieszy się z zapisem na dysk. Poza Windows — zwykły open()."""
    if not IS_WIN:
        return open(path, "wb")
    import msvcrt
    h = _k32.CreateFileW(path, GENERIC_WRITE, 0, None, CREATE_ALWAYS,
                         FILE_ATTRIBUTE_TEMPORARY, None)
    if h in (None, INVALID_HANDLE):
        return open(path, "wb")
    fd = msvcrt.open_osfhandle(h, os.O_WRONLY | os.O_BINARY)
    return os.fdopen(fd, "wb")


def find_window(title: str) -> int:
    if not IS_WIN:
        return 0
    return _u32.FindWindowW(None, title) or 0


def bring_to_front(hwnd: int) -> None:
    """SetForegroundWindow bywa blokowane, gdy proces nie ma „prawa” do fokusu.
    Naciśnięcie i puszczenie Alt odblokowuje je (znany, nieszkodliwy trik)."""
    if not IS_WIN or not hwnd:
        return
    VK_MENU, KEYUP = 0x12, 0x0002
    _u32.keybd_event(VK_MENU, 0, 0, 0)
    _u32.keybd_event(VK_MENU, 0, KEYUP, 0)
    # SW_RESTORE tylko dla zminimalizowanego: na zmaksymalizowanym oknie (pełny
    # ekran pywebview, emulator bez ramki) zdjęłoby maksymalizację
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)
    _u32.BringWindowToTop(hwnd)
    _u32.SetForegroundWindow(hwnd)


# ── okna procesu (menu w grze) ──

WM_CLOSE = 0x0010


def windows_of(pid: int) -> list:
    """Widoczne okna najwyższego poziomu procesu, największe pierwsze."""
    if not IS_WIN:
        return []
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _u32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True
    _u32.EnumWindows(proto(cb), 0)
    return [h for _a, h in sorted(found, reverse=True)]


def close_windows(pid: int) -> None:
    for hwnd in windows_of(pid):
        _u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def set_topmost(hwnd: int, on: bool) -> None:
    if not IS_WIN or not hwnd:
        return
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x2, 0x1, 0x40
    _u32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)


# ── klawiatura: SendInput ze scancode'ami (Qt/SDL w emulatorach czytają scancode) ──

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


VK = {f"F{i}": 0x6F + i for i in range(1, 13)}
VK.update({"SPACE": 0x20, "ESCAPE": 0x1B, "ENTER": 0x0D, "SHIFT": 0x10, "CTRL": 0x11,
           "ALT": 0x12, "P": 0x50})


def _key(vk: int, up: bool) -> _INPUT:
    KEYEVENTF_SCANCODE, KEYEVENTF_KEYUP = 0x0008, 0x0002
    scan = _u32.MapVirtualKeyW(vk, 0)
    i = _INPUT(type=1)
    i.ki = _KEYBDINPUT(vk, scan, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
    return i


def send_keys(combo: str, hold: float = 0.08) -> None:
    """Naciska kombinację, np. 'F2' albo 'SHIFT+F1', w oknie z fokusem."""
    if not IS_WIN or not combo:
        return
    import time
    vks = [VK[k.strip().upper()] for k in combo.split("+") if k.strip().upper() in VK]
    if not vks:
        return
    down = (_INPUT * len(vks))(*[_key(v, False) for v in vks])
    _u32.SendInput(len(vks), down, ctypes.sizeof(_INPUT))
    time.sleep(hold)                    # emulatory sprawdzają klawisze co klatkę
    up = (_INPUT * len(vks))(*[_key(v, True) for v in reversed(vks)])
    _u32.SendInput(len(vks), up, ctypes.sizeof(_INPUT))

