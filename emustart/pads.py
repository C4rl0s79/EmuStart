"""pads — kolejność padów (Gracz 1, Gracz 2…) niezależna od Windows.

Windows nadaje padom XInput numery (sloty 0–3) w kolejności podłączenia, więc pad
na USB, obecny od startu systemu, zawsze wyprzedza pad Bluetooth. EmuStart ustala
własną kolejność i przed startem gry przepina numery urządzeń w ustawieniach
emulatora (patrz ingame.Adapter.remap_pads), a po grze je przywraca.

Tryby:
  windows        — bez zmian,
  wireless_first — bezprzewodowe przed przewodowymi (rodzaj zasilania z XInput),
  manual         — ręcznie przypisane sloty (ekran „Kolejność padów”); jeśli
                   przypisany pad nie jest podłączony, reszta idzie wg windows.

Numery SDL w DuckStation/PCSX2 („SDL-0”) dla padów Xbox odpowiadają slotom XInput
(SDL nadaje im indeks gracza = slot), więc jedna kolejność obsługuje wszystkie
emulatory.

Pady HID, które nie są padami XInput (PS, Switch Pro, GameSir / 8BitDo w trybie Bluetooth
HID — hidpad.py), też są na liście: zamiast numeru slotu mają tożsamość „hid:VID:PID:…”
(pole `slot` to wtedy napis). Domyślnie idą po padach XInput; pad jedyny w systemie albo
ustawiony ręcznie jako pierwszy jest Graczem 1 — adaptery emulatorów przepinają go wtedy
po swojemu (ingame.*.remap_pads, RetroArch.launch_args).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from emustart import hidpad, xinput

BATTERY_TYPE = {0: "disconnected", 1: "wired", 2: "alkaline", 3: "nimh", 0xFF: "unknown"}
BATTERY_LEVEL = {0: "pusta", 1: "niski", 2: "średni", 3: "pełny"}


class _Battery(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


def _battery(slot: int) -> tuple:
    dll = xinput._dll
    if not dll or not hasattr(dll, "XInputGetBatteryInformation"):
        return "unknown", None
    b = _Battery()
    if dll.XInputGetBatteryInformation(wintypes.DWORD(slot), 0, ctypes.byref(b)) != 0:
        return "unknown", None
    return BATTERY_TYPE.get(b.BatteryType, "unknown"), b.BatteryLevel


def is_hid(slot) -> bool:
    return isinstance(slot, str) and slot.startswith("hid:")


def hid_pad(slot):
    """hidpad.Pad dla tożsamości „hid:…” (None, gdy niepodłączony)."""
    return next((p for p in hidpad.pads() if p.id == slot), None) if is_hid(slot) else None


def connected(battery: bool = True) -> list:
    """[{slot, wireless, battery}] podłączonych padów: XInput (slot 0–3), potem HID (slot „hid:…”).

    `battery=False` pomija zapytanie o baterię — przy odbiorniku Xbox to jedyne
    wywołanie, które idzie radiowo do samego pada, więc nie robimy go w pętli."""
    out = []
    for slot in range(4):
        if xinput.buttons(slot) is None:
            continue
        if not battery:
            out.append({"slot": slot, "wireless": False, "kind": "unknown", "battery": None})
            continue
        kind, level = _battery(slot)
        wireless = kind in ("alkaline", "nimh")
        out.append({"slot": slot, "wireless": wireless, "kind": kind,
                    "battery": BATTERY_LEVEL.get(level) if wireless else None})
    for p in hidpad.pads():
        out.append({"slot": p.id, "wireless": p.bluetooth, "kind": "hid", "battery": None, "hid": True,
                    "name": p.name, "vid": p.vid, "pid": p.pid, "layout": p.layout})
    return out


def order(cfg: dict, pads: list | None = None) -> list:
    """Sloty XInput / tożsamości „hid:…” w kolejności graczy (indeks 0 = Gracz 1)."""
    pads = connected() if pads is None else pads
    slots = [p["slot"] for p in pads]
    po = cfg.get("pad_order") or {}
    mode = po.get("mode", "windows")
    if mode == "wireless_first":
        return [p["slot"] for p in sorted(pads, key=lambda p: (not p["wireless"], p["slot"]))]
    if mode == "manual":
        manual = [s for s in po.get("manual", []) if s in slots]
        return manual + [s for s in slots if s not in manual]
    return slots


def is_identity(order_: list) -> bool:
    """Bez przepinania: pady XInput w kolejności slotów, a pady HID (jeśli są) dopiero po nich —
    tak widzą je emulatory bez zmian w ustawieniach. Pad HID jako Gracz 1 (także jedyny) — przepinamy."""
    xi = [s for s in order_ if not is_hid(s)]
    return bool(xi) and all(s == i for i, s in enumerate(xi)) and order_[:len(xi)] == xi or not order_
