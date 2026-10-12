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
    assert "'--server --restarted'" in s                     # parametry serwera, bez dublowania
    assert "_internal" in s and "przywracam" in s            # kopia i przywrócenie przy błędzie


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

    monkeypatch.setattr(updater, "latest", lambda: {"version": "9.9.9", "current": "0.31.0", "newer": True,
                                                    "url": "https://example.invalid/EmuStart-9.9.9-win64.zip", "size": len(blob), "notes": "", "published": "",
                                                    "page": "", "tag": "v9.9.9"})
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=0: R(blob))
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


def test_script_runs_as_own_task_and_confirms_start(tmp_path):
    s = updater.write_script(tmp_path, tmp_path / "app", 4321, ["--server"]).read_text(encoding="utf-8-sig")
    assert "New-Item -ItemType Directory" in s and "PID 4321" in s
    assert "schtasks /Delete /TN 'EmuStart Update'" in s           # jednorazowe zadanie sprząta po sobie
    log = tmp_path / "update.log"
    assert not updater._confirmed(log, 0, "PID 4321", seconds=0.6)  # skrypt nie ruszył — proces zostaje
    log.write_text("2026-10-12 aktualizacja: czekam na zamknięcie serwera (PID 4321)\n", encoding="utf-8")
    assert updater._confirmed(log, 0, "PID 4321", seconds=1)
