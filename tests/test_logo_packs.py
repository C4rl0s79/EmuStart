"""Logo platform z motywów Playnite i zmiana logo wszystkich systemów na jedną paczkę (serwer)."""

from __future__ import annotations

from emustart import logos, mediastore

from test_core import env  # noqa: F401  (fixture)

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 4"><rect width="10" height="4"/></svg>'


def test_playnite_theme_logos_are_offered_by_launchbox_style_names(env, monkeypatch):
    asked = []
    monkeypatch.setattr(logos, "_exists", lambda u: asked.append(u) or "Aniki-Lite" in u and "Sony%20Playstation%202" in u)
    c = logos.candidates({}, "ps2")
    aniki = [x for x in c if x["source"] == "Aniki Lite (Playnite)"]
    assert len(aniki) == 1 and aniki[0]["url"].endswith("/Source/Icons/Platform/Sony%20Playstation%202.png")
    assert "brak licencji" in aniki[0]["license"]
    assert any("ReMiX_jonosellier" in u and "Playstation%202.png" in u for u in asked)
    assert not any("/plat_" in u for u in asked)                             # Playnite Platform Logos — usunięte
    png = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 32
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: png)
    assert logos.choose({}, "ps2", aniki[0]["id"])                             # adres z GitHuba — dozwolony


def test_pack_sources_list_every_theme_once(env):
    names = [p["id"] for p in logos.pack_sources()]
    assert names[0] == logos.ABN and "Aniki Lite (Playnite)" in names         # te same pliki — jedna paczka
    assert not {"Alekfull NX", "Canvas", "Playnite Platform Logos"} & set(names)
    assert "RetroArch (dot-art)" in names and len(names) == len(set(names))


def test_apply_pack_to_all_systems_keeps_systems_the_pack_lacks(env, monkeypatch):
    cfg = {"systems": {"snes": {"logo": "custom"}}}
    monkeypatch.setattr(logos, "_exists", lambda u: "art-book-next" in u and ("/psx.svg" in u or "/snes.svg" in u))
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: SVG if "art-book-next" in u else None)
    r = logos.apply_pack(cfg, logos.ABN, ["psx", "snes", "n64"])
    assert r == {"ok": True, "changed": ["psx", "snes"], "missing": ["n64"]}
    assert cfg["systems"]["psx"]["logo"] == "custom" and mediastore.get(logos.STORE, "psx.custom.svg")[0] == SVG
    assert "n64" not in cfg["systems"] or cfg["systems"]["n64"].get("logo", "") != "custom"   # zostaje przy swoim
    assert not logos.apply_pack(cfg, "nieznana", ["psx"])["ok"]


def test_candidate_urls_are_quoted_github_addresses(env, monkeypatch):
    monkeypatch.setattr(logos, "_exists", lambda u: True)
    for x in logos.candidates({}, "megadrive"):
        if x["id"].startswith("url:"):
            u = x["id"][4:]
            assert u.startswith(logos.GH) and " " not in u


def test_panel_applies_a_pack_in_the_background_and_saves_settings(env, monkeypatch):
    import time
    from emustart import scanner, srvadmin, srvapi
    from test_core import _write
    _, _roms, tmp = env
    for es, f in (("psx", "A (USA).cue"), ("snes", "B (USA).sfc")):
        _write(tmp / "roms" / es / f)
        scanner.scan_system(es, tmp / "roms" / es)
    s = srvapi.ServerApi(start_background=False)
    saved = []
    monkeypatch.setattr(srvapi.config, "save", lambda c: saved.append(dict(c.get("systems") or {})))
    monkeypatch.setattr(logos, "_exists", lambda u: "art-book-next" in u and "/psx.svg" in u)
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: SVG)
    packs = srvadmin.ACTIONS_ARGS["system_logo_packs"](s, {})
    assert packs["ok"] and {"id": logos.ABN, "license": "CC BY-NC-SA (Canvas: CC0)"} in packs["list"]
    assert {"id": "RetroArch (dot-art)", "license": "CC BY 4.0"} in packs["list"]
    assert srvadmin.ACTIONS_ARGS["system_logo_pack_apply"](s, {"id": logos.ABN})["ok"]
    for _ in range(100):
        st = srvadmin.ACTIONS_ARGS["system_logo_pack_status"](s, {})
        if not st["running"]:
            break
        time.sleep(0.05)
    assert st["changed"] == ["psx"] and st["missing"] == ["snes"] and saved
    assert not srvadmin.ACTIONS_ARGS["system_logo_pack_apply"](s, {"id": "nie ma"})["ok"]


def test_svg_without_size_gets_it_from_viewbox():
    from emustart import logos
    only_vb = b'<?xml version="1.0"?>\n<svg version="1.1" id="Ebene_1" viewBox="0 0 264 45" xmlns="http://www.w3.org/2000/svg"><g/></svg>'
    out = logos.svg_sized(only_vb)
    assert b'<svg width="264" height="45" version="1.1"' in out and out.endswith(b"<g/></svg>")
    sized = b'<svg width="100" height="20" viewBox="0 0 264 45"><g/></svg>'
    assert logos.svg_sized(sized) == sized                                  # ma rozmiar — bez zmian
    assert logos.svg_sized(b'<svg viewBox="0,0,52.5,18.4"/>').startswith(b'<svg width="52.5" height="18.4"')
    assert logos.svg_sized(b"<svg><g/></svg>") == b"<svg><g/></svg>"        # bez viewBox — nic do dopisania
    assert logos.svg_sized(b"\x89PNG") == b"\x89PNG"


def test_svg_size_keeps_given_dimension_and_ignores_percent():
    from emustart import logos
    assert logos.svg_sized(b'<svg width="200" viewBox="0 0 2000 500"/>') == b'<svg width="200" height="50" viewBox="0 0 2000 500"/>'
    assert logos.svg_sized(b'<svg width=200 viewBox="0 0 2000 500"/>') == b'<svg width="200" height="50" viewBox="0 0 2000 500"/>'
    assert logos.svg_sized(b'<svg width="100%" height="100%" viewBox="0 0 264 45"/>') == b'<svg width="264" height="45" viewBox="0 0 264 45"/>'


def test_svg_logo_address_changes_once_for_the_sized_format(env):
    from emustart import logos, mediastore
    mediastore.put(logos.STORE, "pc98.custom.svg", b"<svg/>")
    mediastore.put(logos.STORE, "nes.custom.png", b"png")
    assert logos.custom("pc98").endswith("&" + logos.SVG_FMT)              # dawne 0×0 w pamięci — nowy adres
    assert "s=" not in logos.custom("nes")


def test_maker_word_is_dropped_but_super_is_not(env):
    names = logos.playnite_names("snes")
    assert "Nintendo Entertainment System" not in names                    # to NES, nie SNES (Jonos ReMix)
    assert "Playstation 2" in logos.playnite_names("ps2")                   # „Sony” — producent, można pominąć


def _png(w, h, blocks=1):
    import io
    from PIL import Image
    im = Image.new("RGBA", (w // blocks, h // blocks), (0, 0, 0, 0))
    for x in range(im.width):
        for y in range(im.height):
            if (x * 7 + y * 3) % 5 == 0:
                im.putpixel((x, y), (200, (x * 40) % 255, 60, 255))
    im = im.resize((w, h), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _smooth(w, h):
    """Zwykłe logo: gradient i wygładzona (półprzezroczysta) krawędź — nie pixel-art."""
    import io
    from PIL import Image
    im = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            im.putpixel((x, y), (x % 256, y % 256, (x + y) % 256, min(255, 40 + x)))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_pixel_art_logo_keeps_its_size_and_tells_the_art_pixel(env, monkeypatch):
    """Pixel-art: plik w pełnym rozmiarze (klienci bez znacznika — jak dotąd), bez przezroczystego marginesu
    (po siatce pikseli); w ustawieniach rozmiar piksela grafiki — Lumen powiększa o całe piksele."""
    import io
    from PIL import Image
    art, smooth = _png(256, 256, blocks=8), _smooth(256, 256)
    native = _png(129, 33)                                                   # motyw ES „Pixel”: natywny rozmiar
    feed = {"dot-art": art, "pixel": smooth, "es-theme-pixel": native}
    monkeypatch.setattr(logos.art_sources, "fetch",
                        lambda u, timeout=20: next((v for k, v in feed.items() if f"/{k}/" in u), smooth))
    cfg = {}
    assert logos.choose(cfg, "psx", "url:" + logos.RA_ICON.format(theme="dot-art", name="Sony%20-%20PlayStation"))
    im = Image.open(io.BytesIO(mediastore.get(logos.STORE, "psx.custom.png")[0]))
    assert im.size[0] % 8 == 0 and im.size[1] % 8 == 0 and im.size[0] > 32           # pełny rozmiar, po siatce
    assert cfg["systems"]["psx"]["logo_pixel"] == 8 and logos.url_for("psx", cfg)["pixel"] == 8
    assert logos.choose(cfg, "snes", "url:" + logos.RA_ICON.format(theme="pixel", name="x"))   # wygładzone
    assert logos.url_for("snes", cfg)["pixel"] == 0
    assert logos.choose(cfg, "n64", "url:" + logos.GH + "ehettervik/es-theme-pixel/master/n64/logo.png")
    assert logos.url_for("n64", cfg)["pixel"] == 1


def test_pixel_logo_chosen_before_is_recognised_from_the_file(env):
    mediastore.put(logos.STORE, "gba.custom.png", _png(256, 256, blocks=8))           # wybrane przed zmianą
    cfg = {"systems": {"gba": {"logo": "custom"}}}                                  # bez „logo_pixel”
    assert logos.url_for("gba", cfg)["pixel"] == 8
    assert logos.pixel_block(_smooth(64, 64)) == 0                                  # zwykłe logo
    assert logos.pixel_block(b"<svg/>") == 0


def test_art_book_next_pack_falls_back_to_alekfull_and_canvas(env, monkeypatch):
    monkeypatch.setattr(logos, "_exists", lambda u: "canvas-es-de" in u and u.endswith("/vectrex.svg"))
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: SVG)
    c = [x for x in logos.candidates({}, "vectrex") if x["source"] in ("Art Book Next", "Alekfull NX", "Canvas")]
    assert len(c) == 1 and c[0]["source"] == "Canvas" and "canvas-es-de" in c[0]["url"] and c[0]["license"] == "CC0"
    assert [p["id"] for p in logos.pack_sources()].count(logos.ABN) == 1
