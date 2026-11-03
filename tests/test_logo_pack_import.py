"""Paczka logo platform (pole „Paczka logo platform” w panelu): import, postęp, zła ścieżka,
ponowne wczytanie; ręczny wybór — Art Book Next / Alekfull NX / Canvas osobno, gdy plik się różni."""

from __future__ import annotations

import threading

import pytest

from emustart import config, logos, mediastore, paths, srvconfig

from test_core import _write, env  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def media(env, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "MEDIA", tmp_path / "media")
    logos.IMPORT.update(running=False, done=0, total=0, error="")
    yield tmp_path
    logos.IMPORT.update(running=False, done=0, total=0, error="")


def _pack(root, n=3):
    for i in range(n):
        _write(root / "_variants" / "Light_Color" / f"Sega Saturn-0{i}.png", PNG + bytes([i]))
    return root


def test_folder_without_logos_keeps_the_loaded_pack(media):
    good, empty = _pack(media / "good"), media / "empty"
    (empty / "_variants" / "Light_Color").mkdir(parents=True)
    assert logos.import_pack(good) == 3
    with pytest.raises(OSError, match="nie ma obrazków"):
        logos.import_pack(empty)                                        # zła ścieżka — bez czyszczenia
    assert len(mediastore.files(logos.PACK_STORE)) == 3
    st = srvconfig._pack_status()
    assert not st["ok"] and "nie ma obrazków" in st["text"] and "logo w magazynie: 3" in st["text"]


def test_status_shows_progress_while_importing_then_the_count(media, monkeypatch):
    pack = _pack(media / "pack")
    seen, real = [], mediastore.put_many

    def put_many(store, items):
        def spy():
            for it in items:
                seen.append(srvconfig._pack_status())
                yield it
        return real(store, spy())
    monkeypatch.setattr(mediastore, "put_many", put_many)
    logos.import_pack(pack)
    assert seen[0]["busy"] and seen[0]["text"] == "wczytuję paczkę: 0 / 3"
    assert seen[-1]["text"] == "wczytuję paczkę: 2 / 3"
    assert srvconfig._pack_status() == {"ok": True, "text": "logo w magazynie: 3"}


def test_deleted_folder_still_shows_the_pack_count(media):
    pack = _pack(media / "pack")
    logos.import_pack(pack)
    cfg = {**config.load(), "logo_pack_dir": str(media / "pack-usuniety")}
    f = {x["key"]: x for x in srvconfig.get(cfg)["fields"]}["logo_pack_dir"]
    assert f["status"] == {"ok": True, "text": "logo w magazynie: 3 (folder już usunięty)"}
    assert pack.is_dir()


class _Api:
    def __init__(self):
        self._cfg = config.load()


def test_saving_the_same_folder_imports_it_again(media, monkeypatch):
    pack = _pack(media / "pack")
    calls, done = [], threading.Event()
    monkeypatch.setattr(logos, "import_pack", lambda folder: (calls.append(str(folder)), done.set()))
    api = _Api()
    for _ in range(2):
        done.clear()
        assert srvconfig.set_value(api, "logo_pack_dir", str(pack))["ok"]
        assert done.wait(5)
        while logos.IMPORT["running"]:                                   # wątek importu kończy się
            pass
    assert calls == [str(pack), str(pack)]


def test_import_seen_by_a_thread_that_read_the_store_before(media):
    """Wątek serwera, który czytał magazyn przed importem (połączenie w pamięci wątku), widzi nową paczkę."""
    _pack(media / "old", 1)
    logos.import_pack(media / "old")
    pack = _pack(media / "pack")
    go, out = threading.Event(), []

    def reader():
        out.append(len(mediastore.files(logos.PACK_STORE)))        # połączenie zostaje w wątku
        go.wait(5)
        out.append(len(mediastore.files(logos.PACK_STORE)))
        mediastore.close_all()
    t = threading.Thread(target=reader)
    t.start()
    while not out:
        pass
    logos.import_pack(pack)
    go.set()
    t.join(5)
    assert out == [1, 3]


def test_abn_themes_offered_separately_only_when_the_file_differs(media, monkeypatch):
    files = {"art-book-next": b"<svg>ABN</svg>", "alekfull-nx": b"<svg>ALEK</svg>", "canvas-es-de": b"<svg>ABN</svg>"}
    monkeypatch.setattr(logos, "_exists", lambda u: "/n64dd.svg" in u and any(k in u for k in files))
    monkeypatch.setattr(logos.art_sources, "fetch",
                        lambda u, timeout=20: next((v for k, v in files.items() if k in u), None))
    c = [x for x in logos.candidates({}, "n64dd") if x["id"].startswith("url:")]
    assert [x["source"] for x in c] == ["Art Book Next", "Alekfull NX"]    # Canvas = ten sam plik co ABN
    assert c[0]["license"] == "CC BY-NC-SA"
    files["canvas-es-de"] = b"<svg>CANVAS</svg>"
    c = [x for x in logos.candidates({}, "n64dd") if x["id"].startswith("url:")]
    assert [(x["source"], x["license"]) for x in c] == [("Art Book Next", "CC BY-NC-SA"),
                                                       ("Alekfull NX", "CC BY-NC-SA"), ("Canvas", "CC0")]
    assert [p["id"] for p in logos.pack_sources()].count(logos.ABN) == 1   # zmiana wszystkich naraz — jedna
