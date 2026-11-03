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
    assert any(u.endswith("/plat_ps2.svg") for u in asked)                    # Playnite Platform Logos
    png = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 32
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: png)
    assert logos.choose({}, "ps2", aniki[0]["id"])                             # adres z GitHuba — dozwolony


def test_pack_sources_list_every_theme_once(env):
    names = [p["id"] for p in logos.pack_sources()]
    assert names[0] == "Art Book Next" and "Aniki Lite (Playnite)" in names and "Canvas" in names
    assert len(names) == len(set(names))


def test_apply_pack_to_all_systems_keeps_systems_the_pack_lacks(env, monkeypatch):
    cfg = {"systems": {"snes": {"logo": "custom"}}}
    monkeypatch.setattr(logos, "_exists", lambda u: "canvas-es-de" in u and ("/psx.svg" in u or "/snes.svg" in u))
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: SVG if "canvas-es-de" in u else None)
    r = logos.apply_pack(cfg, "Canvas", ["psx", "snes", "n64"])
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
    monkeypatch.setattr(logos, "_exists", lambda u: "canvas-es-de" in u and "/psx.svg" in u)
    monkeypatch.setattr(logos.art_sources, "fetch", lambda u, timeout=20: SVG)
    packs = srvadmin.ACTIONS_ARGS["system_logo_packs"](s, {})
    assert packs["ok"] and {"id": "Canvas", "license": "CC0"} in packs["list"]
    assert srvadmin.ACTIONS_ARGS["system_logo_pack_apply"](s, {"id": "Canvas"})["ok"]
    for _ in range(100):
        st = srvadmin.ACTIONS_ARGS["system_logo_pack_status"](s, {})
        if not st["running"]:
            break
        time.sleep(0.05)
    assert st["changed"] == ["psx"] and st["missing"] == ["snes"] and saved
    assert not srvadmin.ACTIONS_ARGS["system_logo_pack_apply"](s, {"id": "nie ma"})["ok"]
