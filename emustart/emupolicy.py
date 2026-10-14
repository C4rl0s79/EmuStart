"""emupolicy — wymagane ustawienia emulatorów, ustalane na serwerze dla wszystkich komputerów.

Serwer (panel → Emulatory → Wymagane ustawienia) trzyma dla rodziny emulatora listę wartości;
komputery dostają ją razem z indeksem emulatorów (/v1/emulators) i nakładają tuż przed
uruchomieniem gry — po ustawieniach profilu, więc wygrywają z nimi (emustart/launcher.py,
Adapter.apply_policy). Bez zmian w panelu obowiązują DEFAULTS: to, czego EmuStart potrzebuje,
żeby wyjście z gry, stany i podgląd stanów działały bez pytań emulatora.

Zapis w panelu, jedna wartość w wierszu:
    retroarch:   klucz = wartość                 (retroarch.cfg)
    pozostałe:   Sekcja|Klucz = wartość          (plik ini emulatora)
"""

from __future__ import annotations

import re

FAMILIES = {
    "retroarch": "RetroArch — retroarch.cfg (na czas gry)",
    "duckstation": "DuckStation — settings.ini",
    "pcsx2": "PCSX2 — inis\\PCSX2.ini",
    "dolphin": "Dolphin — User\\Config\\Dolphin.ini",
}

DEFAULTS = {
    "retroarch": {
        "pause_nonactive": "true",              # menu EmuStart w grze: RetroArch pauzuje się sam
        "savestate_thumbnail_enable": "true",   # obrazek stanu — podgląd w EmuStart
        "quit_on_close_content": "2",           # „Zamknij grę” = wyjście (uruchomienie z EmuStart)
        "savestate_auto_index": "true",         # każdy zapis stanu = nowy numer, nic się nie nadpisuje
        # pad (numery przycisków sterownika XInput, jak w autoconfig/xinput RetroArcha: Y=3, RB=5,
        # Start=6, Select (View)=7) — dlatego też sterownik
        "input_joypad_driver": "xinput",
        "input_menu_toggle_gamepad_combo": "4",  # Start + Select = menu RetroArcha
        "input_enable_hotkey_btn": "7",          # Select (View) = przycisk skrótów
        "input_toggle_fast_forward_btn": "5",    # Select + RB = przyspieszenie (wł./wył.)
        "input_save_state_btn": "3",             # Select + Y = zapis stanu
    },
    "duckstation": {"Main|ConfirmPowerOff": "false"},   # wyjście z gry bez okna z pytaniem
    "pcsx2": {"UI|ConfirmShutdown": "false"},
    "dolphin": {"Interface|ConfirmStop": "False"},
}

_KEY = re.compile(r"^[\w .-]+(\|[\w .-]+)?$")
_VALUE = re.compile(r'^[^"\r\n]*$')


def parse(family: str, text: str) -> tuple[dict, str]:
    """Tekst z panelu → ({klucz: wartość}, błąd). RetroArch bez sekcji, ini z sekcją."""
    out = {}
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            return {}, f"Wiersz {n}: brak „=”."
        k, v = (x.strip() for x in line.split("=", 1))
        v = v.strip('"')
        if not _KEY.match(k) or not _VALUE.match(v) or len(k) > 120 or len(v) > 500:
            return {}, f"Wiersz {n}: niedozwolone znaki."
        if (family == "retroarch") == ("|" in k):
            return {}, (f"Wiersz {n}: w RetroArchu sam klucz (bez sekcji)." if family == "retroarch"
                        else f"Wiersz {n}: podaj Sekcja|Klucz.")
        out[k] = v
    return out, ""


def format_values(values: dict) -> str:
    return "\n".join(f"{k} = {v}" for k, v in values.items())


def effective(cfg: dict) -> dict:
    """{rodzina: {klucz: wartość}} — ustawione w panelu albo domyślne."""
    own = cfg.get("emu_policy") or {}
    return {f: dict(own[f]) if isinstance(own.get(f), dict) else dict(DEFAULTS.get(f, {})) for f in FAMILIES}


def ini_changes(values: dict) -> dict:
    """{"Sekcja|Klucz": wartość} → {(sekcja, klucz): wartość} (ingame.ini_set)."""
    out = {}
    for k, v in values.items():
        if "|" in k:
            sec, key = k.split("|", 1)
            out[(sec.strip(), key.strip())] = v
    return out
