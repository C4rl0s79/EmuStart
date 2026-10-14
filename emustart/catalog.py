"""catalog — odczyt biblioteki gier wspólny dla EmuStart Server i EmuStart na PC.

Serwer czyta swoją bibliotekę (ze skanu folderów), EmuStart na PC — kopię biblioteki
serwera (emustart/libsync.py). Lista gier z filtrami i metadanymi, wersje gier
(tłumaczenia, wersje arcade), ukrywane grupy.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from emustart import art, library, metadata, scanner, systems, translations

log = logging.getLogger("emustart.catalog")

REGIONS = {"USA": "USA", "Europe": "Europa", "Japan": "Japonia", "World": "Świat",
           "Germany": "Niemcy", "France": "Francja", "Spain": "Hiszpania", "Italy": "Włochy",
           "UK": "Wielka Brytania", "Australia": "Australia", "Korea": "Korea", "Brazil": "Brazylia",
           "Canada": "Kanada", "Asia": "Azja", "China": "Chiny", "Taiwan": "Tajwan",
           "Netherlands": "Holandia", "Sweden": "Szwecja", "Poland": "Polska"}

HIDE_KEYS = {"hide_beta": "beta", "hide_demo": "demo", "hide_pirate": "pirate",
             "hide_unl": "unl", "hide_program": "program"}


def regions(tags: str) -> list:
    """'(USA, Europe) (Rev 1)' → ['USA', 'Europa'] (tylko znane nazwy regionów)."""
    out = []
    for group in re.findall(r"\(([^)]*)\)", tags or ""):
        parts = [p.strip() for p in group.split(",")]
        if parts and all(p in REGIONS for p in parts):
            out += [REGIONS[p] for p in parts]
    return out


def players(v) -> int:
    """'2', '1-4', '4' → największa liczba graczy (0 = brak danych)."""
    nums = [int(n) for n in re.findall(r"\d+", str(v or ""))]
    return max(nums) if nums else 0


def hide_groups(cfg: dict) -> list:
    return [g for k, g in HIDE_KEYS.items() if cfg.get(k)]


def visible_counts(cfg: dict, summary: list) -> dict | None:
    """Liczba gier systemów po ukryciu grup i wersji arcade (None = bez ukrywania)."""
    groups = hide_groups(cfg)
    clones = [s["es"] for s in summary if systems.info(s["es"])["kind"] == "arcade"] \
        if cfg.get("hide_arcade_clones", True) else []
    return library.visible_counts(groups, clones) if groups or clones else None


def list_games(cfg: dict, es: str, profile: int) -> list:
    """Gry systemu z danymi do filtrów (gatunek, rok, gracze, regiony) i adresami grafik."""
    hide = cfg.get("hide_arcade_clones", True) and systems.info(es)["kind"] == "arcade"
    rows = library.list_games(es, profile, hide)
    groups = hide_groups(cfg)
    if groups:
        rows = [r for r in rows if not library.is_hidden(r["name"], groups)]
    try:
        metadata.prepare_system(cfg, es)     # dane do filtrów (rdb, offline)
    except Exception:
        log.exception("metadane systemu %s", es)
    fields = metadata.system_fields(es)
    for r in rows:
        f = fields.get(r["id"], {})
        if f.get("title"):
            r["title"] = f["title"]
        r["genre"], r["year"] = f.get("genre", ""), str(f.get("year", ""))[:4]
        r["players"] = players(f.get("players", ""))
        r["developer"], r["publisher"] = f.get("developer", ""), f.get("publisher", "")
        r["regions"] = regions(r["tags"])
        r["logo"] = art.media_url(es, r["name"], "logo") if r["art_logo"] == art.HAS else ""
        r["box"] = art.media_url(es, r["name"], "box") if r["art_box"] == art.HAS else ""
        r["snap"] = art.media_url(es, r["name"], "snap") if r["art_snap"] == art.HAS else ""
    return rows


def variants(g: dict) -> list:
    """Wersje schowane pod grą: tłumaczenia albo — arcade — inne wersje setu (klony
    rodzica, np. japońska, wcześniejsza rewizja): [{id, rel, label, lang}]."""
    if systems.info(g["es"])["kind"] == "arcade" and not g.get("parent"):
        name = Path(g["rel"]).stem
        rows = library.db().execute(
            "SELECT id, rel, name FROM games WHERE es=? AND parent=? AND hidden=0 ORDER BY name COLLATE NOCASE",
            (g["es"], name)).fetchall()
        return [{"id": r["id"], "rel": r["rel"].replace("\\", "/"), "lang": "",
                 "label": f"{r['name']} · {Path(r['rel']).stem}", "name": r["name"]} for r in rows]
    if g.get("trans") or not g.get("tbase"):
        return []
    rows = library.db().execute(
        "SELECT id, rel, name, trans FROM games WHERE es=? AND tbase=? AND trans!='' ORDER BY name",
        (g["es"], g["tbase"])).fetchall()
    out = [{"id": r["id"], "rel": r["rel"].replace("\\", "/"), "lang": r["trans"],
            "label": translations.label(r["name"], r["trans"]), "name": r["name"]} for r in rows]
    # kilka wersji o tej samej etykiecie (pliki bez tagu [T-…]) — dopisek z nazwy pliku
    labels = [v["label"] for v in out]
    for v in out:
        if labels.count(v["label"]) > 1:
            extra = scanner.split_title(v["name"])[1]
            v["label"] = f"{v['label']} · {extra or Path(v['rel']).stem}"
    return out


def game_meta(cfg: dict, g: dict) -> dict:
    """Metadane gry do szczegółów (bez pól wewnętrznych)."""
    meta = metadata.ensure_local(cfg, g)
    meta = {k: v for k, v in meta.items() if not k.startswith("_")}
    if meta.get("genre"):
        meta["genre"] = metadata.norm_genres(meta["genre"])
    return meta
