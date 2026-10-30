"""Okno EmuStart po tytule (0.54.1): tylko okna tego procesu, bez okien-zastępców powłoki
Windows 11 (karta przeglądarki „EmuStart” w Alt+Tab) — inaczej UI uznawało, że okno nie jest
na wierzchu, i odrzucało pada."""

from __future__ import annotations

import os

import pytest

from emustart import winutil

pytestmark = pytest.mark.skipif(os.name != "nt", reason="WinAPI")


@pytest.fixture()
def window():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("brak ekranu")
    root.title("EmuStart-test-okna")
    root.update()
    yield root
    root.destroy()


def test_finds_own_window_only(window):
    h = winutil.find_window("EmuStart-test-okna", os.getpid())
    assert h and winutil.window_class(h) == "TkTopLevel"
    assert winutil.find_window("EmuStart-test-okna", 4) == 0               # inny proces — nie
    assert winutil.find_window("EmuStart-test", os.getpid()) == 0           # tytuł dokładnie


def test_shell_proxy_window_is_skipped(window, monkeypatch):
    real = winutil.window_class
    monkeypatch.setattr(winutil, "window_class",
                        lambda h: winutil.PROXY_CLASSES[0] if real(h) == "TkTopLevel" else real(h))
    assert winutil.find_window("EmuStart-test-okna") == 0
