"""xinput — odczyt padów XInput (Xbox, a także PS przez Steam Input/DS4Windows).

Działa niezależnie od fokusu okna, więc widzi pad także wtedy, gdy gra ma fokus.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

A, B, X, Y = 0x1000, 0x2000, 0x4000, 0x8000
UP, DOWN, LEFT, RIGHT = 0x1, 0x2, 0x4, 0x8
START, BACK = 0x10, 0x20
L3, R3, LB, RB = 0x40, 0x80, 0x100, 0x200
# lewa gałka — osobne bity (poza maską XInput i spustami hidpad): w menu działa jak krzyżak,
# w skrótach Select + ↑/↓ nie (drgnięcie gałki w grze z wciśniętym Select przełączało sloty)
STICK_UP, STICK_DOWN, STICK_LEFT, STICK_RIGHT = 0x100000, 0x200000, 0x400000, 0x800000
STICK = STICK_UP | STICK_DOWN | STICK_LEFT | STICK_RIGHT


def stick_as_dpad(b: int) -> int:
    """Maska z gałką dołożoną do krzyżaka — do sterowania menu i interfejsem."""
    for s, d in ((STICK_UP, UP), (STICK_DOWN, DOWN), (STICK_LEFT, LEFT), (STICK_RIGHT, RIGHT)):
        if b & s:
            b |= d
    return b


class _Pad(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _Pad)]


_dll = None
if os.name == "nt":
    for _name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            _dll = ctypes.WinDLL(_name)
            break
        except OSError:
            continue


def available() -> bool:
    return _dll is not None


def buttons(index: int) -> int | None:
    """Maska przycisków pada 0–3 (gałka — bity STICK_*, nie krzyżak); None = brak pada."""
    if not _dll:
        return None
    st = _State()
    if _dll.XInputGetState(index, ctypes.byref(st)) != 0:
        return None
    g = st.Gamepad
    b = g.wButtons
    dz = 16000
    if g.sThumbLY > dz:
        b |= STICK_UP
    elif g.sThumbLY < -dz:
        b |= STICK_DOWN
    if g.sThumbLX < -dz:
        b |= STICK_LEFT
    elif g.sThumbLX > dz:
        b |= STICK_RIGHT
    return b


def all_buttons() -> int:
    """Suma przycisków wszystkich podłączonych padów."""
    out = 0
    for i in range(4):
        out |= buttons(i) or 0
    return out
