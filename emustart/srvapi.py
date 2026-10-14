"""srvapi — EmuStart Server: biblioteka gier, skan folderów, grafiki i opisy.

Używane przez API urządzeń (emustart/remote.py), panel serwera (emustart/srvadmin.py)
i konfigurację (emustart/srvconfig.py). Bez interfejsu z padem, profili gracza na tym
komputerze i uruchamiania gier — to robi EmuStart na PC (emustart/api.py).
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from emustart import (art, catalog, config, launchbox, library, logos, metadata, scanner,
                      sysinfo, systems)

log = logging.getLogger("emustart.srvapi")


def _clear_device_lists() -> None:
    from emustart import remote
    remote._games_cache.clear()                   # listy gier dla urządzeń — od nowa


class ServerApi:
    def __init__(self, start_background: bool = True):
        self._cfg = config.load()
        self._profile = library.default_profile()
        self._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
        self._art_job = None
        self._lb_update = None
        if start_background:
            from emustart import mediastore
            mediastore.migrate_later()            # stare pliki grafik → jeden plik na system
            threading.Thread(target=self._arcade_meta, daemon=True, name="arcade-meta").start()
            threading.Thread(target=self._platforms_meta, daemon=True, name="platforms").start()

    # ── biblioteka (urządzenia) ──
    def system_info(self, es: str) -> dict:
        """Opis platformy (producent, lata, nośnik, opis). Brak danych = pobranie w tle."""
        d = sysinfo.get(es)
        if d is None:
            sysinfo.ensure_now(es)
            return {"pending": True}
        return {k: v for k, v in d.items() if not k.startswith("_") and k != "lb_notes"}

    def list_systems(self) -> list:
        summary = library.systems_summary()
        logos.fetch_missing(self._cfg, [s["es"] for s in summary])
        counts = catalog.visible_counts(self._cfg, summary)
        out = []
        for s in summary:
            if counts is not None:
                s["games"] = counts.get(s["es"], 0)
            emu = (self._cfg.get("systems") or {}).get(s["es"]) or {}
            if not emu.get("enabled", True) or not s["games"]:
                continue
            lg = logos.url_for(s["es"], self._cfg)
            out.append({"es": s["es"], "display": emu.get("name") or s["display"], "games": s["games"],
                        "cached": 0, "logo": lg["url"], "logo_glow": lg["glow"],
                        "emulator": emu.get("label", ""), "kind": systems.info(s["es"])["kind"]})
        return out

    def list_games(self, es: str) -> list:
        return catalog.list_games(self._cfg, es, self._profile)

    def game_detail(self, game_id: int, use_variant: bool = False) -> dict:
        """Szczegóły gry dla urządzenia (wersję gry wybiera profil na urządzeniu)."""
        g = library.game(int(game_id))
        if not g:
            return {}
        from emustart import metadata
        meta = catalog.game_meta(self._cfg, g)
        return {"id": g["id"], "title": meta.get("title") or g["title"], "tags": g["tags"],
                "name": g["name"], "meta": meta,
                "meta_online": bool(metadata.get(g["id"]).get("_online")),
                "system": systems.info(g["es"])["display"], "size": g["size"], "files": len(g["files"]),
                "file": g["rel"], "es": g["es"], "rel": g["rel"].replace("\\", "/"),
                "variants": catalog.variants(g), "variant": 0, "variant_label": ""}

    # ── skan folderów z grami ──
    def rescan(self) -> dict:
        _clear_device_lists()
        if self._scan["running"]:
            return {"ok": False}
        self._scan.update(running=True, text="Start…", done=0, total=0, result=None)

        def prog(text, done, total):
            self._scan.update(text=text, done=done, total=total)

        def run():
            try:
                self._scan["result"] = scanner.scan_all(self._cfg, prog)
            except Exception as ex:
                log.exception("scan")
                self._scan["result"] = {"ok": False, "reason": str(ex)}
            finally:
                self._scan["running"] = False
                _clear_device_lists()
        threading.Thread(target=run, daemon=True, name="scan").start()
        return {"ok": True}

    def scan_status(self) -> dict:
        return dict(self._scan)

    def _rescan_systems(self, names: list) -> None:
        """Ponowny skan wskazanych systemów (np. po nowym indeksie arcade) — w tym wątku."""
        if not names:
            return
        found, _u = scanner.collect_systems(self._cfg)
        for es in names:
            if found.get(es):
                try:
                    scanner.scan_system(es, found[es])
                    log.info("ponowny skan %s (nazwy i wersje arcade)", es)
                except Exception:
                    log.exception("skan %s", es)
        _clear_device_lists()

    def _arcade_meta(self) -> None:
        """Nazwy, rodzice wersji, rok/producent/gracze/gatunek setów arcade — raz, w tle.
        Z MAME, gdy jest; bez niego — z pliku DAT FinalBurn Neo. Po nowym indeksie systemy
        arcade są skanowane od nowa (nazwy, łączenie wersji)."""
        from emustart import arcade
        try:
            arc = [s["es"] for s in library.systems_summary() if systems.info(s["es"])["kind"] == "arcade"]
            if arcade.find_mame(self._cfg):
                if arcade.needs_refresh():
                    r = arcade.refresh(self._cfg)
                    log.info("arcade: %s", r)
                    if r.get("ok") and not r.get("cached"):
                        self._rescan_systems(arc)
            elif arc:
                r = arcade.refresh_fbneo()
                log.info("arcade (bez MAME): %s", r)
                if r.get("ok") and not r.get("cached"):
                    self._rescan_systems(arc)
            for es in arc:                  # grafiki pod nazwą setu → pod pełną nazwą (0.44.4)
                n = art.adopt_set_names(es)
                if n:
                    log.info("grafiki %s: %d pod nowe nazwy gier", es, n)
                    _clear_device_lists()
        except Exception:
            log.exception("arcade meta")

    def _platforms_meta(self) -> None:
        """Dane platform LaunchBoksa i opisy platform (karuzela na urządzeniach)."""
        try:
            if launchbox.ready() and launchbox.platform("psx") is None:
                log.info("platformy LaunchBox: %s", launchbox.fetch_platforms())
            sysinfo.ensure([s["es"] for s in library.systems_summary()])
        except Exception:
            log.exception("platformy")

    # ── grafiki i opisy ──
    def art_overview(self) -> dict:
        from emustart import art_sources
        rows = library.db().execute("""
            SELECT es, COUNT(*) n, SUM(art_box=1) box, SUM(art_snap=1) snap,
                   SUM(art_box=0 OR art_snap=0) unchecked
            FROM games WHERE hidden=0 GROUP BY es""").fetchall()
        names = {s["es"]: s["display"] for s in library.systems_summary()}
        systems_ = sorted(({"es": r["es"], "display": names.get(r["es"], r["es"]),
                            "games": r["n"], "box": r["box"] or 0, "snap": r["snap"] or 0,
                            "unchecked": r["unchecked"] or 0} for r in rows),
                          key=lambda x: x["display"].lower())
        desc = dict(library.db().execute("""
            SELECT g.es, SUM(m.data LIKE '%"description"%' OR m.data LIKE '%"wiki"%')
            FROM games g LEFT JOIN game_meta m ON m.game_id=g.id WHERE g.hidden=0 GROUP BY g.es""").fetchall())
        logo_n = dict(library.db().execute(
            "SELECT es, SUM(art_logo=1) FROM games WHERE hidden=0 GROUP BY es").fetchall())
        for s_ in systems_:
            s_["desc"] = desc.get(s_["es"]) or 0
            s_["logo"] = logo_n.get(s_["es"]) or 0
        keys = self._cfg.get("art_keys") or {}
        return {"systems": systems_, "sources": art_sources.Sources(self._cfg).enabled(),
                "launchbox": launchbox.status(), "games_logo": bool(self._cfg.get("games_logo")),
                "lb_update": self._lb_update.status() if self._lb_update else None,
                "keys_from": keys.get("source", ""),
                "job": self._art_job.status() if self._art_job else None}

    def art_start(self, es: str = "", mode: str = "art") -> dict:
        """mode: art | meta | meta_wiki | all (grafiki + metadane) | shrink (zmniejsz zapisane)."""
        if self._art_job and not self._art_job.finished:
            return {"ok": False, "reason": "Pobieranie już trwa."}
        if mode != "shrink":
            try:
                art.check_space()
            except art.DiskFull as ex:
                return {"ok": False, "reason": str(ex)}
        self._art_job = art.Job(self._cfg, es or None, art=mode in ("art", "all"),
                                meta=mode in ("meta", "meta_wiki", "all"), wiki=mode == "meta_wiki",
                                shrink=mode == "shrink")
        self._art_job.start()
        return {"ok": True}

    def art_status(self) -> dict | None:
        return self._art_job.status() if self._art_job else None

    def art_cancel(self) -> None:
        if self._art_job:
            self._art_job.cancel.set()

    def launchbox_update(self) -> dict:
        if self._lb_update and not self._lb_update.finished:
            return {"ok": False, "reason": "Pobieranie bazy LaunchBox już trwa."}
        self._lb_update = launchbox.Updater()
        self._lb_update.start()
        return {"ok": True}

    def launchbox_status(self) -> dict:
        return {"db": launchbox.status(), "update": self._lb_update.status() if self._lb_update else None}

    # ── jedna gra: grafiki i opis (panel → Grafiki i opisy → Gra) ──
    def games_search(self, q: str = "", es: str = "", limit: int = 60) -> list:
        """Gry po fragmencie nazwy (bez ukrytych), z adresami grafik."""
        sql = "SELECT * FROM games WHERE hidden=0"
        args: list = []
        if es:
            sql += " AND es=?"
            args.append(es)
        for word in (q or "").split()[:6]:
            sql += " AND name LIKE ? ESCAPE '\\'"
            args.append("%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY name COLLATE NOCASE LIMIT ?"
        args.append(max(1, min(200, int(limit))))
        names = {s_["es"]: s_["display"] for s_ in library.systems_summary()}
        return [{"id": r["id"], "es": r["es"], "system": names.get(r["es"], r["es"]), "name": r["name"],
                 **self._art_urls(r)} for r in library.db().execute(sql, args).fetchall()]

    @staticmethod
    def _art_urls(r) -> dict:
        return {k: art.media_url(r["es"], r["name"], k) if r[f"art_{k}"] == art.HAS else ""
                for k in ("box", "snap", "logo")}

    def game_edit(self, game_id: int) -> dict:
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        m = metadata.get(g["id"])
        r = library.db().execute("SELECT * FROM games WHERE id=?", (g["id"],)).fetchone()
        return {"ok": True, "id": g["id"], "es": g["es"], "name": g["name"], "title": g["title"],
                "system": systems.info(g["es"])["display"], "rel": g["rel"].replace("\\", "/"),
                "meta": catalog.game_meta(self._cfg, g), "edits": m.get("_edits", {}),
                "online": bool(m.get("_online")), **self._art_urls(r)}

    def meta_fetch(self, game_id: int, full: bool = False) -> dict:
        """Opis z sieci w tle (`full` — także TheGamesDB, limit miesięczny)."""
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}

        def run():
            try:
                metadata.fetch_online(self._cfg, g, tgdb=bool(full))
            except Exception:
                log.exception("metadane %s", g["name"])
        threading.Thread(target=run, daemon=True, name="meta").start()
        return {"ok": True}

    def meta_auto(self, game_id: int) -> None:
        """Urządzenie oglądało grę bez opisu z sieci — pobranie w tle (bez TheGamesDB, który
        ma limit miesięczny), najwyżej raz na godzinę dla gry."""
        import time
        asked = self.__dict__.setdefault("_meta_asked", {})
        if time.monotonic() - asked.get(game_id, -1e9) < 3600:
            return
        asked[game_id] = time.monotonic()
        self.meta_fetch(game_id, False)

    def meta_save(self, game_id: int, edits: dict) -> dict:
        clean = {k: str(v)[:20000] for k, v in (edits or {}).items() if k in META_FIELDS and str(v).strip()}
        metadata.set_edits(int(game_id), clean)
        return {"ok": True}

    def art_candidates(self, game_id: int, kind: str, query: str = "") -> list:
        from emustart import art_sources
        g = library.game(int(game_id))
        if not g or kind not in ("box", "snap", "logo"):
            return []
        setname = Path(g["rel"]).stem if systems.info(g["es"])["kind"] == "arcade" else ""
        return art_sources.candidates(self._cfg, g["es"], g["name"], kind, query or "", setname)

    def art_choose(self, game_id: int, kind: str, url: str) -> dict:
        from emustart import art_sources
        g = library.game(int(game_id))
        if not g or kind not in ("box", "snap", "logo"):
            return {"ok": False, "reason": "Nie ma takiej gry."}
        data = art_sources.fetch(url)
        if not art_sources.is_image(data):
            return {"ok": False, "reason": "Nie udało się pobrać tej grafiki."}
        art.save(g["es"], g["name"], kind, data)
        library.set_art(g["id"], kind, art.HAS)
        _clear_device_lists()
        return {"ok": True, "url": art.media_url(g["es"], g["name"], kind)}

    def art_clear(self, game_id: int, kind: str) -> dict:
        g = library.game(int(game_id))
        if g and kind in ("box", "snap", "logo"):
            art.delete(g["es"], g["name"], kind)
            library.set_art(g["id"], kind, art.MISSING)
            _clear_device_lists()
        return {"ok": True}

    # ── wygląd systemów (logo, nazwa, poświata) — urządzenia pokazują to samo ──
    def systems_look(self) -> list:
        import json as _json
        out = []
        merge = self._cfg.get("system_merge") or {}
        dirs = {r["es"]: r["rom_dir"] for r in library.db().execute("SELECT es, rom_dir FROM systems")}
        from emustart import emustore
        store_idx = emustore.load_index(self._cfg)          # rdzenie z buildbota i ich systemy (.info)
        for s_ in sorted(library.systems_summary(), key=lambda x: x["display"].lower()):
            es = s_["es"]
            sc = (self._cfg.get("systems") or {}).get(es) or {}
            lg = logos.url_for(es, self._cfg)
            from emustart import emucatalog, emulators
            plat = systems.info(es)["plat"]
            out.append({"es": es, "display": sc.get("name") or systems.info(es)["display"],
                        "default_name": systems.info(es)["display"], "games": s_["games"],
                        "logo": lg["url"], "glow": bool(lg["glow"]), "logo_choice": sc.get("logo", ""),
                        "enabled": sc.get("enabled", True),
                        "emu_rec": sc.get("emu_rec", ""), "core_rec": sc.get("core_rec", ""),
                        "core_default": emucatalog.CORES.get(plat, ""), "cores": list(dict.fromkeys(emulators.cores_for(plat) + emustore.alt_cores(store_idx, es))),
                        "dirs": _json.loads(dirs.get(es) or "[]"),
                        "merged": sorted(k for k, v in merge.items() if v == es)})
        return out

    def system_merge(self, src: str, dst: str) -> dict:
        """Platforma `src` scalona z `dst` (np. dwa foldery Xbox 360): gry z obu folderów na
        jednej liście, te same gry raz (pierwszeństwo — kolejność folderów z grami).
        `dst` puste — rozdzielenie. Lista gier przeładowuje się sama."""
        merge = dict(self._cfg.get("system_merge") or {})
        known = {s_["es"] for s_ in library.systems_summary()} | set(merge)
        if src not in known:
            return {"ok": False, "reason": "Nie ma takiej platformy."}
        if dst:
            if dst == src or dst not in known or dst in merge:
                return {"ok": False, "reason": "Nie można scalić z tą platformą."}
            if any(v == src for v in merge.values()):
                return {"ok": False, "reason": "Z tą platformą scalono już inne — najpierw je rozdziel."}
            merge[src] = dst
        else:
            merge.pop(src, None)
        self._cfg["system_merge"] = merge
        config.save(self._cfg)
        log.info("platformy scalone: %s", merge)
        self.rescan()
        return {"ok": True}

    def system_set(self, es: str, data: dict) -> dict:
        if es not in {s_["es"] for s_ in library.systems_summary()}:
            return {"ok": False, "reason": "Nie ma takiego systemu."}
        sc = self._cfg.setdefault("systems", {}).setdefault(es, {})
        if "name" in data:
            name = str(data["name"] or "").strip()[:60]
            if name and name != systems.info(es)["display"]:
                sc["name"] = name
            else:
                sc.pop("name", None)
        if "glow" in data:
            sc["logo_glow"] = bool(data["glow"])
        if "enabled" in data:
            sc["enabled"] = bool(data["enabled"])
        if "emu_rec" in data:                     # zalecany emulator dla urządzeń
            if data["emu_rec"] not in ("", "retroarch", "standalone"):
                return {"ok": False, "reason": "Nieznany rodzaj emulatora."}
            sc["emu_rec"] = data["emu_rec"]
        if "core_rec" in data:                    # rdzeń RetroArcha ("" = domyślny)
            core = str(data["core_rec"] or "").strip().lower()
            if core and not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,63}", core):
                return {"ok": False, "reason": "Zła nazwa rdzenia RetroArcha."}
            if core:
                sc["core_rec"] = core
            else:
                sc.pop("core_rec", None)
        config.save(self._cfg)
        return {"ok": True}

    def system_logo_candidates(self, es: str) -> list:
        return logos.candidates(self._cfg, es)

    def system_logo_choose(self, es: str, cid: str) -> dict:
        if not logos.choose(self._cfg, es, cid):
            return {"ok": False, "reason": "Nie udało się pobrać tego logo."}
        self._cfg["systems"][es].setdefault("logo_glow", False)
        config.save(self._cfg)
        return {"ok": True, **logos.url_for(es, self._cfg)}


META_FIELDS = ("title", "developer", "publisher", "year", "genre", "players", "description")
