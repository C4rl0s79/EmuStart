"""Baza LaunchBox: połączenie na zapytanie, podmiana pliku czeka na trwające odczyty
(Windows: WinError 32 przy podmianie otwartego pliku)."""

from __future__ import annotations

import sqlite3
import threading
import time

from emustart import api as api_mod, launchbox, paths


def _db(path, value):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO meta VALUES('updated', ?)", (value,))
    con.commit()
    con.close()


def _read():
    with launchbox._reader() as con:
        return con.execute("SELECT v FROM meta").fetchone()[0]


def test_swap_waits_for_readers_and_leaves_nothing_open(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    _db(launchbox.db_path(), "stara")
    assert _read() == "stara" and launchbox._readers == 0      # nic nie zostaje otwarte
    new = tmp_path / "launchbox.new"
    _db(new, "nowa")
    inside, release, order = threading.Event(), threading.Event(), []

    def long_reader():
        with launchbox._reader() as con:
            inside.set()
            release.wait(5)
            order.append(("czytelnik", con.execute("SELECT v FROM meta").fetchone()[0]))

    t = threading.Thread(target=long_reader)
    t.start()
    inside.wait(5)
    sw = threading.Thread(target=lambda: (launchbox._install(new), order.append(("podmiana", ""))))
    sw.start()
    time.sleep(0.2)
    assert not order                                 # podmiana czeka na trwający odczyt
    release.set()
    t.join(5); sw.join(5)
    assert order == [("czytelnik", "stara"), ("podmiana", "")]
    assert _read() == "nowa" and not new.exists()
    assert launchbox._readers == 0 and not launchbox._swapping


def test_readers_wait_for_swap(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    _db(launchbox.db_path(), "stara")
    new = tmp_path / "launchbox.new"
    _db(new, "nowa")
    real_replace, got = launchbox.os.replace, []

    def slow_replace(a, b):
        time.sleep(0.3)
        real_replace(a, b)

    monkeypatch.setattr(launchbox.os, "replace", slow_replace)
    sw = threading.Thread(target=launchbox._install, args=(new,))
    sw.start()
    time.sleep(0.05)
    r = threading.Thread(target=lambda: got.append(_read()))
    r.start()
    sw.join(5); r.join(5)
    assert got == ["nowa"]                           # odczyt w trakcie podmiany — po podmianie


def test_focus_hook_once():
    class Ev:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, h):
            self.handlers.append(h)
            return self

    class WV:
        focused = 0

        def Focus(self):
            WV.focused += 1

    class Browser:
        webview = WV()

    class Form:
        browser = Browser()
        Activated = Ev()

    class Win:
        native = Form()

    a = api_mod.Api.__new__(api_mod.Api)
    a._window = Win()
    a._hook_webview_focus()
    a._hook_webview_focus()                          # drugie „shown” — bez dublowania
    assert len(Form.Activated.handlers) == 1
    Form.Activated.handlers[0]()
    assert WV.focused == 1
    b = api_mod.Api.__new__(api_mod.Api)
    b._window = type("W", (), {"native": None})()
    b._hook_webview_focus()                          # brak kontrolki — tylko ostrzeżenie
