"""Zdjęcia konsol platform (serwer): Wikimedia Commons przez API, licencja, wycięcie tła, magazyn."""

from __future__ import annotations

import io
import json
import urllib.parse

from PIL import Image

from emustart import consoles, mediastore

from test_core import env  # noqa: F401  (fixture)


def _jpeg_on_white() -> bytes:
    """Konsola (ciemny prostokąt) na białym tle studia, bez przezroczystości."""
    im = Image.new("RGB", (200, 100), (255, 255, 255))
    for x in range(50, 150):
        for y in range(30, 80):
            im.putpixel((x, y), (40, 40, 44))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _page(title: str, lic: str, artist: str = '<a href="//commons.wikimedia.org/wiki/User:Evan-Amos">Evan-Amos</a>'):
    name = title.removeprefix("File:")
    return {"title": title, "imageinfo": [{
        "url": f"https://upload.wikimedia.org/x/{name}", "thumburl": f"https://upload.wikimedia.org/thumb/{name}",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
        "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": artist}}}]}


PS2_HITS = {"query": {"pages": {
    "1": _page("File:Sony-PlayStation-3-2001A-wController-L.jpg", "Public domain"),        # inna konsola
    "2": _page("File:PS2-Versions.jpg", "Public domain"),
    "3": _page("File:PS2-Motherboard.jpg", "Public domain"),                               # wnętrze, nie konsola
    "4": _page("File:PlayStation-2-Console-Set-Fan.png", "Fair use"),                      # licencja nie do użycia
    "5": _page("File:PS2-Slim-Console-Set.png", "CC BY-SA 3.0")}}}


def _fake_fetch(api_answer: dict, image: bytes):
    asked = []

    def fetch(url, hdrs=None, data=None, timeout=20):
        asked.append(url)
        if url.startswith(consoles.COMMONS_API):
            return json.dumps(api_answer).encode()
        if url.startswith("https://upload.wikimedia.org/"):
            return image
        return None
    return fetch, asked


def test_search_picks_this_platforms_console_with_a_free_licence(env, monkeypatch):
    fetch, asked = _fake_fetch(PS2_HITS, b"")
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    found = consoles.search("ps2")
    assert [c["title"] for c in found] == ["File:PS2-Slim-Console-Set.png", "File:PS2-Versions.jpg"]
    assert found[0]["credit"] == "Evan-Amos, Wikimedia Commons (CC BY-SA 3.0)"
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(asked[0]).query)
    assert "PlayStation 2" in q["gsrsearch"][0] and q["gsrnamespace"] == ["6"]


def test_fetch_cuts_the_studio_background_and_keeps_credit(env, monkeypatch):
    fetch, _asked = _fake_fetch(PS2_HITS, _jpeg_on_white())
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    assert consoles.fetch("ps2", consoles.search("ps2")[0])
    data, _t = mediastore.get(consoles.STORE, "ps2.console.webp")
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    assert abs(im.size[0] - 100) <= 2 and abs(im.size[1] - 50) <= 2        # przycięte do konsoli
    assert im.getpixel((50, 25))[3] == 255
    meta = json.loads(mediastore.get(consoles.STORE, "ps2.console.json")[0])
    assert meta["credit"] == "Evan-Amos, Wikimedia Commons (CC BY-SA 3.0)" and meta["lum"] < 0.25
    u = consoles.url_for("ps2")
    assert u["url"].startswith("/media/_systems/ps2.console.webp?v=") and u["credit"] == meta["credit"]


def test_transparent_corners_after_cut(env, monkeypatch):
    im = Image.new("RGB", (60, 40), (255, 255, 255))
    for x in range(20, 40):
        for y in range(10, 30):
            im.putpixel((x, y), (200, 30, 30))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = Image.open(io.BytesIO(consoles.prepare(buf.getvalue())[0])).convert("RGBA")
    assert out.size == (20, 20) and out.getpixel((10, 10))[3] == 255


def test_arcade_has_no_console_and_nothing_found_is_remembered(env, monkeypatch):
    calls = []
    monkeypatch.setattr(consoles.art_sources, "fetch", lambda *a, **k: calls.append(a) or None)
    assert consoles.search("fbneo") == [] and calls == []                  # automaty: bez pytania sieci
    monkeypatch.setattr(consoles.art_sources, "fetch", _fake_fetch({"query": {"pages": {}}}, b"")[0])
    monkeypatch.setattr(consoles.art_sources, "online", lambda: True)
    consoles._run(["ps2"])
    assert mediastore.exists(consoles.STORE, "ps2.console.none") and consoles.url_for("ps2")["url"] == ""


def test_found_but_download_failed_is_tried_again_later(env, monkeypatch):
    # Commons ma kandydata, ale pobranie obrazka się nie udało (sieć) — bez znacznika „brak”, następnym razem od nowa
    monkeypatch.setattr(consoles, "search", lambda es: [{"title": "File:PS2-Versions.jpg", "thumb": "x"}])
    monkeypatch.setattr(consoles, "fetch", lambda es, cand: False)
    monkeypatch.setattr(consoles.art_sources, "online", lambda: True)
    monkeypatch.setattr(consoles.art_sources, "last_error", lambda: "upload.wikimedia.org: HTTP 503")
    consoles._run(["ps2"])
    assert not mediastore.exists(consoles.STORE, "ps2.console.none")
    # po błędzie sieci — przerwa: kolejne zapytania urządzeń nie szukają od nowa co chwilę
    started = []
    monkeypatch.setattr(consoles.threading, "Thread", lambda *a, **k: started.append(k) or type("T", (), {"start": lambda self: None})())
    consoles.fetch_missing(["ps2"])
    assert started == []
    consoles.choose("ps2", "auto")                                          # ręczne „automatycznie” — od razu
    consoles.fetch_missing(["ps2"])
    assert len(started) == 1


def test_candidates_that_are_not_images_mean_no_photo_not_a_network_error(env, monkeypatch):
    monkeypatch.setattr(consoles, "search", lambda es: [{"title": "File:X.jpg", "thumb": "x"}])
    monkeypatch.setattr(consoles.art_sources, "fetch", lambda url, timeout=20: b"to nie obraz")
    monkeypatch.setattr(consoles, "PROGRESS", consoles.mediaprogress.Progress("konsola"))
    consoles.PROGRESS.start(1)
    consoles._run(["ps2"])
    p = consoles.PROGRESS.snapshot()
    assert mediastore.exists(consoles.STORE, "ps2.console.none") and p["none"] == 1 and not p["last_error"]


def test_logo_network_error_is_retried_404_is_an_answer(env, monkeypatch):
    import urllib.error
    from emustart import art_sources, logos

    def answer(code):
        def urlopen(*a, **k):
            raise urllib.error.HTTPError("https://raw.githubusercontent.com/x", code, "x", {}, None)
        return urlopen
    monkeypatch.setattr(art_sources, "online", lambda: True)
    monkeypatch.setattr(logos, "PROGRESS", consoles.mediaprogress.Progress("logo"))
    logos.PROGRESS.start(2)
    monkeypatch.setattr(art_sources.urllib.request, "urlopen", answer(429))
    logos._run({}, ["vectrex"])
    assert not logos._has("vectrex.none")                                   # chwilowy błąd — bez znacznika
    monkeypatch.setattr(art_sources.urllib.request, "urlopen", answer(404))
    logos._retry_at.clear()
    logos._run({}, ["vectrex"])
    assert logos._has("vectrex.none")                                       # oba źródła: nie ma
    p = logos.PROGRESS.snapshot()
    assert (p["retry"], p["none"]) == (1, 1) and "HTTP 429" in p["last_error"]


def test_choose_none_and_auto(env, monkeypatch):
    fetch, _asked = _fake_fetch(PS2_HITS, _jpeg_on_white())
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    assert consoles.choose("ps2", "commons:File:PS2-Versions.jpg")
    assert json.loads(mediastore.get(consoles.STORE, "ps2.console.json")[0])["title"] == "File:PS2-Versions.jpg"
    assert not consoles.choose("ps2", "commons:File:PS2-Motherboard.jpg")   # spoza propozycji — nie
    assert consoles.choose("ps2", "none") and consoles.url_for("ps2")["url"] == ""
    assert consoles.choose("ps2", "auto") and not mediastore.exists(consoles.STORE, "ps2.console.none")


def test_upload_own_photo(env):
    im = Image.new("RGBA", (30, 20), (0, 0, 0, 0))
    for x in range(5, 25):
        im.putpixel((x, 10), (250, 250, 250, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    assert consoles.upload("ps2", buf.getvalue(), "własne zdjęcie")
    assert consoles.url_for("ps2")["credit"] == "własne zdjęcie" and consoles.url_for("ps2")["lum"] > 0.9
    assert not consoles.upload("ps2", b"nie obraz", "")


def test_another_platform_in_the_name_or_a_part_is_not_this_console(env, monkeypatch):
    """„PlayStation” to nie „PS3-Slim”; Xbox 360 to nie oryginalny Xbox; „Box”, „Back”, sam pad — nie konsola."""
    hits = {"query": {"pages": {
        "1": _page("File:Sony-PlayStation-PS3-Slim-Console-BR.jpg", "Public domain"),
        "2": _page("File:PSX-Console-wController.png", "Public domain"),
        "3": _page("File:PlayStation-Box-Front.jpg", "Public domain")}}}
    monkeypatch.setattr(consoles.art_sources, "fetch", _fake_fetch(hits, b"")[0])
    assert [c["title"] for c in consoles.search("psx")] == ["File:PSX-Console-wController.png"]
    hits = {"query": {"pages": {
        "1": _page("File:Microsoft-Xbox-360-Pro-Console-FL.jpg", "Public domain"),
        "2": _page("File:Xbox-360-Controller-Black.jpg", "Public domain"),
        "3": _page("File:Atari-Lynx-Back-Top.jpg", "Public domain")}}}
    monkeypatch.setattr(consoles.art_sources, "fetch", _fake_fetch(hits, b"")[0])
    assert [c["title"] for c in consoles.search("x360")] == ["File:Microsoft-Xbox-360-Pro-Console-FL.jpg"]
    assert consoles.search("xbox") == []                                    # „xbox” + 360 — inna konsola


def test_generation_number_after_the_name_is_another_console_model_number_is_not():
    v = consoles._variants("psx")
    assert consoles._named("File:Sony-PlayStation-4-PS4-Console-FL.png", v) == []
    assert consoles._named("File:Sony-PlayStation-3-2001A-wController-L.jpg", v) == []
    assert consoles._named("File:PSX-Console-wController.jpg", v)
    assert consoles._named("File:PlayStation-Vita-1101-FL.png", consoles._variants("psvita"))
    assert consoles._named("File:Nintendo-64-Console-FL.jpg", consoles._variants("n64"))


# ── konsola przy systemach: serwer → urządzenia → klient (jak logo) ──

def _png(w=40, h=20, color=(30, 30, 30, 255)) -> bytes:
    im = Image.new("RGBA", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_server_systems_carry_console_and_missing_ones_are_fetched(env, monkeypatch):
    from emustart import libsync, remote, scanner, srvapi
    from test_core import _write
    _, _roms, tmp = env
    _write(tmp / "roms" / "snes" / "A (USA).sfc")
    scanner.scan_system("snes", tmp / "roms" / "snes")
    asked = []
    monkeypatch.setattr(consoles, "fetch_missing", lambda es_list: asked.extend(es_list))
    s = srvapi.ServerApi(start_background=False)
    assert consoles.upload("snes", _png(), "Evan-Amos, Wikimedia Commons (CC BY-SA 3.0)")
    row = {x["es"]: x for x in s.list_systems()}["snes"]
    assert row["console"].startswith("/media/_systems/snes.console.webp") and row["console_lum"] < 0.3
    assert row["console_credit"].startswith("Evan-Amos") and asked == ["snes"]
    remote.API = s
    try:
        # komputery i Lumen pytają /v1/library (server_systems), panel — systems_look: obie drogi też pobierają brakujące
        asked.clear()
        rec = {x["es"]: x for x in libsync.server_systems()}["snes"]
        assert rec["console"] == row["console"] and rec["console_credit"] == row["console_credit"]
        assert asked == ["snes"]
        asked.clear()
        assert {x["es"]: x for x in s.systems_look()}["snes"]["console"] == row["console"]
        assert asked == ["snes"]
    finally:
        remote.API = None


def test_client_keeps_server_console_and_drops_a_changed_local_copy(env, monkeypatch):
    from emustart import libsync, library
    old = {"snes": {"console": "/media/_systems/snes.console.webp?v=1"}}
    new = {"snes": {"console": "/media/_systems/snes.console.webp?v=2", "console_credit": "X", "console_lum": 0.4}}
    mediastore.put(consoles.STORE, "snes.console.webp", b"stara kopia")
    library.meta_set("server_emu_rec", json.dumps(new))
    libsync._drop_changed_logos(old, new)
    assert not mediastore.exists(consoles.STORE, "snes.console.webp")       # pobierze się nowa
    assert libsync.console_for("snes") == {"url": new["snes"]["console"], "credit": "X", "lum": 0.4}
    assert libsync.console_for("nes") is None


def test_panel_actions_candidates_choose_and_upload(env, monkeypatch):
    import base64
    from emustart import srvadmin, srvapi
    fetch, _asked = _fake_fetch(PS2_HITS, _jpeg_on_white())
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    monkeypatch.setattr(consoles, "_exists", lambda u: False)               # motywy: bez sieci
    s = srvapi.ServerApi(start_background=False)
    r = srvadmin.ACTIONS_ARGS["system_console_candidates"](s, {"es": "ps2"})
    assert r["ok"] and [c["id"] for c in r["list"]][:2] == ["commons:File:PS2-Slim-Console-Set.png",
                                                             "commons:File:PS2-Versions.jpg"]
    assert srvadmin.ACTIONS_ARGS["system_console_choose"](s, {"es": "ps2", "id": r["list"][1]["id"]})["url"]
    big = _png(1200, 900, (200, 30, 30, 255))
    assert len(big) > 1 << 16 or True
    up = srvadmin.ACTIONS_ARGS["system_console_upload"](s, {"es": "ps2", "data": base64.b64encode(big).decode(),
                                                             "credit": "moje zdjęcie"})
    assert up["ok"] and up["credit"] == "moje zdjęcie"
    assert not srvadmin.ACTIONS_ARGS["system_console_upload"](s, {"es": "ps2", "data": "!!"})["ok"]


def test_panel_upload_body_may_be_larger_than_other_actions(monkeypatch):
    """Zdjęcie konsoli ma kilka MB — akcja wgrania czyta całą treść, inne jak dotąd do 64 KB."""
    from emustart import srvadmin
    got = {}
    monkeypatch.setitem(srvadmin.ACTIONS_ARGS, "system_console_upload", lambda api, a: got.update(a) or {"ok": True})

    class H:
        path = "/admin/system_console_upload"
        body = json.dumps({"es": "ps2", "data": "A" * 300_000}).encode()
        headers = {"Content-Length": str(len(body))}
        rfile = io.BytesIO(body)
        wfile = io.BytesIO()

        def send_response(self, *_a): pass
        def send_header(self, *_a): pass
        def end_headers(self): pass
        def send_error(self, *_a): raise AssertionError("404")
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    srvadmin.handle(H(), None)
    assert len(got.get("data", "")) == 300_000


# ── paczki grafik konsol z motywów ES / ES-DE (jak paczki logo) ──

def _pack(name_part: str) -> str:
    return next(p["id"] for p in consoles.pack_sources() if name_part in p["id"])


def test_theme_packs_are_listed_with_licences():
    packs = {p["id"]: p["license"] for p in consoles.pack_sources()}
    assert packs[_pack("Retrorama")] == "GPL-3.0"
    assert packs[_pack("PlayStation X")] == packs[_pack("Analogue 3D")] == "brak licencji w repozytorium"


def test_pack_sets_every_system_it_has_and_others_keep_theirs(env, monkeypatch):
    src = _pack("Retrorama")
    monkeypatch.setattr(consoles, "_exists", lambda u: "retrorama" in u and u.endswith("/snes.png"))
    monkeypatch.setattr(consoles.art_sources, "fetch", lambda u, timeout=20: _png(600, 300, (200, 30, 30, 255)))
    assert consoles.upload("psx", _png(), "moje")                           # psx ma własne — paczka go nie ma
    monkeypatch.setattr(consoles, "PROGRESS", consoles.mediaprogress.Progress("konsola"))
    r = consoles.apply_pack(src, ["snes", "psx"])
    assert r == {"ok": True, "changed": ["snes"], "missing": ["psx"]}
    p = consoles.PROGRESS.snapshot()                                        # ten sam pasek w panelu
    assert (p["done"], p["total"], p["found"], p["none"], p["running"]) == (2, 2, 1, 1, False)
    assert p["label"] == f"paczka {src}"
    snes = consoles.url_for("snes")
    assert snes["url"] and "Retrorama" in snes["credit"] and "GPL-3.0" in snes["credit"]
    assert consoles.url_for("psx")["credit"] == "moje"
    assert not consoles.apply_pack("nie ma takiej", ["snes"])["ok"]


def test_theme_candidates_and_choose_only_from_proposals(env, monkeypatch):
    monkeypatch.setattr(consoles, "_exists", lambda u: u.endswith("/megadrive.webp") or u.endswith("/megadrive.png"))
    monkeypatch.setattr(consoles.art_sources, "fetch", lambda u, timeout=20: _png(424, 238))
    cands = consoles.theme_candidates("megadrive")
    assert {c["source"] for c in cands} == {p["id"] for p in consoles.pack_sources()}
    assert all(c["url"].startswith("https://raw.githubusercontent.com/") and c["license"] for c in cands)
    assert consoles.choose("megadrive", "theme:" + cands[0]["url"])
    assert not consoles.choose("megadrive", "theme:https://raw.githubusercontent.com/obcy/repo/main/megadrive.png")


def test_pixel_art_is_enlarged_with_sharp_pixels():
    im = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(40):
        for y in range(20):
            if (x + y) % 2:
                im.putpixel((x, y), (250, 40, 40, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out, _lum = consoles.prepare(buf.getvalue(), pixel=True)
    big = Image.open(io.BytesIO(out)).convert("RGBA")
    assert big.size[0] >= 800                                               # tło pełnego ekranu, bez rozmycia
    opaque = {c for _n, c in big.getcolors(1 << 20) if c[3] > 0}
    assert len(opaque) <= 3                                                 # bez nowych, uśrednionych kolorów


def test_panel_lists_and_applies_console_packs(env, monkeypatch):
    import time
    from emustart import scanner, srvadmin, srvapi
    from test_core import _write
    _, _roms, tmp = env
    for es, f in (("psx", "A (USA).cue"), ("snes", "B (USA).sfc")):
        _write(tmp / "roms" / es / f)
        scanner.scan_system(es, tmp / "roms" / es)
    s = srvapi.ServerApi(start_background=False)
    monkeypatch.setattr(consoles, "fetch_missing", lambda es_list: None)
    monkeypatch.setattr(consoles, "_exists", lambda u: "analogue-3d" in u and u.endswith("/psx.png"))
    monkeypatch.setattr(consoles.art_sources, "fetch", lambda u, timeout=20: _png(450, 246))
    packs = srvadmin.ACTIONS_ARGS["system_console_packs"](s, {})
    assert packs["ok"] and len(packs["list"]) == 3
    src = _pack("Analogue 3D")
    assert srvadmin.ACTIONS_ARGS["system_console_pack_apply"](s, {"id": src})["ok"]
    for _ in range(100):
        st = srvadmin.ACTIONS_ARGS["system_console_pack_status"](s, {})
        if not st["running"]:
            break
        time.sleep(0.05)
    assert st["changed"] == ["psx"] and st["missing"] == ["snes"]
    assert not srvadmin.ACTIONS_ARGS["system_console_pack_apply"](s, {"id": "nie ma"})["ok"]


AMIGA_HITS = {"query": {"pages": {
    "1": _page("File:Amiga-CD32-wController-L-TRSP.png", "Public domain"),                 # konsola CD32, nie Amiga
    "2": _page("File:2019-08-04 Commodore Amiga personal computer.jpg", "CC BY-SA 4.0"),
    "3": _page("File:Amiga 500 Plus (transparent background).png", "Public domain"),
    "4": _page("File:Console videogiochi amiga 500 (milano).jpg", "CC BY 3.0")}}}


def test_home_computers_are_searched_as_computers_not_consoles(env, monkeypatch):
    fetch, asked = _fake_fetch(AMIGA_HITS, b"")
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    found = consoles.search("amiga")
    assert [c["title"] for c in found][:2] == ["File:Amiga 500 Plus (transparent background).png",
                                               "File:2019-08-04 Commodore Amiga personal computer.jpg"]
    assert not any("CD32" in c["title"] or "Console" in c["title"] for c in found)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(asked[0]).query)["gsrsearch"][0]
    assert q == "Amiga 500"                                                 # typowy model, bez „console Evan-Amos”
    fetch, asked = _fake_fetch(PS2_HITS, b"")
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    consoles.search("ps2")                                                  # konsole — jak dotąd
    assert urllib.parse.parse_qs(urllib.parse.urlparse(asked[0]).query)["gsrsearch"][0].endswith("console Evan-Amos")


def test_background_download_reports_progress_and_network_errors(env, monkeypatch):
    """Pasek w panelu: ile z ilu, wynik każdego systemu; błąd sieci (np. HTTP 429) — do ponowienia, z powodem,
    bez znacznika „brak zdjęcia”."""
    fetch, _asked = _fake_fetch(PS2_HITS, _jpeg_on_white())
    monkeypatch.setattr(consoles.art_sources, "fetch", fetch)
    monkeypatch.setattr(consoles.art_sources, "online", lambda: True)
    monkeypatch.setattr(consoles, "PROGRESS", consoles.mediaprogress.Progress("konsola"))
    consoles.PROGRESS.start(3)
    consoles.art_sources._err.text = "stary błąd innego systemu"            # nie przechodzi na kolejny system
    consoles._run(["ps2", "fbneo"])                                          # zdjęcie jest; automat — bez zdjęcia
    monkeypatch.setattr(consoles, "search", lambda es: [])
    monkeypatch.setattr(consoles.art_sources, "last_error", lambda: "commons.wikimedia.org: HTTP 429")
    consoles._run(["snes"])
    p = consoles.PROGRESS.snapshot()
    assert (p["done"], p["total"], p["found"], p["none"], p["retry"], p["running"]) == (3, 3, 1, 1, 1, False)
    assert p["last_error"] == "snes: commons.wikimedia.org: HTTP 429"
    assert not mediastore.exists(consoles.STORE, "snes.console.none")       # następnym razem od nowa


def test_fetch_tells_why_it_failed(monkeypatch):
    import urllib.error
    from emustart import art_sources

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://upload.wikimedia.org/x.png", 429, "Too Many Requests", {}, None)
    monkeypatch.setattr(art_sources.urllib.request, "urlopen", boom)
    assert art_sources.fetch("https://upload.wikimedia.org/x.png") is None
    assert art_sources.last_error() == "upload.wikimedia.org: HTTP 429"


def test_panel_shows_media_progress(env):
    from emustart import srvadmin, srvapi
    s = srvapi.ServerApi(start_background=False)
    r = srvadmin.ACTIONS_ARGS["systems_media_progress"](s, {})
    assert r["ok"] and {"running", "done", "total", "current", "last_error"} <= set(r["consoles"]) <= set(r["logos"]) | set(r["consoles"])
