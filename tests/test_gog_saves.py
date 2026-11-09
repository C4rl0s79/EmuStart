"""Zapisy i ustawienia gier GOG: reguły (ludusavi-manifest, poprawki serwera, ręczne gracza), rozwijanie ścieżek,
zbiory plików i synchronizacja przez serwer EmuStart między dwoma urządzeniami (emustart/saverules.py,
emustart/gogsaves.py, syncclient.py). Dane testowe w katalogach tymczasowych; żadnych zapytań do GOG."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

from emustart import gogsaves, paths, saverules, savesync, syncclient

from test_core import _write, env  # noqa: F401  (fixture)
from test_savesync_server import srv  # noqa: F401  (fixture)
from test_syncclient import PC, TEL, net  # noqa: F401  (fixture)

PID = "1207659157"

MANIFEST_YAML = """
Omerta - City of Gangsters:
  files:
    <winAppData>/omerta/saves:
      tags: [save]
      when: [{os: windows}]
    <winDocuments>/Omerta/settings.ini:
      tags: [config]
    <xdgData>/omerta:
      tags: [save]
      when: [{os: linux}]
    <base>/steam_only:
      tags: [save]
      when: [{store: steam}]
  registry:
    HKEY_CURRENT_USER/Software/Kalypso/Omerta:
      tags: [config]
  gog:
    id: 1207659157
Bundle One:
  files:
    <base>/one: {tags: [save]}
  id:
    gogExtra: [555]
Bundle Two:
  files:
    <base>/two: {tags: [save]}
  id:
    gogExtra: [555]
Main Game:
  files:
    <base>/main: {tags: [save]}
  gog: {id: 777}
Main Game DLC Pack:
  files:
    <base>/pack: {tags: [save]}
  id: {gogExtra: [777]}
Nickname:
  alias: Main Game
"""


@pytest.fixture()
def rules_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "srvdata")
    monkeypatch.setattr(saverules, "MIN_ENTRIES", 1)
    monkeypatch.setattr(saverules, "_index", None)
    saverules.install(MANIFEST_YAML.encode(), etag="e1")
    return tmp_path


# ── reguły na serwerze ──

def test_match_by_gog_id_only(rules_dir):
    r = saverules.rules_for(PID)
    assert r["source"] == "ludusavi" and r["title"] == "Omerta - City of Gangsters"
    assert {f["path"] for f in r["files"]} >= {"<winAppData>/omerta/saves", "<winDocuments>/Omerta/settings.ini"}
    assert "ludusavi-manifest" in r["attribution"]
    # wpis z gog.id ma pierwszeństwo przed wpisami, które mają ten sam numer tylko w gogExtra
    assert saverules.rules_for("777")["title"] == "Main Game"
    # dwa wpisy wyłącznie z gogExtra — niejednoznaczne, bez zgadywania po tytule
    amb = saverules.rules_for("555")
    assert amb["source"] == "ambiguous" and amb["candidates"] == ["Bundle One", "Bundle Two"] and not amb["files"]
    assert saverules.rules_for("555", "Bundle Two")["title"] == "Bundle Two"           # wybór gracza z kandydatów
    assert saverules.rules_for("555", "Omerta - City of Gangsters")["source"] == "ambiguous"   # spoza kandydatów
    assert saverules.rules_for("999")["source"] == "none"
    with pytest.raises(ValueError):
        saverules.rules_for("../x")


def test_server_override_beats_manifest_and_survives_update(rules_dir):
    (paths.DATA / "saverules" / "overrides.json").write_text(json.dumps(
        {PID: {"title": "Omerta (poprawka)", "files": {"<base>/SaveGames": {"tags": ["save"]}}}}), encoding="utf-8")
    r = saverules.rules_for(PID)
    assert r["source"] == "server" and r["files"][0]["path"] == "<base>/SaveGames"
    saverules.install(MANIFEST_YAML.replace("omerta/saves", "omerta/new").encode(), etag="e2")
    assert saverules.rules_for(PID)["source"] == "server"                               # aktualizacja nie rusza poprawki


def test_update_keeps_previous_copy_on_errors(rules_dir):
    before = saverules.rules_for(PID)
    assert saverules.update(force=True, fetch=lambda u, e: (304, b"", e))["changed"] is False
    bad = saverules.update(force=True, fetch=lambda u, e: (200, b"to: [nie manifest", "e3"))
    assert not bad["ok"] and saverules.rules_for(PID) == before                        # uszkodzona wersja nie podmienia
    saverules.update(force=True, fetch=lambda u, e: (_ for _ in ()).throw(OSError("brak sieci")))
    assert saverules.rules_for(PID) == before
    ok = saverules.update(force=True, fetch=lambda u, e: (200, MANIFEST_YAML.replace("omerta/saves", "omerta/v2").encode(), "e4"))
    assert ok["changed"] and (paths.DATA / "saverules" / "gog-index.prev.json").is_file()
    assert saverules.update()["changed"] is False                                       # świeża kopia — bez pobierania


# ── rozwijanie ścieżek, warunki, save/config ──

def folders(root: Path, user: str) -> dict:
    h = root / "Users" / user
    return {"home": str(h), "winAppData": str(h / "AppData" / "Roaming"), "winLocalAppData": str(h / "AppData" / "Local"),
            "winLocalAppDataLow": str(h / "AppData" / "LocalLow"), "winDocuments": str(root / "Dokumenty-" / user),
            "winPublic": str(root / "Public"), "winProgramData": str(root / "ProgramData"), "winDir": str(root / "Windows")}


def test_expansion_depends_on_user_and_install_dir(tmp_path):
    r = {"files": [{"path": "<winAppData>/omerta/saves", "tags": ["save"], "when": [{"os": "windows"}]},
                   {"path": "<base>/save/*.sav", "tags": ["save"], "when": []},
                   {"path": "<winDocuments>/Omerta/settings.ini", "tags": ["config"], "when": []}]}
    a = gogsaves.plan(PID, str(tmp_path / "GryA" / "Omerta"), r, True, folders(tmp_path, "ala"))
    b = gogsaves.plan(PID, str(tmp_path / "D" / "GOG" / "Omerta"), r, True, folders(tmp_path, "bob"))
    fa = {s.template: s for s in a["sets"]}
    fb = {s.template: s for s in b["sets"]}
    assert fa["<winAppData>/omerta/saves"].folder == tmp_path / "Users" / "ala" / "AppData" / "Roaming" / "omerta" / "saves"
    assert fb["<winAppData>/omerta/saves"].folder == tmp_path / "Users" / "bob" / "AppData" / "Roaming" / "omerta" / "saves"
    assert fa["<base>/save/*.sav"].folder == tmp_path / "GryA" / "Omerta" / "save" and fa["<base>/save/*.sav"].parts == ["*.sav"]
    assert fb["<base>/save/*.sav"].folder == tmp_path / "D" / "GOG" / "Omerta" / "save"
    # ten sam zbiór na obu komputerach (identyfikator z szablonu reguły, nie ze ścieżki)
    assert {s.core_id for s in a["sets"]} == {s.core_id for s in b["sets"]}
    ini = fa["<winDocuments>/Omerta/settings.ini"]
    assert ini.kind == "config" and ini.parts == ["settings.ini"]                       # plik, którego jeszcze nie ma
    assert {s.kind for s in a["sets"]} == {"saves", "config"}


def test_conditions_tags_config_option_and_registry(tmp_path):
    r = {"files": [{"path": "<xdgData>/x", "tags": ["save"], "when": [{"os": "linux"}]},
                   {"path": "<base>/steam", "tags": ["save"], "when": [{"store": "steam"}]},
                   {"path": "<base>/dos", "tags": ["save"], "when": [{"os": "dos"}]},
                   {"path": "<base>/gog", "tags": ["save"], "when": [{"os": "windows", "store": "gog"}]},
                   {"path": "<base>/cfg", "tags": ["config"], "when": []},
                   {"path": "<base>/both", "tags": ["save", "config"], "when": []},
                   {"path": "<storeUserId>/<unknownThing>", "tags": ["save"], "when": []}],
         "registry": [{"path": "HKEY_CURRENT_USER/Software/X", "tags": ["save"], "when": []}]}
    p = gogsaves.plan(PID, str(tmp_path / "G"), r, False, folders(tmp_path, "ala"))
    assert sorted(s.template for s in p["sets"]) == ["<base>/both", "<base>/dos", "<base>/gog"]
    assert all(s.kind == "saves" for s in p["sets"])
    reasons = {s["path"]: s["reason"] for s in p["skipped"]}
    assert reasons["<base>/cfg"] == "ustawienia wyłączone"
    assert "niedostępny" in reasons["<storeUserId>/<unknownThing>"]
    assert p["registry"] and "wymaga obsługi rejestru" in p["registry"][0]["reason"]   # nie udajemy synchronizacji
    p2 = gogsaves.plan(PID, str(tmp_path / "G"), r, True, folders(tmp_path, "ala"))
    assert "<base>/cfg" in {s.template for s in p2["sets"]}


def test_whole_system_folders_are_never_a_set(tmp_path):
    r = {"files": [{"path": "<winAppData>", "tags": ["save"]}, {"path": "<winDocuments>/*", "tags": ["save"]},
                   {"path": "<home>/**", "tags": ["save"]}, {"path": "<winDocuments>/*.sav", "tags": ["save"]}]}
    p = gogsaves.plan(PID, str(tmp_path / "G"), r, True, folders(tmp_path, "ala"))
    assert [s.template for s in p["sets"]] == ["<winDocuments>/*.sav"]                  # wzorzec plików — dozwolony
    assert len(p["skipped"]) == 3


def test_patterns_folders_links_and_safe_paths(tmp_path):
    base = tmp_path / "G"
    _write(base / "save" / "slot1.sav", b"1")
    _write(base / "save" / "slot2.sav", b"2")
    _write(base / "save" / "readme.txt", b"x")
    _write(base / "profiles" / "p1" / "deep" / "a.dat", b"A")
    _write(base / "profiles" / "p2" / "b.dat", b"B")
    r = {"files": [{"path": "<base>/save/*.sav", "tags": ["save"]}, {"path": "<base>/profiles/*", "tags": ["save"]}]}
    p = gogsaves.plan(PID, str(base), r, True, folders(tmp_path, "ala"))
    sav, prof = p["sets"]
    assert [a for _p, a in sav.files] == ["slot1.sav", "slot2.sav"]
    assert [a for _p, a in prof.files] == ["p1/deep/a.dat", "p2/b.dat"]                 # pasujący folder — w całości
    for bad in ("../evil.sav", "..\\evil.sav", "/abs.sav", "C:/x.sav", "a/../../b.sav", "notmatching.txt"):
        with pytest.raises(ValueError):
            sav.local_path(bad)
    assert sav.local_path("slot9.sav") == base / "save" / "slot9.sav"
    if os.name == "nt":                                   # punkt połączenia w zbiorze — nie idziemy za nim
        outside = tmp_path / "poza"
        _write(outside / "secret.dat", b"S")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(base / "profiles" / "link"), str(outside)],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)
        prof.collect()
        assert "link/secret.dat" not in [a for _p, a in prof.files]
        with pytest.raises(ValueError):
            prof.local_path("link/secret.dat")


def test_user_rules_have_priority_and_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "dane")

    class Src:
        calls = 0

        def save_rules(self, pid, pick=""):
            Src.calls += 1
            return {"source": "ludusavi", "title": "Omerta", "files": [{"path": "<base>/s", "tags": ["save"], "when": []}]}
    assert gogsaves.rules(PID, Src())["source"] == "ludusavi"
    assert gogsaves.rules(PID, Src())["source"] == "ludusavi" and Src.calls == 1         # świeża kopia — bez pytania
    gogsaves.save_settings(PID, rules={"files": [{"path": "<winDocuments>/Moje/*.sav", "tags": ["save"]}]})
    r = gogsaves.rules(PID, Src())
    assert r["source"] == "user" and r["files"][0]["path"] == "<winDocuments>/Moje/*.sav"
    for bad in ({"files": [{"path": "../x"}]}, {"files": [{"path": "<cokolwiek>/x"}]}, {"files": []}):
        with pytest.raises(gogsaves.RulesError):
            gogsaves.save_settings(PID, rules=bad)
    gogsaves.save_settings(PID, rules=None)

    class Down:
        def save_rules(self, pid, pick=""):
            raise OSError("serwer niedostępny")
    r = gogsaves.rules(PID, Down(), refresh=True)                                       # offline — ostatnia kopia
    assert r["source"] == "ludusavi" and r["offline"]


# ── synchronizacja przez serwer EmuStart: dwa urządzenia ──

RULES = {"files": [{"path": "<winAppData>/omerta/saves", "tags": ["save"], "when": [{"os": "windows"}]},
                   {"path": "<winDocuments>/Omerta/settings.ini", "tags": ["config"], "when": []}]}


@pytest.fixture()
def two(net, monkeypatch):
    cfgs, tmp = net
    calls = []
    real = urllib.request.urlopen
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or real(*a, **k))

    def sets(who: int, config: bool = False) -> list:
        user = "ala" if who == PC else "bob"
        inst = tmp / ("C" if who == PC else "D") / "GOG Games" / "Omerta"
        return gogsaves.plan(PID, str(inst), RULES, config, folders(tmp / f"dev{who}", user))["sets"]

    def save_dir(who: int) -> Path:
        user = "ala" if who == PC else "bob"
        return tmp / f"dev{who}" / "Users" / user / "AppData" / "Roaming" / "omerta" / "saves"
    yield cfgs, tmp, sets, save_dir
    assert not [c for c in calls if "gog.com" in str(c)]                                # żadnych zapytań do GOG


def test_vertical_save_moves_to_second_device(two):
    cfgs, tmp, sets, save_dir = two
    _write(save_dir(PC) / "slot1.sav", b"ZAPIS-PC")
    _write(save_dir(PC) / "auto" / "quick.sav", b"QUICK")
    r = gogsaves.before(cfgs[PC], PC, PID, sets(PC))
    assert r.uploaded == 1 and not r.conflicts
    # drugi komputer: inny użytkownik i inny katalog instalacji, katalog zapisów jeszcze nie istnieje
    assert not save_dir(TEL).exists()
    r = gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))
    assert r.downloaded == 1
    assert (save_dir(TEL) / "slot1.sav").read_bytes() == b"ZAPIS-PC"
    assert (save_dir(TEL) / "auto" / "quick.sav").read_bytes() == b"QUICK"
    # gra na drugim → po grze na serwer → pierwszy pobiera
    _write(save_dir(TEL) / "slot1.sav", b"ZAPIS-TEL")
    assert gogsaves.after(cfgs[TEL], TEL, PID, sets(TEL)).uploaded == 1
    assert gogsaves.before(cfgs[PC], PC, PID, sets(PC)).downloaded == 1
    assert (save_dir(PC) / "slot1.sav").read_bytes() == b"ZAPIS-TEL"
    # na serwerze tylko ścieżki względne zbioru, rodzaj saves, emulator gog
    man = savesync.manifest("Jezus", "pc", "gog", PID, urls=False)
    core = next(iter(man["emulators"]["gog"]["cores"]))
    assert man["emulators"]["gog"]["cores"][core]["bundles"]["saves"]["revision"] == 2


def test_config_is_separate_and_off_by_default(two):
    cfgs, tmp, sets, save_dir = two
    ini = tmp / f"dev{PC}" / "Dokumenty-" / "ala" / "Omerta" / "settings.ini"
    _write(ini, b"[video]\nw=1920")
    _write(save_dir(PC) / "s.sav", b"S")
    assert gogsaves.before(cfgs[PC], PC, PID, sets(PC)).uploaded == 1                  # tylko zapisy
    r = gogsaves.before(cfgs[PC], PC, PID, sets(PC, config=True))
    assert r.uploaded == 1                                                              # teraz ustawienia
    gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL, config=True))
    assert (tmp / f"dev{TEL}" / "Dokumenty-" / "bob" / "Omerta" / "settings.ini").read_bytes() == b"[video]\nw=1920"
    man = savesync.manifest("Jezus", "pc", "gog", PID, urls=False)
    kinds = {k for c in man["emulators"]["gog"]["cores"].values() for k in c["bundles"]}
    assert kinds == {"saves", "config"}


def test_offline_change_is_pending_then_sent(two):
    cfgs, tmp, sets, save_dir = two
    _write(save_dir(PC) / "s.sav", b"V1")
    gogsaves.before(cfgs[PC], PC, PID, sets(PC))
    _write(save_dir(PC) / "s.sav", b"OFFLINE")
    off = {k: v for k, v in cfgs[PC].items() if k != "dev"}                             # bez połączenia z serwerem
    r = gogsaves.after(off, PC, PID, sets(PC))
    assert r.offline and r.uploaded == 0
    assert gogsaves.local_state(PC, PID, sets(PC))["pending"] == 1
    assert syncclient.retry(cfgs[PC]) == 1                                              # w tle, po powrocie sieci
    gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))
    assert (save_dir(TEL) / "s.sav").read_bytes() == b"OFFLINE"


def test_both_sides_changed_is_a_conflict_and_backup_restores(two):
    cfgs, tmp, sets, save_dir = two
    _write(save_dir(PC) / "s.sav", b"V1")
    gogsaves.before(cfgs[PC], PC, PID, sets(PC))
    gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))
    _write(save_dir(PC) / "s.sav", b"PC")
    gogsaves.after(cfgs[PC], PC, PID, sets(PC))
    _write(save_dir(TEL) / "s.sav", b"TEL")
    r = gogsaves.after(cfgs[TEL], TEL, PID, sets(TEL))
    assert r.uploaded == 0 and len(r.conflicts) == 1                                    # serwer nie nadpisany (409)
    r = gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))
    assert len(r.conflicts) == 1 and (save_dir(TEL) / "s.sav").read_bytes() == b"TEL"  # nic bez decyzji gracza
    gogsaves.resolve(cfgs[TEL], TEL, PID, r.conflicts, "remote")
    assert (save_dir(TEL) / "s.sav").read_bytes() == b"PC"
    bak = gogsaves.backups(TEL, PID)
    assert bak and bak[0]["sets"]
    assert gogsaves.restore_backup(TEL, PID, bak[0]["id"], sets(TEL)) == 1
    assert (save_dir(TEL) / "s.sav").read_bytes() == b"TEL"                             # wersja z kopii wróciła


def test_empty_scan_never_deletes_server_saves(two):
    cfgs, tmp, sets, save_dir = two
    _write(save_dir(PC) / "s.sav", b"V1")
    gogsaves.before(cfgs[PC], PC, PID, sets(PC))
    import shutil
    shutil.rmtree(save_dir(PC))                                                         # dysk odłączony / folder zniknął
    assert gogsaves.after(cfgs[PC], PC, PID, sets(PC)).uploaded == 0
    gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))
    assert (save_dir(TEL) / "s.sav").read_bytes() == b"V1"


def test_interrupted_download_and_upload_change_nothing(two, monkeypatch):
    cfgs, tmp, sets, save_dir = two
    _write(save_dir(PC) / "s.sav", b"V1")
    gogsaves.before(cfgs[PC], PC, PID, sets(PC))
    _write(save_dir(TEL) / "s.sav", b"OLD")
    gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))                                     # konflikt pierwszej synchronizacji
    gogsaves.resolve(cfgs[TEL], TEL, PID, gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL)).conflicts, "remote")
    _write(save_dir(PC) / "s.sav", b"V2")
    gogsaves.after(cfgs[PC], PC, PID, sets(PC))
    # pobieranie przerwane (uszkodzona paczka) — pliki na drugim urządzeniu nietknięte
    real_fetch = syncclient._fetch
    monkeypatch.setattr(syncclient, "_fetch", lambda cl, srv: (_ for _ in ()).throw(syncclient.Offline("przerwane")))
    r = gogsaves.before(cfgs[TEL], TEL, PID, sets(TEL))
    assert r.offline and (save_dir(TEL) / "s.sav").read_bytes() == b"V1"
    monkeypatch.setattr(syncclient, "_fetch", real_fetch)
    # wysyłanie przerwane po init — wersja na serwerze bez zmian, zmiana czeka
    _write(save_dir(PC) / "s.sav", b"V3")
    real_call = syncclient._Client.call

    def broken(self, method, url, *a, **k):
        if method == "PUT":
            raise syncclient.Offline("połączenie zerwane")
        return real_call(self, method, url, *a, **k)
    monkeypatch.setattr(syncclient._Client, "call", broken)
    r = gogsaves.after(cfgs[PC], PC, PID, sets(PC))
    assert r.offline and r.uploaded == 0
    monkeypatch.setattr(syncclient._Client, "call", real_call)
    man = savesync.manifest("Jezus", "pc", "gog", PID, urls=False)
    assert next(iter(man["emulators"]["gog"]["cores"].values()))["bundles"]["saves"]["revision"] == 2
    assert gogsaves.local_state(PC, PID, sets(PC))["pending"] == 1


def test_server_keys_for_gog(tmp_path, monkeypatch):
    with pytest.raises(savesync.SyncError):
        savesync._key("gog", "", "saves")                                               # zbiór wymagany
    with pytest.raises(savesync.SyncError):
        savesync._key("gog", "s1", "states")
    with pytest.raises(savesync.SyncError):
        savesync._key("pcsx2", "", "config")                                            # config tylko dla GOG
    assert savesync._key("gog", "s1", "config") == ("gog", "s1", "config")
