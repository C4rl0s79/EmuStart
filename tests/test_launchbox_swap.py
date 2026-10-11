"""Podmiana bazy LaunchBox przy otwartych połączeniach innych wątków (Windows: WinError 32)."""

from __future__ import annotations

import sqlite3
import threading

from emustart import launchbox, paths


def _db(path, value):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO meta VALUES('updated', ?)", (value,))
    con.commit()
    con.close()


def test_install_closes_reader_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    _db(launchbox.db_path(), "stara")
    seen = {}

    def reader(key):
        seen[key] = launchbox._con().execute("SELECT v FROM meta").fetchone()[0]

    t = threading.Thread(target=reader, args=("przed",))
    t.start(); t.join()
    reader("ten wątek")
    assert len(launchbox._open) >= 2
    new = tmp_path / "launchbox.new"
    _db(new, "nowa")
    launchbox._install(new)
    assert not launchbox._open and not new.exists()
    reader("po")                                       # ten sam wątek otwiera bazę na nowo
    assert seen == {"przed": "stara", "ten wątek": "stara", "po": "nowa"}
