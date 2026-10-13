"""Aktualizacja serwera z panelu: wydanie z GitHuba, archiwum, skrypt podmiany, akcje panelu."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from emustart import paths, srvadmin, updater


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def test_versions():
    assert updater.vtuple("v0.31.0") == (0, 31, 0)
    assert updater.vtuple("0.31.0") > updater.vtuple("0.30.6") > updater.vtuple("0.30.10"[:6])


def test_latest(monkeypatch):
    rel = {"tag_name": "v9.9.9", "body": "zmiany", "html_url": "u", "published_at": "2026",
           "assets": [{"name": "EmuStart-9.9.9-android.apk", "browser_download_url": "apk", "size": 1},
                      {"name": "EmuStart-9.9.9-win64.zip", "browser_download_url": "zip-url", "size": 123}]}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=0: R(json.dumps(rel).encode()))
    info = updater.latest()
    assert info["newer"] and info["version"] == "9.9.9" and info["url"] == "zip-url" and info["size"] == 123


def test_extract_checks_archive(tmp_path):
    good = tmp_path / "good.zip"
    _zip(good, {"EmuStart/EmuStart.exe": "exe", "EmuStart/_internal/a.dll": "dll", "EmuStart/web/x.js": "js"})
    new = tmp_path / "new"
    updater._extract(good, new)
    assert (new / "EmuStart.exe").read_text() == "exe" and (new / "_internal" / "a.dll").is_file()
    bad = tmp_path / "bad.zip"
    _zip(bad, {"EmuStart/_internal/a.dll": "dll"})
    with pytest.raises(RuntimeError, match="nie wygląda"):
        updater._extract(bad, tmp_path / "n2")
    slip = tmp_path / "slip.zip"
    _zip(slip, {"EmuStart/EmuStart.exe": "x", "EmuStart/_internal/a": "x", "EmuStart/../../zle.txt": "x"})
    with pytest.raises(RuntimeError, match="Niedozwolona"):
        updater._extract(slip, tmp_path / "n3")
    assert not (tmp_path / "zle.txt").exists()


def test_script(tmp_path):
    s = updater.write_script(tmp_path, tmp_path / "app", 1234, ["--server", "--restarted"]).read_text("utf-8-sig")
    assert "Wait-Process -Id 1234" in s and "EmuStart Server" in s
    assert "'--service --restarted'" in s                    # dawne --server → --service, bez dublowania
    assert "EmuStartServer.exe" in s and "/Change /TN 'EmuStart Server'" in s   # zadanie → nowy program
    assert "_internal" in s and "przywracam" in s            # kopia i przywrócenie przy błędzie


def _test_key(monkeypatch):
    """Para kluczy testowych: publiczny zamiast wbudowanego → funkcja podpisu (base64)."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from emustart import signing
    k = Ed25519PrivateKey.generate()
    priv = base64.b64encode(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                            serialization.NoEncryption())).decode()
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,
                                                       serialization.PublicFormat.Raw)).decode()
    monkeypatch.setattr(signing, "RELEASE_PUBKEY", pub)
    return lambda name, data: signing.sign(priv, name, __import__("hashlib").sha256(data).hexdigest())


def _serve(blob: bytes, sig: str):
    """urlopen: plik wydania albo jego podpis (.sig)."""
    class R(io.BytesIO):
        headers = {"Content-Length": str(len(blob))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda req, timeout=0: R(sig.encode() if req.full_url.endswith(".sig") else blob)


def test_panel_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP", tmp_path)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("EmuStart/EmuStart.exe", "exe")
        z.writestr("EmuStart/_internal/a.dll", "dll")
    blob = data.getvalue()

    class R(io.BytesIO):
        headers = {"Content-Length": str(len(blob))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    sign = _test_key(monkeypatch)
    url = updater.DOWNLOAD_PREFIX + "v9.9.9/EmuStart-9.9.9-win64.zip"
    monkeypatch.setattr(updater, "latest", lambda: {"version": "9.9.9", "current": "0.31.0", "newer": True,
                                                    "url": url, "size": len(blob), "notes": "", "published": "",
                                                    "sha256": __import__("hashlib").sha256(blob).hexdigest(),
                                                    "sig_url": url + ".sig",
                                                    "page": "", "tag": "v9.9.9"})
    monkeypatch.setattr(updater.urllib.request, "urlopen", _serve(blob, sign("EmuStart-9.9.9-win64.zip", blob)))
    srvadmin._update.update(info=None, job=None)
    assert srvadmin.update_start(None)["ok"] is False          # najpierw sprawdzenie
    assert srvadmin.update_check(None)["newer"]
    assert srvadmin.update_start(None)["ok"]
    srvadmin._update["job"].thread.join(5)
    st = srvadmin.update_status(None)
    assert st["phase"] == "ready" and st["done"] == len(blob)
    assert (tmp_path / "update" / "new" / "EmuStart.exe").is_file()
    r = srvadmin.update_apply(None)                           # poza EmuStart.exe na Windows — odmowa
    assert r["ok"] is False and "EmuStart.exe" in r["reason"]


def test_download_checks_address_and_sha256(tmp_path, monkeypatch):
    """Plik tylko z wydań repozytorium i zgodny ze skrótem SHA-256 podanym przez GitHub."""
    import hashlib
    monkeypatch.setattr(updater.paths, "APP", tmp_path)
    blob = b"archiwum"
    sign = _test_key(monkeypatch)
    monkeypatch.setattr(updater.urllib.request, "urlopen", _serve(blob, sign("EmuStart-9.9.9-win64.zip", blob)))
    good = updater.DOWNLOAD_PREFIX + "v9.9.9/EmuStart-9.9.9-win64.zip"
    job = updater.Job({"version": "9.9.9", "url": "https://evil.example/EmuStart-9.9.9-win64.zip", "size": len(blob)})
    with pytest.raises(RuntimeError, match="adres"):
        job._download(job.info["url"], tmp_path / "a.zip")
    job = updater.Job({"version": "9.9.9", "url": good, "size": len(blob), "sha256": "0" * 64})
    with pytest.raises(RuntimeError, match="SHA-256"):
        job._download(good, tmp_path / "b.zip")
    assert not (tmp_path / "b.zip").exists()
    job = updater.Job({"version": "9.9.9", "url": good, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                       "sig_url": good + ".sig"})
    job._download(good, tmp_path / "c.zip")
    assert (tmp_path / "c.zip").read_bytes() == blob


def test_release_must_be_signed_with_our_key(tmp_path, monkeypatch):
    """Przejęte konto GitHub: plik i skrót się zgadzają, ale podpisu kluczem wydań nie ma."""
    import hashlib
    blob = b"podmienione archiwum"
    good = updater.DOWNLOAD_PREFIX + "v9.9.9/EmuStart-9.9.9-win64.zip"
    sign = _test_key(monkeypatch)
    info = {"version": "9.9.9", "url": good, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    # bez podpisu
    monkeypatch.setattr(updater.urllib.request, "urlopen", _serve(blob, ""))
    with pytest.raises(RuntimeError, match="podpisu"):
        updater.Job(info)._download(good, tmp_path / "a.zip")
    assert not (tmp_path / "a.zip").exists()
    # podpis innym kluczem (np. atakującego)
    mp = pytest.MonkeyPatch()
    other = _test_key(mp)                      # obcy klucz — tylko do podpisu
    mp.undo()                                  # program dalej ufa wyłącznie kluczowi wydań
    monkeypatch.setattr(updater.urllib.request, "urlopen", _serve(blob, other("EmuStart-9.9.9-win64.zip", blob)))
    with pytest.raises(RuntimeError, match="podpisu"):
        updater.Job({**info, "sig_url": good + ".sig"})._download(good, tmp_path / "b.zip")
    # podpis innego pliku (starsza wersja) — nazwa w podpisie nie pasuje
    monkeypatch.setattr(updater.urllib.request, "urlopen", _serve(blob, sign("EmuStart-9.9.8-win64.zip", blob)))
    with pytest.raises(RuntimeError, match="podpisu"):
        updater.Job({**info, "sig_url": good + ".sig"})._download(good, tmp_path / "c.zip")
    # podpis spoza wydań repozytorium
    monkeypatch.setattr(updater.urllib.request, "urlopen", _serve(blob, sign("EmuStart-9.9.9-win64.zip", blob)))
    with pytest.raises(RuntimeError, match="podpisu"):
        updater.Job({**info, "sig_url": "https://evil.example/EmuStart-9.9.9-win64.zip.sig"})._download(good, tmp_path / "d.zip")
    # wbudowany klucz wydań ma właściwy rozmiar (Ed25519)
    from emustart import signing
    import base64
    monkeypatch.undo()
    assert len(base64.b64decode(signing.RELEASE_PUBKEY)) == 32


def test_two_programs_pick_own_release_files(monkeypatch, tmp_path):
    """EmuStart i EmuStart Server — osobne pliki wydania; serwer nie pobierze EmuStart na PC."""
    import io
    import json
    import zipfile
    rel = {"tag_name": "v9.0.0", "assets": [
        {"name": "EmuStart-9.0.0-win64.zip", "browser_download_url": "u/c", "size": 1},
        {"name": "EmuStart-Server-9.0.0-win64.zip", "browser_download_url": "u/s", "size": 2}]}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: R(json.dumps(rel).encode()))
    assert updater.latest(app="client")["url"] == "u/c"
    assert updater.latest(app="server")["url"] == "u/s" and updater.latest(app="server")["root"] == "EmuStartServer"
    # wydanie sprzed rozdziału (jeden program): serwer bierze dotychczasowy plik i zostaje EmuStart.exe --server
    rel["assets"] = rel["assets"][:1]
    old = updater.latest(app="server")
    assert old["url"] == "u/c" and old["root"] == "EmuStart"
    s = updater.write_script(tmp_path, tmp_path / "app", 1, ["--server"], exe_name=old["root"]).read_text("utf-8-sig")
    assert "'--server --restarted'" in s and str(tmp_path / "app" / "EmuStart.exe") in s
    assert str(tmp_path / "app" / "EmuStartServer.exe") not in s
    monkeypatch.setattr(updater.sys, "argv", ["x", "--service"])
    assert updater.kind() == "server"
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w") as w:
        w.writestr("EmuStartServer/EmuStartServer.exe", b"x")
        w.writestr("EmuStartServer/_internal/a", b"x")
    updater._extract(z, tmp_path / "n", "EmuStartServer")
    assert (tmp_path / "n" / "EmuStartServer.exe").is_file()
    with pytest.raises(RuntimeError):
        updater._extract(z, tmp_path / "n2", "EmuStart")              # EmuStart na PC z pliku serwera — nie


def test_script_runs_as_own_task_and_confirms_start(tmp_path):
    s = updater.write_script(tmp_path, tmp_path / "app", 4321, ["--server"]).read_text(encoding="utf-8-sig")
    assert "New-Item -ItemType Directory" in s and "PID 4321" in s
    assert "schtasks /Delete /TN 'EmuStart Update'" in s           # jednorazowe zadanie sprząta po sobie
    log = tmp_path / "update.log"
    assert not updater._confirmed(log, 0, "PID 4321", seconds=0.6)  # skrypt nie ruszył — proces zostaje
    log.write_text("2026-10-12 aktualizacja: czekam na zamknięcie serwera (PID 4321)\n", encoding="utf-8")
    assert updater._confirmed(log, 0, "PID 4321", seconds=1)


def test_script_reopens_panel_in_user_session(tmp_path):
    s = updater.write_script(tmp_path, tmp_path / "app", 1, ["--server"]).read_text(encoding="utf-8-sig")
    assert "panel-user.txt" in s and "/IT" in s and "'EmuStart Panel'" in s
    assert s.index("panel-user.txt") < s.index("schtasks /Delete /TN 'EmuStart Update'")


@pytest.mark.skipif(__import__("os").name != "nt", reason="PowerShell — Windows")
def test_powershell_really_runs_in_background(tmp_path):
    """PowerShell bez konsoli (DETACHED_PROCESS) kończył się bez wykonania skryptu — przez to
    nie działała aktualizacja z aplikacji na komputerze i nie wracał panel serwera."""
    import time
    out = tmp_path / "out.txt"
    script = tmp_path / "t.ps1"
    script.write_text(f"Set-Content -LiteralPath '{out}' -Value 'ok'\n", encoding="utf-8-sig")
    updater.spawn_powershell(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], tmp_path)
    # pierwszy start PowerShella na świeżej maszynie CI potrafi trwać kilkanaście sekund
    end = time.monotonic() + 60
    while time.monotonic() < end and not (out.exists() and out.stat().st_size):
        time.sleep(0.25)
    assert out.read_text(encoding="utf-8-sig").strip() == "ok"
