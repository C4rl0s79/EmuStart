"""Menedżer zapisów: stany tej gry z komputera i telefonu, zgodność stanów PS2
(wersja formatu z .p2s), kopiowanie między platformami."""

from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

from emustart import nasfs, savemgr


def _p2s(version: int) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("PCSX2 Savestate Version.id", struct.pack("<I", version))
        z.writestr("eeMemory.bin", b"\0" * 64)
    return b.getvalue()


def _w(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


V = (0x9A59 << 16)


def _nas(tmp_path):
    nas = tmp_path / "nas"
    g = nas / "Jezus"
    # RetroArch: komputer (podfolder states/<rdzeń>), telefon (płasko)
    _w(g / "save/retroarch/states/SwanStation/Crash (USA).state1", b"pc1")
    _w(g / "save/retroarch/states/SwanStation/Crash (USA).state1.png", b"png")
    _w(g / "save/retroarch/states/SwanStation/Inna gra.state1", b"x")
    _w(g / "states/android/retroarch/Crash (USA).state.auto", b"tel")
    _w(g / "save/retroarch/saves/SwanStation/Crash (USA).srm", b"s" * 10)
    # PS2: komputer (PCSX2), telefon (ARMSX2) — numer płyty z metadanych
    _w(g / "save/pcsx2/sstates/SLUS-20431 (ABCD1234).01.p2s", _p2s(V))
    _w(g / "save/pcsx2/sstates/SLUS-99999 (FFFF0000).01.p2s", _p2s(V))
    _w(g / "states/android/armsx2/SLUS-20431 (ABCD1234).03.p2s", _p2s(V - 1))
    _w(g / "save/pcsx2/memcards/Mcd001.ps2", b"c" * 100)
    return nasfs.DirFs(nas)


def test_p2s_version(tmp_path):
    f = tmp_path / "a.p2s"
    _w(f, _p2s(V))
    assert savemgr.p2s_version(f) == V and savemgr.fmt_version(V) == "9A59.0000"
    _w(f, b"nie zip")
    assert savemgr.p2s_version(f) is None


def test_catalog_lists_both_platforms(tmp_path):
    fs = _nas(tmp_path)
    # stany RetroArcha i zapisy w grze — paczki z serwerem (syncclient), nie menedżer
    ra = savemgr.catalog(fs, "Jezus", "psx", "Crash (USA)", ["Crash (USA)"])
    assert ra["states"] == [] and ra["saves"] == []

    ps2 = savemgr.catalog(fs, "Jezus", "ps2", "Okage (USA)", ["Okage (USA)"], serial="SLUS-20431")
    st = {s["platform"] + "/" + s["emu"]: s for s in ps2["states"]}
    assert set(st) == {"windows/pcsx2", "android/armsx2"}
    pc = st["windows/pcsx2"]
    arm = next(t for t in pc["targets"] if t["emu"] == "armsx2")
    assert arm["ok"] is False                       # telefon ma starszy format niż ten stan
    back = next(t for t in st["android/armsx2"]["targets"] if t["emu"] == "pcsx2")
    assert back["ok"] is False and "inny format" in back["note"]


def test_same_version_is_compatible(tmp_path):
    fs = _nas(tmp_path)
    _w(tmp_path / "nas/Jezus/states/android/armsx2/SLUS-20431 (ABCD1234).03.p2s", _p2s(V))
    ps2 = savemgr.catalog(fs, "Jezus", "ps2", "Okage (USA)", [], serial="SLUS-20431")
    pc = next(s for s in ps2["states"] if s["platform"] == "windows")
    assert next(t for t in pc["targets"] if t["emu"] == "armsx2")["ok"] is True


def test_copy_pc_to_phone_ps2_updates_index(tmp_path):
    fs = _nas(tmp_path)
    r = savemgr.copy(fs, "Jezus", "save/pcsx2/sstates/SLUS-20431 (ABCD1234).01.p2s",
                     "android", "armsx2", "ps2", "Okage (USA)")
    assert r["ok"] and r["rel"] == "states/android/armsx2/SLUS-20431 (ABCD1234).01.p2s"
    idx = json.loads((tmp_path / "nas/Jezus/states/android/armsx2/.emustart-index.json").read_text())
    assert idx["ps2/Okage (USA)"] == ["SLUS-20431 (ABCD1234)"]
    # telefon widzi go potem po przedrostku z indeksu, bez numeru płyty
    ps2 = savemgr.catalog(fs, "Jezus", "ps2", "Okage (USA)", [])
    assert any(s["platform"] == "android" and s["slot"] == "01" for s in ps2["states"])


def test_copy_rejects_bad_paths(tmp_path):
    fs = _nas(tmp_path)
    assert not savemgr.copy(fs, "Jezus", "../x", "android", "retroarch", "a", "b")["ok"]
    assert not savemgr.copy(fs, "Jezus", "save/x", "android", "nieznany", "a", "b")["ok"]


def test_server_endpoints(tmp_path, monkeypatch):
    import urllib.request
    from emustart import remote, server
    fs = _nas(tmp_path)

    class Api:
        _cfg = {"server_token": "k"}
    monkeypatch.setattr(remote, "API", Api())
    monkeypatch.setattr(remote, "NAS_ROOT", fs.root)
    monkeypatch.setattr(remote, "_savemgr_game", lambda es, name: ([name], "SLUS-20431"))
    base = server.start(0)
    h = {"Authorization": "Bearer k"}
    q = "profile=Jezus&es=ps2&name=Okage%20(USA)"
    with urllib.request.urlopen(urllib.request.Request(base + "/v1/savemgr/catalog?" + q, headers=h)) as r:
        cat = json.loads(r.read())
    assert len(cat["states"]) == 2
    src = urllib.parse.quote("save/pcsx2/sstates/SLUS-20431 (ABCD1234).01.p2s")
    req = urllib.request.Request(base + f"/v1/savemgr/copy?{q}&src={src}&platform=android&emu=armsx2",
                                 data=b"", method="PUT", headers=h)
    with urllib.request.urlopen(req) as r:
        assert json.loads(r.read())["ok"]
    bad = urllib.request.Request(base + "/v1/savemgr/catalog?profile=..&es=a&name=b", headers=h)
    try:
        urllib.request.urlopen(bad)
        assert False
    except urllib.error.HTTPError as ex:
        assert ex.code == 400


def test_armsx2_autosave_listed_and_copied_as_resume(tmp_path):
    """ARMSX2 zapisuje stan przy wyjściu jako .autosave.p2s (wczytywany przy starcie) —
    menedżer go pokazuje, a kopia na komputer trafia jako .resume.p2s PCSX2 (i odwrotnie)."""
    fs = _nas(tmp_path)
    tel = tmp_path / "nas/Jezus/states/android/armsx2"
    _w(tel / "SLUS-20851 (39B574F0).autosave.p2s", _p2s(V))
    _w(tel / "SLUS-20851 (39B574F0).autosave.2.p2s", _p2s(V))
    _w(tel / "SLUS-20851 (39B574F0).autosave.p2s.backup", b"x")
    _w(tel / "SLUS-20851 (39B574F0).00.p2s", _p2s(V))
    cat = savemgr.catalog(fs, "Jezus", "ps2", "Ace Combat 5 (USA)", [], serial="SLUS-20851")
    slots = sorted(s["slot"] for s in cat["states"] if s["platform"] == "android")
    assert slots == ["00", "autosave", "autosave.2"]                  # bez .backup
    r = savemgr.copy(fs, "Jezus", "states/android/armsx2/SLUS-20851 (39B574F0).autosave.p2s",
                     "windows", "pcsx2", "ps2", "Ace Combat 5 (USA)")
    assert r["ok"] and r["rel"] == "save/pcsx2/sstates/SLUS-20851 (39B574F0).resume.p2s"
    back = savemgr.copy(fs, "Jezus", r["rel"], "android", "armsx2", "ps2", "Ace Combat 5 (USA)")
    assert back["rel"].endswith(".autosave.p2s")
    two = savemgr.copy(fs, "Jezus", "states/android/armsx2/SLUS-20851 (39B574F0).autosave.2.p2s",
                       "windows", "pcsx2", "ps2", "Ace Combat 5 (USA)")
    assert two["rel"].endswith(".autosave.2.p2s")                     # starsze autozapisy bez zmiany nazwy


def test_retroarch_learns_state_prefix():
    from emustart import ingame
    ra = ingame.RetroArch.__new__(ingame.RetroArch)
    assert ra.prefix_of("Chrono_Trigger_(USA)_(MSU1).state1") == "Chrono_Trigger_(USA)_(MSU1).state"
    assert ra.prefix_of("Gra.state.auto") == "Gra.state"
    assert ra.prefix_of("Gra.state1.png") == ""
