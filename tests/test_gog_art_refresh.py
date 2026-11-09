"""Grafika GOG: starsza kopia na dysku jest zwracana od razu, odświeżenie idzie w tle."""

from __future__ import annotations

import os
import threading
import time

from emustart import goginstall as gi
from emustart.goglib import GogService


def test_old_copy_returned_at_once_and_refreshed_in_background(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "gog_dir", lambda: tmp_path)
    d = tmp_path / "art" / "123"
    d.mkdir(parents=True)
    f = d / "grid.jpg"
    f.write_bytes(b"stara")
    old = time.time() - 30 * 86400
    os.utime(f, (old, old))
    release, called = threading.Event(), threading.Event()

    class Src:
        def art(self, pid, kind):
            called.set()
            release.wait(5)
            return b"nowa", "image/jpeg"

    s = GogService.__new__(GogService)
    monkeypatch.setattr(s, "_source_fn", lambda: Src(), raising=False)
    assert s.art("123", "grid") == {"path": str(f)}          # serwer jeszcze nie odpowiedział
    assert called.wait(5) and f.read_bytes() == b"stara"
    release.set()
    for _ in range(200):
        if f.read_bytes() == b"nowa":
            break
        time.sleep(0.01)
    assert f.read_bytes() == b"nowa"
