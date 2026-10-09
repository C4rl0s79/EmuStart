"""Wspólny interfejs (web/) na Windows i Androidzie: każda funkcja, którą woła interfejs,
musi istnieć w emustart/api.py i być obsłużona w aplikacji na Androida (Bridge.kt) —
albo świadomie oznaczona jako tylko dla komputera (WINDOWS_ONLY, z powodem). Nowa funkcja
w interfejsie bez tej decyzji = błąd testu, zamiast cichego „nic” na telefonie."""

from __future__ import annotations

import re
from pathlib import Path

from emustart import api

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "android" / "app" / "src" / "main" / "java" / "io" / "github" / "c4rl0s79" / "emustart" / "Bridge.kt"

# tylko w EmuStart na komputerze — na telefonie niedostępne z menu (Android buduje własne
# menu i ustawienia) albo zablokowane w interfejsie; Bridge odpowiada komunikatem „na komputerze”
WINDOWS_ONLY = {
    # grafiki i metadane: robi je serwer
    "art_cancel", "art_candidates", "art_choose", "art_clear", "art_overview", "art_start",
    "import_pylinks_keys", "launchbox_status", "launchbox_update", "meta_save",
    # emulatory na komputerze, skanowanie folderów
    "autodetect", "emulator_options", "install_cancel", "install_missing", "install_start",
    "install_status", "scan_status", "set_game_emulator",
    # opcje gier i systemów (openGameOptions / openSystemOptions zablokowane na Androidzie)
    "game_options", "system_logo_candidates", "system_logo_choose", "system_options",
    "system_rescan", "system_set",
    # menu w grze i pady Windows (XInput), RetroAchievements w emulatorach na komputerze
    "ingame_action", "pads_set", "pads_state", "ui_pad_poll",
    "ra_hardcore", "ra_import", "ra_login", "ra_logout", "ra_status",
    # klucz serwera ze schowka Windows (na telefonie: pole tekstowe)
    "server_paste_key",
}


def _ui_calls() -> set:
    calls = set()
    for f in (ROOT / "web").glob("*.js"):
        calls |= set(re.findall(r"api\(\)\.([a-z_]+)", f.read_text(encoding="utf-8")))
    return calls


def _android() -> set:
    src = BRIDGE.read_text(encoding="utf-8")
    body = src[src.index("private fun dispatch"):src.index("// ── stan i listy")]
    out = set()
    for line in body.splitlines():
        if "->" in line:
            out |= set(re.findall(r'"([a-z_]+)"', line.split("->", 1)[0]))
    return out


def test_every_ui_call_exists_on_windows():
    missing = sorted(c for c in _ui_calls() if not callable(getattr(api.Api, c, None)))
    assert not missing, f"interfejs woła funkcje, których nie ma w emustart/api.py: {missing}"


def test_every_ui_call_handled_on_android_or_marked_windows_only():
    android = _android()
    missing = sorted(_ui_calls() - android - WINDOWS_ONLY)
    assert not missing, ("interfejs woła funkcje bez obsługi na Androidzie — dodaj je w Bridge.kt "
                         f"albo do WINDOWS_ONLY (z powodem i blokadą w interfejsie): {missing}")


def test_windows_only_list_is_current():
    stale = sorted(WINDOWS_ONLY - _ui_calls())
    assert not stale, f"WINDOWS_ONLY zawiera funkcje, których interfejs już nie woła: {stale}"
    both = sorted(WINDOWS_ONLY & _android())
    assert not both, f"obsłużone na Androidzie, a oznaczone jako tylko Windows: {both}"


def test_server_lists_every_core_for_platform():
    from emustart import emulators, installer
    assert emulators.cores_for("SNES")[:2] == ["snes9x", "bsnes"]       # domyślny pierwszy
    for plat, default in installer.CORES.items():
        cores = emulators.cores_for(plat)
        assert cores[0] == default and len(cores) == len(set(cores))
