"""savemigrate — zapisy sprzed 0.46 (foldery profili) → paczki synchronizacji (savesync).

EmuStart 0.45 trzymał zapisy w folderze profilu na serwerze:

    <profil>/save/retroarch/saves/[<rdzeń>/]<gra>.srm           zapisy w grze (PC, telefon)
    <profil>/save/retroarch/states/[<rdzeń>/]<gra>.stateN       stany z PC (synchronizacja folderu)
    <profil>/states/shared/retroarch/<rdzeń>/<gra>.stateN       stany wspólne (rastates, .states.json)
    <profil>/states/android/retroarch/[<rdzeń>/]<gra>.stateN    stany telefonu sprzed 0.31
    <profil>/save/duckstation/memcards/<gra>_1.mcd              karty DuckStation (PC, telefon)

0.46 synchronizuje je tylko paczkami (konto + platforma + gra + emulator + rdzeń + rodzaj),
a tych folderów nikt nie przenosił — urządzenia widziały różne zapisy. Migracja (jednorazowa,
idempotentna; z panelu serwera) dopasowuje pliki do gier biblioteki tak jak savebundles
(nazwy umowne „game.srm”, „game.state3.png”, „game_1.mcd”) i:

  - paczki jeszcze nie ma → zapisy sprzed 0.46 zostają jej aktualną wersją;
  - paczka jest (np. z urządzenia z 0.46) → NIE jest nadpisywana: wersja ze starego folderu
    trafia do historii paczki z opisem „zapisy sprzed 0.46 (urządzenie, data)” — do
    przywrócenia w panelu;
  - ta sama zawartość już jest (ponowne uruchomienie) → nic.

Ze starych folderów nic nie znika. Raport (ile gier, plików, czego nie dopasowano) zostaje
w <serwer>/savesync/_migration.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path

from emustart import emulators, library, savebundles, savesync, systems

log = logging.getLogger("emustart.savemigrate")

DEVICE = "migracja"                 # „urządzenie” wersji z migracji (manifest, historia)
UNMATCHED_LIMIT = 500

# nazwa folderu rdzenia (corename RetroArcha) → identyfikator rdzenia
_CORE_DIRS = {
    "beetle saturn": "mednafen_saturn", "beetle psx hw": "mednafen_psx_hw", "beetle psx": "mednafen_psx",
    "beetle pce": "mednafen_pce", "beetle pce fast": "mednafen_pce_fast", "beetle wonderswan": "mednafen_wswan",
    "beetle neopop": "mednafen_ngp", "beetle vb": "mednafen_vb", "finalburn neo": "fbneo",
    "genesis plus gx": "genesis_plus_gx", "mgba": "mgba", "swanstation": "swanstation",
    "mupen64plus-next": "mupen64plus_next", "melonds": "melonds", "melonds ds": "melondsds", "desmume": "desmume",
    "fceumm": "fceumm", "gambatte": "gambatte", "sameboy": "sameboy", "gearboy": "gearboy", "stella": "stella",
    "yabasanshiro": "yabasanshiro", "ppsspp": "ppsspp", "puae": "puae", "picodrive": "picodrive",
    "snes9x": "snes9x", "snes9x 2010": "snes9x2010", "snes9x 2005": "snes9x2005", "snes9x 2005 plus": "snes9x2005_plus",
    "snes9x 2002": "snes9x2002", "bsnes": "bsnes", "bsnes-hd beta": "bsnes_hd_beta", "bsnes-mercury accuracy":
    "bsnes_mercury_accuracy", "bsnes-mercury balanced": "bsnes_mercury_balanced", "bsnes-mercury performance":
    "bsnes_mercury_performance", "bsnes 2014 accuracy": "bsnes2014_accuracy", "mesen-s": "mesen-s",
    "nestopia": "nestopia", "mesen": "mesen", "quicknes": "quicknes", "opera": "opera", "flycast": "flycast",
    "mame": "mame", "vice x64": "vice_x64", "dosbox-pure": "dosbox_pure", "pcsx-rearmed": "pcsx_rearmed",
    "parallel n64": "parallel_n64", "handy": "handy", "prosystem": "prosystem",
}
_RA_STATE = re.compile(r"(.+?)(\.state\d*(?:\.auto)?(?:\.png)?)", re.I)


def core_of_dir(name: str) -> str:
    n = name.strip().lower()
    return _CORE_DIRS.get(n) or re.sub(r"[^a-z0-9.+-]+", "_", n).strip("_")


def _norm(s: str) -> str:
    return savebundles.norm(s)


def _bare(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[([].*?[)\]]", " ", name.replace("_", " "))).strip().lower()


class _Games:
    """Gry biblioteki serwera: po nazwie (gry, pliku) i — karty PS1 — po tytule bez dopisków."""

    def __init__(self):
        self.by_name: dict = {}
        self.by_bare: dict = {}
        for r in library.db().execute("SELECT es, name, rel FROM games WHERE hidden=0 OR hidden IS NULL"):
            g = (r["es"], r["name"])
            for k in {_norm(r["name"]), _norm(Path(r["rel"]).stem)}:
                self.by_name.setdefault(k, set()).add(g)
            if systems.info(r["es"]).get("plat") == "PS1":
                self.by_bare.setdefault(_bare(r["name"]), set()).add(g)

    def find(self, stem: str, core: str = "") -> list:
        hits = sorted(self.by_name.get(_norm(stem), set()))
        plats = (emulators.CORE_PLATS.get(core) or "").split()
        if plats:
            hits = [g for g in hits if systems.info(g[0]).get("plat") in plats]
        return hits


def _stamp_device(path: Path) -> str:
    """Urządzenie z znacznika synchronizacji folderu (`<czas>-<komputer>`)."""
    try:
        t = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    m = re.match(r"^[\d.]+-(.+?)(?:-menedzer)?$", t)
    return m.group(1) if m else ""


def _walk(base: Path):
    if not base.is_dir():
        return
    for root, _dirs, files in os.walk(base):
        for f in files:
            if not f.startswith(".") and not f.endswith((".emustart-tmp", ".emustart-new", ".tmp", ".part")):
                yield Path(root) / f


def _collect(prof: Path, games: _Games, report: dict) -> dict:
    """{(es, nazwa, emulator, rdzeń, rodzaj): {"files": {nazwa w paczce: (plik, mtime)}, "devices": set}}."""
    buckets: dict = {}
    rel = lambda p: p.relative_to(prof.parent).as_posix()

    def unmatched(p: Path, why: str) -> None:
        report["unmatched_count"] += 1
        if len(report["unmatched"]) < UNMATCHED_LIMIT:
            report["unmatched"].append({"file": rel(p), "reason": why})

    def put(key, arc, p: Path, device: str) -> None:
        b = buckets.setdefault(key, {"files": {}, "devices": set()})
        mt = p.stat().st_mtime
        cur = b["files"].get(arc)
        if cur is None or mt > cur[1]:
            b["files"][arc] = (p, mt)
        if device:
            b["devices"].add(device)

    def retroarch(base: Path, kind: str, device: str, index: dict | None = None) -> None:
        for p in _walk(base):
            parts = p.relative_to(base).parts
            core = core_of_dir(parts[-2]) if len(parts) >= 2 else ""
            name = p.name
            if kind == "saves":
                stem, _, ext = name.rpartition(".")
                if not stem or ext.lower() not in savebundles.RA_SAVE_EXTS:
                    continue
                arc = f"game.{ext.lower()}"
            else:
                m = _RA_STATE.fullmatch(name)
                if not m:
                    continue
                stem, arc = m.group(1), "game" + m.group(2).lower()
            hits = games.find(stem, core)
            if not hits:
                unmatched(p, "nie ma takiej gry w bibliotece" + (f" (rdzeń {core})" if core else ""))
                continue
            for es, gname in hits:
                c = core
                if not c:
                    groups = {g for g, mem in savebundles.SAVE_GROUPS.items()
                              if any(systems.info(es).get("plat") in (emulators.CORE_PLATS.get(x) or "").split() for x in mem)}
                    if kind == "saves" and len(groups) == 1:
                        c = groups.pop()            # zapis bez folderu rdzenia — wspólny dla grupy rdzeni
                    else:
                        unmatched(p, "bez folderu rdzenia — nie wiadomo, którego rdzenia to dane")
                        continue
                if kind == "saves":
                    c = savebundles.save_core(c)
                dev = ((index or {}).get(name) or {}).get("device") or device
                put((es, gname, "retroarch", c, kind), arc, p, dev)

    ra = prof / "save" / "retroarch"
    dev_ra = _stamp_device(ra / ".emustart-stamp")
    retroarch(ra / "saves", "saves", dev_ra)
    retroarch(ra / "states", "states", dev_ra)
    shared = prof / "states" / "shared" / "retroarch"
    idx: dict = {}                                     # opisy stanów wspólnych (rastates): urządzenie
    for f in shared.glob("*/.states.json") if shared.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            idx.update(d if isinstance(d, dict) else {})
        except (OSError, ValueError):
            pass
    retroarch(shared, "states", "", idx)
    retroarch(prof / "states" / "android" / "retroarch", "states", "telefon")

    mc = prof / "save" / "duckstation" / "memcards"
    dev_mc = _stamp_device(prof / "save" / "duckstation" / ".emustart-stamp")
    for p in _walk(mc):
        m = re.fullmatch(r"(.+)(_[12])\.mcd", p.name, re.I)
        if not m:
            unmatched(p, "karta wspólna dla gier — nie należy do jednej gry")
            continue
        hits = sorted(games.by_bare.get(_bare(m.group(1)), set()))
        if not hits:
            unmatched(p, "nie ma takiej gry PS1 w bibliotece")
            continue
        for es, gname in hits:
            put((es, gname, "duckstation", "", "memcards"), f"game{m.group(2)}.mcd", p, dev_mc)
    for p in _walk(prof / "save" / "pcsx2" / "memcards"):
        unmatched(p, "karta PCSX2 wspólna dla gier — od 0.46 karta osobna dla gry powstaje z niej na komputerze")
    return buckets


def _zip(files: dict) -> Path:
    fd, tmp = tempfile.mkstemp(prefix="emustart-migracja-", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, (p, mt) in sorted(files.items()):
            zi = zipfile.ZipInfo(arc, time.localtime(max(mt, 315532800))[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as src, z.open(zi, "w") as dst:
                for buf in iter(lambda: src.read(1 << 20), b""):
                    dst.write(buf)
    return Path(tmp)


def _report_file() -> Path:
    return savesync._root() / "_migration.json"


def last_report() -> dict:
    try:
        return json.loads(_report_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run(nas_root: Path, dry: bool = False, accounts: list | None = None) -> dict:
    """Migracja wszystkich kont (folderów profili). `dry` — tylko raport, bez zmian."""
    nas_root = Path(nas_root)
    report = {"time": time.time(), "dry": dry, "accounts": 0, "games": 0, "files": 0, "new": 0,
              "history": 0, "same": 0, "errors": 0, "items": [], "unmatched": [], "unmatched_count": 0}
    games = _Games()
    profs = [d for d in sorted(nas_root.iterdir()) if d.is_dir() and not d.name.startswith(("_", "."))] \
        if nas_root.is_dir() else []
    seen_games = set()
    for prof in profs:
        account = prof.name
        if accounts and account.lower() not in {a.lower() for a in accounts}:
            continue
        if (prof / "moved.json").exists():
            continue                                    # folder po zmianie nazwy profilu
        buckets = _collect(prof, games, report)
        if not buckets:
            continue
        report["accounts"] += 1
        for (es, gname, emu, core, kind), b in sorted(buckets.items()):
            newest = max(mt for _p, mt in b["files"].values())
            devs = ", ".join(sorted(b["devices"])) or "urządzenie nieznane"
            note = f"zapisy sprzed 0.46 ({devs}, {time.strftime('%d.%m.%Y %H:%M', time.localtime(newest))})"
            item = {"account": account, "es": es, "game": gname, "emulator": emu, "core": core, "kind": kind,
                    "files": len(b["files"]), "note": note, "game_id": savesync.game_id(es, gname)}
            seen_games.add((account.lower(), es, gname))
            report["files"] += len(b["files"])
            if dry:
                item["result"] = "plan"
            else:
                z = _zip(b["files"])
                try:
                    item["result"] = savesync.import_bundle(account, es.lower(), item["game_id"], emu, core, kind,
                                                            z, DEVICE, note, newest)
                    report[item["result"]] += 1
                except Exception as ex:
                    log.exception("migracja %s %s/%s", account, es, gname)
                    item["result"], item["error"] = "error", str(ex)
                    report["errors"] += 1
                finally:
                    z.unlink(missing_ok=True)
            report["items"].append(item)
    report["games"] = len(seen_games)
    if not dry:
        report["folded"] = fold_all()
        _report_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = _report_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, _report_file())
    log.info("migracja zapisów sprzed 0.46%s: kont %d, gier %d, plików %d — nowe %d, do historii %d, "
             "bez zmian %d, błędy %d, niedopasowane %d", " (próba)" if dry else "", report["accounts"],
             report["games"], report["files"], report["new"], report["history"], report["same"],
             report["errors"], report["unmatched_count"])
    return report


def fold_all() -> int:
    """Zapisy rdzeni sprzed 0.47 → wspólne paczki grup zgodnych rdzeni, we wszystkich
    manifestach (dzieje się też samo przy pierwszym dostępie do gry). Liczba zmienionych gier."""
    n = 0
    for mf in savesync._root().glob("a*/*/*/manifest.json"):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and savesync._fold_groups(m):
            tmp = mf.with_suffix(".tmp")
            tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, mf)
            n += 1
    return n


def legacy_versions(account: str) -> list:
    """Dla panelu: wersje z migracji w historii paczek konta (do przywrócenia)."""
    out = []
    rep = last_report()
    for it in rep.get("items") or []:
        if it.get("account", "").lower() != account.lower() or it.get("result") != "history":
            continue
        for b in savesync.history(account, it["es"].lower(), it["game_id"]):
            if (b["emulator_id"], b["core_id"], b["bundle_kind"]) != (it["emulator"], it["core"], it["kind"]):
                continue
            for h in b["history"]:
                if h.get("imported"):
                    out.append({**it, "bundle_id": h["bundle_id"], "current_revision": b["revision"],
                                "current_device": b.get("updated_by_device_id"), "current_time": b.get("updated_at")})
    return out


def main(argv=None) -> int:
    """Próba na kopii danych serwera: python -m emustart.savemigrate --server <kopia\\server>
    [--profiles <folder profili>] [--dry]. Pisze tylko w podanej kopii."""
    import argparse
    from emustart import paths
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="kopia folderu server (savesync, library.sqlite)")
    ap.add_argument("--profiles", help="folder profili (domyślnie <server>/profiles)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    srv = Path(a.server)
    paths.SERVER = srv
    paths.DB_PATH = srv / "library.sqlite"
    library._local.con = None
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rep = run(Path(a.profiles) if a.profiles else srv / "profiles", dry=a.dry)
    print(json.dumps({k: v for k, v in rep.items() if k not in ("items", "unmatched")}, ensure_ascii=False, indent=1))
    for it in rep["items"]:
        print(f"{it['result']:8} {it['account']} {it['es']}/{it['game']} {it['emulator']}/{it['core'] or '-'}/{it['kind']} "
              f"({it['files']} plików) — {it['note']}")
    for u in rep["unmatched"][:50]:
        print(f"nie dopasowano: {u['file']} — {u['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
