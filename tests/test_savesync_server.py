"""Synchronizacja zapisów — serwer (emustart/savesync.py): wersje, konflikty, podpisane adresy,
konto z tokenu sesji, rdzenie RetroArcha osobno, kopie zapasowe."""

import hashlib
import http.client
import io
import json
import time
import zipfile

import pytest

from emustart import accounts, devices, remote, savesync, server

DEVICES = {"pc": "Jezus", "tel": "Jezus", "ola-pc": "Ola"}


@pytest.fixture()
def srv(monkeypatch):
    class A:
        _cfg = {"profile_secret": "sekret-testowy"}
    monkeypatch.setattr(remote, "API", A())

    def authorized(handler):                 # urządzenie z nagłówka testu (zamiast logowania podpisem)
        did = (handler.headers.get("Authorization") or "")[len("Bearer "):]
        if did not in DEVICES:
            return False
        handler.device = did
        return True
    monkeypatch.setattr(remote, "authorized", authorized)
    monkeypatch.setattr(devices, "account_raw", lambda did: DEVICES.get(did, ""))
    url = server.start(0, bind="127.0.0.1")
    host, port = url.removeprefix("http://").split(":")
    return host, int(port)


def req(srv, method, path, dev="pc", body=None, raw=None, headers=None):
    c = http.client.HTTPConnection(*srv, timeout=10)
    h = {"Authorization": f"Bearer {dev}", **(headers or {})}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def bundle(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def upload(srv, files, base, dev="pc", game="g1", emu="retroarch", core="snes9x", kind="saves", **extra):
    data = bundle(files) if isinstance(files, dict) else files
    sha = hashlib.sha256(data).hexdigest()
    st, init = req(srv, "POST", "/v1/savesync/upload/init", dev, {
        "platform_id": "snes", "game_id": game, "emulator_id": emu, "core_id": core, "bundle_kind": kind,
        "base_revision": base, "sha256": sha, "size": len(data), **extra})
    if st != 200:
        return st, init
    st, put = req(srv, "PUT", init["upload_url"], dev, raw=data)
    if st != 200:
        return st, put
    return req(srv, "POST", "/v1/savesync/upload/commit", dev, {
        "upload_id": init["upload_id"], "base_revision": base, "sha256": sha, "size": len(data), **extra})


def test_upload_manifest_download(srv):
    st, r = upload(srv, {"Gra.srm": b"SRAM-1"}, 0)
    assert st == 200 and r["revision"] == 1
    st, m = req(srv, "GET", "/v1/savesync/snes/g1/manifest")
    b = m["emulators"]["retroarch"]["cores"]["snes9x"]["bundles"]["saves"]
    assert b["revision"] == 1 and b["file_count"] == 1 and b["updated_by_device_id"] == "pc"
    assert "history" not in b and "savesync" not in b["download_url"]          # bez ścieżek serwera
    st, data = req(srv, "GET", b["download_url"], "tel")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read("Gra.srm") == b"SRAM-1"
    st, s = req(srv, "GET", "/v1/savesync/snes/g1/status")
    assert "download_url" not in json.dumps(s)


def test_stale_base_revision_is_conflict(srv):
    """Dwa urządzenia jednego konta zmieniły tę samą paczkę: drugie dostaje 409 — przy init
    i przy commit (gdy pierwsze zatwierdziło w międzyczasie)."""
    assert upload(srv, {"Gra.srm": b"A"}, 0)[0] == 200
    st, r = upload(srv, {"Gra.srm": b"B"}, 0, dev="tel")
    assert st == 409 and r["conflict"] and r["current"]["revision"] == 1
    # wyścig: obaj zaczęli od rev 1, pierwszy zatwierdza, drugi dostaje 409 przy commit
    d1, d2 = bundle({"Gra.srm": b"PC"}), bundle({"Gra.srm": b"TEL"})
    inits = []
    for dev, data in (("pc", d1), ("tel", d2)):
        st, i = req(srv, "POST", "/v1/savesync/upload/init", dev, {
            "platform_id": "snes", "game_id": "g1", "emulator_id": "retroarch", "core_id": "snes9x",
            "bundle_kind": "saves", "base_revision": 1, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        assert st == 200
        assert req(srv, "PUT", i["upload_url"], dev, raw=data)[0] == 200
        inits.append((dev, i, data))
    commit = lambda dev, i, data: req(srv, "POST", "/v1/savesync/upload/commit", dev, {
        "upload_id": i["upload_id"], "base_revision": 1, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    assert commit(*inits[0])[0] == 200
    st, r = commit(*inits[1])
    assert st == 409 and r["current"]["revision"] == 2

    # decyzja gracza na telefonie: „zostaw z tego urządzenia” — wersja PC do historii
    st, r = req(srv, "POST", "/v1/savesync/snes/g1/resolve", "tel",
                {"choice": "local", "upload_id": inits[1][1]["upload_id"], "base_revision": 1})
    assert st == 200 and r["revision"] == 3
    hist = savesync.history("Jezus", "snes", "g1")[0]["history"]
    assert [h["revision"] for h in hist] == [2, 1]                               # nic nie zginęło


def test_bad_sha_bad_zip_and_paths_rejected(srv):
    data = bundle({"Gra.srm": b"X"})
    st, i = req(srv, "POST", "/v1/savesync/upload/init", "pc", {
        "platform_id": "snes", "game_id": "g1", "emulator_id": "retroarch", "core_id": "snes9x",
        "bundle_kind": "saves", "base_revision": 0, "sha256": "0" * 64, "size": len(data)})
    assert req(srv, "PUT", i["upload_url"], raw=data)[0] == 400                 # zły SHA-256
    for payload in (b"to nie jest zip", bundle({"../../wyjscie.srm": b"X"})):
        st, r = upload(srv, payload, 0)
        assert st == 400, r
    # przerwana wysyłka (niepełna treść) — bez commita, nic się nie zmienia
    data = bundle({"Gra.srm": b"Y" * 1000})
    st, i = req(srv, "POST", "/v1/savesync/upload/init", "pc", {
        "platform_id": "snes", "game_id": "g1", "emulator_id": "retroarch", "core_id": "snes9x",
        "bundle_kind": "saves", "base_revision": 0, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    st, r = req(srv, "PUT", i["upload_url"], raw=data[:100])
    assert st == 400
    st, r = req(srv, "POST", "/v1/savesync/upload/commit", "pc", {
        "upload_id": i["upload_id"], "base_revision": 0, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    assert st == 400 and req(srv, "GET", "/v1/savesync/snes/g1/status")[1]["revision"] == 0


def test_retroarch_cores_are_separate(srv):
    """Stany per rdzeń (niezgodne); zapisy w grze SNES wspólne dla Snes9x i bsnes (surowy SRAM)."""
    assert upload(srv, {"game.state1": b"snes9x"}, 0, core="snes9x", kind="states")[0] == 200
    assert upload(srv, {"game.state1": b"bsnes"}, 0, core="bsnes", kind="states")[0] == 200   # osobna paczka
    assert upload(srv, {"game.state1": b"snes9x-2"}, 1, core="snes9x", kind="states")[0] == 200
    cores = req(srv, "GET", "/v1/savesync/snes/g1/manifest")[1]["emulators"]["retroarch"]["cores"]
    st, data = req(srv, "GET", cores["bsnes"]["bundles"]["states"]["download_url"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read("game.state1") == b"bsnes"                                 # stany bsnes nietknięte
    # zapisy w grze: Snes9x i bsnes — jedna paczka; klient sprzed 0.47 widzi ją pod swoim rdzeniem
    assert upload(srv, {"game.srm": b"SRAM"}, 0, core="snes9x")[0] == 200
    st, r = upload(srv, {"game.srm": b"SRAM-bsnes"}, 0, core="bsnes")
    assert st == 409                                                             # ta sama paczka, rev 1
    cores = req(srv, "GET", "/v1/savesync/snes/g1/manifest")[1]["emulators"]["retroarch"]["cores"]
    assert cores["bsnes"]["bundles"]["saves"]["revision"] == cores["sram-snes"]["bundles"]["saves"]["revision"]
    assert upload(srv, {"game.srm": b"SRAM-bsnes"}, cores["bsnes"]["bundles"]["saves"]["revision"], core="bsnes")[0] == 200
    cores = req(srv, "GET", "/v1/savesync/snes/g1/manifest")[1]["emulators"]["retroarch"]["cores"]
    st, data = req(srv, "GET", cores["snes9x"]["bundles"]["saves"]["download_url"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read("game.srm") == b"SRAM-bsnes"
    # rdzeń spoza listy — zapisy jak dotąd per rdzeń
    assert upload(srv, {"game.srm": b"x"}, 0, core="nieznany_rdzen")[0] == 200
    assert upload(srv, {"x": b"1"}, 0, core="")[0] == 400                        # RetroArch bez rdzenia
    # PCSX2: karta pamięci osobna dla każdej gry, bez rdzenia
    assert upload(srv, {"Gra.ps2": b"karta"}, 0, game="g2", emu="pcsx2", core="", kind="memcards")[0] == 200
    assert "bundles" in req(srv, "GET", "/v1/savesync/snes/g2/manifest")[1]["emulators"]["pcsx2"]


def test_accounts_are_isolated(srv, monkeypatch):
    """Konto z tokenu sesji, nie z treści zapytania: urządzenie konta Ola nie widzi zapisów
    konta Jezus; podanie cudzego konta bez jego tokenu — 403."""
    assert upload(srv, {"Gra.srm": b"Jezusa"}, 0)[0] == 200
    st, m = req(srv, "GET", "/v1/savesync/snes/g1/manifest", "ola-pc")
    assert st == 200 and m["revision"] == 0                                      # puste konto Oli
    st, r = req(srv, "GET", "/v1/savesync/snes/g1/manifest", "ola-pc", headers={"X-EmuStart-Account": "Jezus"})
    assert st == 403
    st, r = upload(srv, {"Gra.srm": b"podmiana"}, 1, dev="ola-pc", account="Jezus")
    assert st == 403
    # Jezus zalogował się hasłem na komputerze Oli — token konta otwiera jego zapisy
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    assert not accounts.set_password("Jezus", "Haslo-Testowe-123!")
    tok = accounts.token(remote._secret(), "Jezus")
    assert tok
    st, m = req(srv, "GET", "/v1/savesync/snes/g1/manifest", "ola-pc",
                headers={"X-EmuStart-Account": "Jezus", "X-Profile-Token": tok})
    assert st == 200 and m["revision"] == 1


def test_urls_expire_and_cannot_be_forged(srv, monkeypatch):
    assert upload(srv, {"Gra.srm": b"A"}, 0)[0] == 200
    url = req(srv, "GET", "/v1/savesync/snes/g1/manifest")[1]["emulators"]["retroarch"]["cores"]["snes9x"]["bundles"]["saves"]["download_url"]
    assert req(srv, "GET", url[:-3] + "AAA")[0] == 403                          # podrobiony podpis
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + savesync.URL_TTL + 5)
    assert req(srv, "GET", url)[0] == 403                                       # wygasł
    assert req(srv, "PUT", "/v1/files/u/abc.def", raw=b"x")[0] == 403


def test_server_keeps_limited_history(srv, monkeypatch):
    monkeypatch.setattr(savesync, "KEEP_HISTORY", 2)
    for rev in range(5):
        assert upload(srv, {"Gra.srm": f"v{rev}".encode()}, rev)[0] == 200
    h = savesync.history("Jezus", "snes", "g1")[0]
    assert h["revision"] == 5 and [x["revision"] for x in h["history"]] == [4, 3]
    folder = savesync._game_dir("Jezus", "snes", "g1") / "bundles"
    assert len(list(folder.glob("*.zip"))) == 3                                 # bieżąca + 2 kopie


def test_game_id_is_stable_across_devices():
    assert savesync.game_id("snes", "Chrono Trigger (USA)") == savesync.game_id("SNES", "chrono_trigger (usa)")
    assert savesync.game_id("snes", "A") != savesync.game_id("nes", "A")
