"""sdlpads — pady HID w emulatorach na SDL (DuckStation, PCSX2, Dolphin, Cemu, Ryujinx…).

1. Układ przycisków: SDL zna pady XInput, PS i Switch Pro, ale nie zna np. GameSira w trybie
   Bluetooth HID — taki pad byłby dla emulatora „joystickiem” bez A/B/X/Y. EmuStart podaje
   emulatorowi przy starcie zmienną SDL_GAMECONTROLLERCONFIG z układem z hidpad.LAYOUTS
   (dolny = A), więc pad jest zwykłym padem. Pliki ustawień emulatora się nie zmieniają.
   Pady, które SDL obsługuje sam (sterownik HIDAPI: PS4, PS5, Switch), mają inny GUID
   (znacznik sterownika) — nasz wpis ich nie dotyczy.

2. Numer „SDL-n” w DuckStation/PCSX2: tak jak emulator nadaje go z SDL3.dll tego emulatora.
   SDL otwiera część urządzeń HID, więc liczymy to w osobnym, krótkim procesie
   (main.py --sdl-probe=<SDL3.dll>), z limitem czasu — nie w procesie EmuStart.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from emustart import hidpad, xinput

log = logging.getLogger("emustart.sdlpads")

ENV = "SDL_GAMECONTROLLERCONFIG"

# przycisk SDL → przycisk XInput / spust (hidpad)
_SDL_BUTTONS = (("a", xinput.A), ("b", xinput.B), ("x", xinput.X), ("y", xinput.Y),
                ("back", xinput.BACK), ("start", xinput.START), ("leftshoulder", xinput.LB),
                ("rightshoulder", xinput.RB), ("leftstick", xinput.L3), ("rightstick", xinput.R3),
                ("lefttrigger", hidpad.LT), ("righttrigger", hidpad.RT))


def _le16(v: int) -> str:
    return f"{v & 0xFF:02x}{v >> 8 & 0xFF:02x}"


def guids(vid: int, pid: int) -> list:
    """GUID-y joysticka DirectInput w SDL (bez sumy kontrolnej nazwy i wersji — SDL dopasowuje
    mapowanie także bez nich): magistrala USB (stare SDL, także dla Bluetooth) i Bluetooth."""
    return [f"{bus}000000{_le16(vid)}0000{_le16(pid)}000000000000" for bus in ("03", "05")]


def axes(pad) -> dict:
    """Numery osi SDL (DirectInput: kolejność użyć HID) — lewa i prawa gałka."""
    order = sorted(u for u in pad.values if 0x30 <= u <= 0x35)
    idx = {u: i for i, u in enumerate(order)}
    out = {}
    if 0x30 in idx and 0x31 in idx:
        out.update(leftx=idx[0x30], lefty=idx[0x31])
    right = (0x32, 0x35) if 0x32 in idx and 0x35 in idx else (0x33, 0x34)
    if right[0] in idx and right[1] in idx:
        out.update(rightx=idx[right[0]], righty=idx[right[1]])
    return out


def mapping(pad) -> list:
    """Wpisy SDL_GAMECONTROLLERCONFIG dla pada HID (po jednym na GUID)."""
    name = pad.name.replace(",", " ")
    parts = []
    for key, mask in _SDL_BUTTONS:
        n = hidpad.number_of(pad.layout, mask)
        if n:
            parts.append(f"{key}:b{n - 1}")
    guide = hidpad.GUIDE.get(pad.layout, 0)
    if guide and guide <= pad.buttons:
        parts.append(f"guide:b{guide - 1}")
    if 0x39 in pad.values:
        parts += ["dpup:h0.1", "dpright:h0.2", "dpdown:h0.4", "dpleft:h0.8"]
    parts += [f"{k}:a{v}" for k, v in axes(pad).items()]
    body = ",".join(parts)
    return [f"{g},{name},{body},platform:Windows," for g in guids(pad.vid, pad.pid)]


def env(base: dict | None = None) -> dict:
    """Środowisko dla emulatora: SDL_GAMECONTROLLERCONFIG z układem podłączonych padów HID
    (dopisane do tego, co gracz ma ustawione sam)."""
    out = dict(os.environ if base is None else base)
    try:
        lines = [m for p in hidpad.pads() for m in mapping(p)]
    except Exception:
        log.exception("układ padów HID dla SDL")
        return out
    if lines:
        own = out.get(ENV, "").strip()
        out[ENV] = "\n".join(([own] if own else []) + lines)
        log.info("SDL: układ padów HID dla emulatora: %s", "; ".join(sorted({p.name for p in hidpad.pads()})))
    return out


# ── numer SDL-n (DuckStation / PCSX2) ──
def player_ids(joys: list) -> list:
    """Numer „SDL-n”, jak nadają go DuckStation/PCSX2: indeks gracza SDL, a gdy go brak albo jest
    zajęty — najniższy wolny (w kolejności wykrycia)."""
    used: set = set()
    out = []
    for j in joys:
        pi = j.get("player", -1)
        if pi < 0 or pi in used:
            pi = next(i for i in range(len(joys) + 8) if i not in used)
        used.add(pi)
        out.append(dict(j, sdl=pi))
    return out


def joysticks(dll_path: Path, wait: float = 0.6) -> list:
    """[{name, path, vid, pid, player}] z SDL3 — tylko w osobnym procesie (--sdl-probe)."""
    sdl = ctypes.CDLL(str(dll_path))
    sdl.SDL_Init.restype = ctypes.c_bool
    sdl.SDL_Init.argtypes = [ctypes.c_uint32]
    sdl.SDL_GetJoysticks.restype = ctypes.POINTER(ctypes.c_uint32)
    sdl.SDL_GetJoysticks.argtypes = [ctypes.POINTER(ctypes.c_int)]
    for f, r in (("SDL_GetJoystickNameForID", ctypes.c_char_p), ("SDL_GetJoystickPathForID", ctypes.c_char_p),
                 ("SDL_GetJoystickPlayerIndexForID", ctypes.c_int), ("SDL_GetJoystickVendorForID", ctypes.c_uint16),
                 ("SDL_GetJoystickProductForID", ctypes.c_uint16)):
        getattr(sdl, f).restype = r
        getattr(sdl, f).argtypes = [ctypes.c_uint32]
    sdl.SDL_free.argtypes = [ctypes.c_void_p]
    sdl.SDL_GetError.restype = ctypes.c_char_p
    if not sdl.SDL_Init(0x200 | 0x2000):                    # SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD
        raise RuntimeError((sdl.SDL_GetError() or b"").decode("utf-8", "replace"))
    try:
        end = time.time() + wait
        while time.time() < end:                            # część urządzeń wykrywana asynchronicznie
            sdl.SDL_PumpEvents()
            time.sleep(0.05)
        n = ctypes.c_int()
        ids = sdl.SDL_GetJoysticks(ctypes.byref(n))
        dec = (lambda b: (b or b"").decode("utf-8", "replace"))
        out = [{"name": dec(sdl.SDL_GetJoystickNameForID(ids[i])), "path": dec(sdl.SDL_GetJoystickPathForID(ids[i])),
                "vid": int(sdl.SDL_GetJoystickVendorForID(ids[i])), "pid": int(sdl.SDL_GetJoystickProductForID(ids[i])),
                "player": int(sdl.SDL_GetJoystickPlayerIndexForID(ids[i]))} for i in range(n.value)]
        if ids:
            sdl.SDL_free(ids)
        return out
    finally:
        sdl.SDL_Quit()


def probe_main(dll: str, out: str) -> int:
    """main.py --sdl-probe=<SDL3.dll> --sdl-out=<plik>: joysticki SDL z numerami SDL-n jako JSON
    (do pliku — EmuStart.exe to program okienkowy, bez standardowego wyjścia)."""
    try:
        r = {"joysticks": player_ids(joysticks(Path(dll)))}
    except Exception as ex:
        r = {"error": str(ex)}
    Path(out).write_text(json.dumps(r), encoding="utf-8")
    return 0


def probe(dll: Path, timeout: float = 8.0) -> list | None:
    """Joysticki SDL emulatora z numerami SDL-n (osobny proces); None przy błędzie / po czasie."""
    cmd = [sys.executable] if getattr(sys, "frozen", False) else [
        sys.executable, str(Path(__file__).resolve().parent.parent / "main.py")]
    import tempfile
    fd, out = tempfile.mkstemp(prefix="emustart-sdl-", suffix=".json")
    os.close(fd)
    cmd += [f"--sdl-probe={dll}", f"--sdl-out={out}"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, env=env(),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(Path(out).read_text(encoding="utf-8"))
    except Exception as ex:
        log.warning("SDL: numery padów niedostępne (%s)", ex)
        return None
    finally:
        Path(out).unlink(missing_ok=True)
    if data.get("error"):
        log.warning("SDL: %s", data["error"])
        return None
    return data.get("joysticks", [])


def index_of(joys: list, pad) -> int | None:
    """Numer SDL-n pada HID: po ścieżce urządzenia, inaczej po jedynym joysticku o tym VID:PID."""
    hit = next((j for j in joys if (j.get("path") or "").lower() == pad.path.lower()), None)
    if hit is None:
        same = [j for j in joys if (j.get("vid"), j.get("pid")) == (pad.vid, pad.pid)]
        hit = same[0] if len(same) == 1 else None
    return hit["sdl"] if hit else None


def slots_for(order: list, sdl_dll: Path | None) -> list:
    """Kolejność padów (sloty XInput / „hid:…”) → numery SDL-n emulatora. Slot XInput = jego numer
    (jak dotąd); pad HID — z SDL3.dll emulatora, a gdy się nie da: None (bez przepinania)."""
    from emustart import pads
    if not any(pads.is_hid(s) for s in order):
        return list(order)
    joys = probe(sdl_dll) if sdl_dll and sdl_dll.is_file() else None
    out = []
    for s in order:
        if not pads.is_hid(s):
            # pad XInput: „XInput#N” w SDL; bez odczytu — jak dotąd SDL-n = slot XInput
            hit = next((j for j in joys or [] if (j.get("path") or "").lower() == f"xinput#{s}"), None)
            out.append(hit["sdl"] if hit else s)
            continue
        pad = pads.hid_pad(s)
        idx = index_of(joys, pad) if joys is not None and pad else None
        if idx is None and pad and joys is None and len(order) == 1:
            idx = 0                                         # jedyny pad w systemie
        log.info("SDL: pad HID %s → SDL-%s", pad.name if pad else s, idx)
        out.append(idx)
    return out
