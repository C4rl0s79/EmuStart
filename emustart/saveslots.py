"""saveslots — sloty zapisów gry na tym urządzeniu (klient; serwer: savesync.py).

Slot to jedno „przejście” gry: komplet paczek zapisów (savebundles — stany, zapisy w grze,
karty pamięci). Gra ma 4 sloty, jeden aktywny (wspólny dla urządzeń konta):

    aktywny slot      foldery emulatora (jak przed slotami — emulator niczego nie zauważa)
    pozostałe sloty   <profil>/_sloty/<platforma>/<gra>/<slot>/<emulator>-<rdzeń>-<rodzaj>/
    kosz slotu        <profil>/_kosz/<platforma>/<gra>/<slot>/<emulator>-<rdzeń>/

W folderze profilu pliki mają nazwy umowne paczki („game.state3”), w folderach emulatora —
od pliku gry. Wszystkie sloty i kosze synchronizują się z serwerem (syncclient.before).

Zmiana slotu przenosi pliki: z folderów emulatora do folderu slotu, który przestaje być
aktywny, i z folderu nowego slotu do folderów emulatora — trzy kroki ze znacznikiem w bazie,
przerwana zamiana kończy się przy następnym uruchomieniu.

Edycja stanów (opcje gry → „Wczytaj zapis” → przytrzymane A): zmień numer, kopiuj jako nowy
numer, kopiuj / przenieś do slotu, ustaw jako „Kontynuuj”, usuń (do kosza), przywróć z kosza.
Stan usunięty albo przenumerowany zostaje zapisany jako para (nazwa, SHA-256) — sumowanie
stanów z innego urządzenia (savebundles.merge_states) go nie przywraca.

Straż numerów (`Guard`): po sesji emulatora stan o istniejącym numerze z inną treścią dostaje
kolejny wolny numer, a poprzedni wraca na swój (z kopii sprzed sesji) — zapis po wczytaniu
slotu „Auto” w RetroArchu (slot −1 → 0 → 1…) niczego nie nadpisze.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from emustart import library, savebundles

log = logging.getLogger("emustart.saveslots")

SLOTS = (1, 2, 3, 4)
TRASH_KEEP = 10
AUTO = "game.state.auto"                 # „Kontynuuj” RetroArcha (autozapis)
_RA = re.compile(r"^game\.state(\d*)$")
_P2S = re.compile(r"^(?P<prefix>.+?)\.(?P<slot>\d{2})\.p2s$", re.I)


class EditError(ValueError):
    """Zmiana stanu niemożliwa — powód dla gracza."""


# ── stan lokalny ──

def _db():
    c = library.db()
    c.execute("""CREATE TABLE IF NOT EXISTS save_slot(
        profile_id INTEGER NOT NULL, platform TEXT NOT NULL, game TEXT NOT NULL,
        here INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, active_at REAL NOT NULL DEFAULT 0,
        sent INTEGER NOT NULL DEFAULT 1, swap TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(profile_id, platform, game))""")
    return c


def info(pid: int, plat: str, gid: str) -> dict:
    """{here: slot w folderach emulatora, active: slot wybrany, active_at, sent, swap}."""
    r = _db().execute("SELECT * FROM save_slot WHERE profile_id=? AND platform=? AND game=?",
                      (pid, plat, gid)).fetchone()
    return dict(r) if r else {"here": 1, "active": 1, "active_at": 0.0, "sent": 1, "swap": ""}


def _put(pid: int, plat: str, gid: str, **kw) -> None:
    row = {**info(pid, plat, gid), **kw}
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO save_slot(profile_id, platform, game, here, active, active_at, sent, swap) "
                  "VALUES(?,?,?,?,?,?,?,?)", (pid, plat, gid, int(row["here"]), int(row["active"]),
                                              float(row["active_at"]), int(row["sent"]), row["swap"] or ""))


def here(pid: int, plat: str, gid: str) -> int:
    return int(info(pid, plat, gid)["here"])


# ── foldery slotów w profilu ──

def _root(pid: int, what: str, plat: str, gid: str, slot: int) -> Path:
    from emustart import profiles
    return profiles.LOCAL / str(pid) / what / plat / gid / str(int(slot))


def store_bundle(pid: int, plat: str, gid: str, slot: int, b) -> savebundles.Bundle:
    """Paczka `b` slotu `slot` w folderze profilu (nazwy umowne: pliki „game.*”)."""
    folder = _root(pid, "_sloty", plat, gid, slot) / f"{b.emulator_id}-{b.core_id or '-'}-{b.kind}"
    native = (b.emulator_id, b.kind) in savebundles.NATIVE      # nazwy jak na urządzeniu (PCSX2, Dolphin, PPSSPP)
    return savebundles.Bundle(b.emulator_id, b.core_id, b.kind, folder,
                              (b.stem if native else savebundles.ARC),
                              list(b.names) if native else [savebundles.ARC]).collect()


def trash_bundle(pid: int, plat: str, gid: str, slot: int, states) -> savebundles.Bundle:
    folder = _root(pid, "_kosz", plat, gid, slot) / f"{states.emulator_id}-{states.core_id or '-'}"
    return savebundles.Bundle(states.emulator_id, states.core_id, "trash", folder, "", []).collect()


def other_bundles(pid: int, plat: str, gid: str, bundles: list, at: int) -> list:
    """[(paczka, slot)] do synchronizacji poza aktywnym slotem: te same paczki w pozostałych
    slotach i kosze stanów wszystkich slotów."""
    out = []
    for n in SLOTS:
        if n != at:
            out += [(store_bundle(pid, plat, gid, n, b), n) for b in bundles]
        out += [(trash_bundle(pid, plat, gid, n, b), n) for b in bundles if b.kind == "states"]
    return out


def bundle_in(pid: int, plat: str, gid: str, bundles: list, slot: int, kind: str = "states"):
    """Paczka rodzaju `kind` slotu `slot`: z folderów emulatora (slot aktywny) albo z profilu."""
    b = next((x for x in bundles if x.kind == kind), None)
    if b is None:
        return None
    return b.collect() if slot == here(pid, plat, gid) else store_bundle(pid, plat, gid, slot, b)


# ── zmiana slotu ──

def finish_swap(pid: int, plat: str, gid: str, bundles: list) -> int:
    """Przerwana zamiana slotów (np. zamknięty program) — dokończenie od zapisanego kroku.
    Zwraca slot w folderach emulatora."""
    row = info(pid, plat, gid)
    if row["swap"]:
        try:
            st = json.loads(row["swap"])
            _swap_files(pid, plat, gid, bundles, int(st["from"]), int(st["to"]), int(st.get("step") or 2))
        except (ValueError, KeyError, OSError):
            log.exception("dokończenie zamiany slotów %s/%s", plat, gid)
    return here(pid, plat, gid)


class SwapError(OSError):
    """Kopia plików slotu niezgodna z oryginałem — zamiana wstrzymana, nic nie skasowane."""


def _same(a, b) -> bool:
    """Ten sam komplet plików (nazwy w paczce i treść) — kopia sprawdzona przed kasowaniem."""
    return savebundles.content_hash(a.collect().files) == savebundles.content_hash(b.collect().files)


def swap(pid: int, plat: str, gid: str, bundles: list, to: int) -> None:
    """Pliki slotu `to` do folderów emulatora; bieżące — do folderu swojego slotu.

    Kolejność (nic nie jest kasowane, zanim nie ma sprawdzonej kopii):
      1. pliki emulatora → folder slotu `frm` (wcześniejsze pliki tam — do kopii zapasowej);
         kopia porównana z oryginałem; znacznik {from, to, step 2};
      2. pliki slotu `to` → foldery emulatora, pliki emulatora spoza slotu `to` usunięte
         (są w slocie `frm` — sprawdzone w kroku 1); wynik porównany ze slotem `to`;
         znacznik {step 3};
      3. folder slotu `to` wyczyszczony, slot `to` aktywny, znacznik zdjęty.
    Przerwanie: przed znacznikiem — pliki emulatora nietknięte; w kroku 2 — powtórka kroku 2
    (folder `to` jeszcze pełny); w kroku 3 — tylko krok 3 (emulator ma już komplet `to`)."""
    to = int(to)
    if to not in SLOTS:
        raise EditError("Nie ma takiego slotu.")
    frm = finish_swap(pid, plat, gid, bundles)
    if frm == to:
        return
    if info(pid, plat, gid)["swap"]:
        raise SwapError("poprzednia zamiana slotów nie skończyła się — pliki zostają bez zmian")
    for b in bundles:
        b.collect()
        sb = store_bundle(pid, plat, gid, frm, b)
        if sb.files:                     # nie powinno ich być (slot `frm` jest w emulatorze) — nie kasujemy
            _backup(pid, sb, f"slot{frm}-przed-zamiana")
            for p, _arc in sb.files:
                Path(p).unlink(missing_ok=True)
        sb.folder.mkdir(parents=True, exist_ok=True)
        for p, arc in b.files:
            _copy(Path(p), sb.local_path(arc))
        if not _same(sb, b):
            raise SwapError(f"kopia slotu {frm} niezgodna z plikami emulatora ({b.kind}) — zamiana wstrzymana")
    _put(pid, plat, gid, swap=json.dumps({"from": frm, "to": to, "step": 2}))
    _swap_files(pid, plat, gid, bundles, frm, to, 2)
    log.info("sloty %s/%s: aktywny slot %d → %d", plat, gid, frm, to)


def _swap_files(pid: int, plat: str, gid: str, bundles: list, frm: int, to: int, step: int) -> None:
    from emustart import syncclient
    if step <= 2:
        # 2. pliki nowego slotu do folderów emulatora (podmiana przez plik tymczasowy)
        for b in bundles:
            b.collect()
            tb = store_bundle(pid, plat, gid, to, b)
            new = {arc: Path(p) for p, arc in tb.files}
            b.folder.mkdir(parents=True, exist_ok=True)
            for arc, src in new.items():
                _copy(src, b.local_path(arc))
            for p, arc in b.files:
                if arc not in new:
                    Path(p).unlink(missing_ok=True)
            if not _same(b, tb):
                raise SwapError(f"pliki slotu {to} w emulatorze niezgodne z kopią ({b.kind}) — dokończę później")
        _put(pid, plat, gid, swap=json.dumps({"from": frm, "to": to, "step": 3}))
    # 3. folder nowego slotu pusty; synchronizacja wie, gdzie są pliki obu slotów
    for b in bundles:
        tb = store_bundle(pid, plat, gid, to, b)
        for p, _arc in tb.files:
            Path(p).unlink(missing_ok=True)
        syncclient.respec(pid, plat, gid, frm, store_bundle(pid, plat, gid, frm, b))
        syncclient.respec(pid, plat, gid, to, b.collect())
    _put(pid, plat, gid, here=to, swap="")


def _backup(pid: int, b, why: str) -> None:
    from emustart import syncclient
    bak = syncclient._backup_dir(pid) / time.strftime("%Y%m%d-%H%M%S") / f"{why}-{b.emulator_id}-{b.core_id or '-'}-{b.kind}"
    bak.mkdir(parents=True, exist_ok=True)
    for p, _arc in b.files:
        shutil.copy2(p, bak / Path(p).name)
    syncclient._prune_backups(pid)


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".emustart-new")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def set_active(cfg: dict, pid: int, game: dict, bundles: list, slot: int) -> dict:
    """Gracz wybrał slot: zamiana plików od razu, wybór na serwer (bez połączenia — później).
    {ok, slot, sent}."""
    from emustart import syncclient
    plat, gid = syncclient.game_ids(game)
    slot = int(slot)
    if slot not in SLOTS:
        raise EditError("Nie ma takiego slotu.")
    try:
        with syncclient._lock:           # nie w trakcie synchronizacji tej gry
            swap(pid, plat, gid, bundles, slot)
    except SwapError as ex:
        raise EditError(f"Nie zmieniono slotu: {ex}. Zapisy zostały bez zmian.")
    _put(pid, plat, gid, active=slot, active_at=time.time(), sent=0)
    sent = _send_active(cfg, pid, plat, gid)
    return {"ok": True, "slot": slot, "sent": sent}


def _send_active(cfg: dict, pid: int, plat: str, gid: str) -> bool:
    from emustart import syncclient
    row = info(pid, plat, gid)
    try:
        cl = syncclient._Client(cfg, pid)
        if not cl.slots_ok(plat, gid):
            return False
        r = cl.json("POST", f"/v1/savesync/{plat}/{gid}/slot", {"slot": row["active"], "at": row["active_at"]})
    except (syncclient.Offline, syncclient.ConflictError) as ex:
        log.info("slot %s/%s czeka na wysłanie: %s", plat, gid, ex)
        return False
    _put(pid, plat, gid, sent=1, active=int(r.get("active_slot") or row["active"]),
         active_at=float(r.get("active_at") or row["active_at"]))
    return True


def reconcile_active(cl, pid: int, plat: str, gid: str, man: dict) -> int:
    """Aktywny slot po synchronizacji: niewysłana zmiana z tego urządzenia, jeśli późniejsza niż
    na serwerze (wysłana teraz), inaczej — slot z serwera."""
    row = info(pid, plat, gid)
    srv, srv_at = int(man.get("active_slot") or 1), float(man.get("active_at") or 0)
    if not row["sent"] and row["active_at"] > srv_at:
        try:
            r = cl.json("POST", f"/v1/savesync/{plat}/{gid}/slot", {"slot": row["active"], "at": row["active_at"]})
            srv, srv_at = int(r.get("active_slot") or row["active"]), float(r.get("active_at") or row["active_at"])
        except Exception as ex:
            log.info("slot %s/%s: wysyłka odłożona (%s)", plat, gid, ex)
            return int(row["active"])
    _put(pid, plat, gid, active=srv, active_at=srv_at, sent=1)
    return srv


def retry_active(cfg: dict) -> int:
    rows = [dict(r) for r in _db().execute("SELECT * FROM save_slot WHERE sent=0")]
    return sum(_send_active(cfg, r["profile_id"], r["platform"], r["game"]) for r in rows)


# ── numery stanów ──

def state_no(arc: str) -> int | None:
    """Numer stanu w paczce: „game.state” = 0, „game.state7” = 7, PCSX2 „….03.p2s” = 3;
    „Kontynuuj” i miniatury — None."""
    m = _RA.match(arc)
    if m:
        return int(m.group(1) or 0)
    m = _P2S.match(arc) or savebundles._DOLPHIN_STATE.match(arc) or savebundles._PPSSPP_STATE.match(arc)
    return int(m.group("slot")) if m else None


def _arc_no(b, like: str, n: int) -> str:
    if b.emulator_id == "pcsx2":
        m = _P2S.match(like) or re.match(r"^(?P<prefix>.+?)\.(resume|autosave(\.\d)?)\.p2s$", like, re.I)
        if not m or not 0 <= n <= 9:
            raise EditError("Stany PS2 mają numery 0–9.")
        return f"{m.group('prefix')}.{n:02d}.p2s"
    if b.emulator_id == "dolphin":
        m = savebundles._DOLPHIN_STATE.match(like)
        if not m or not 1 <= n <= 99:
            raise EditError("Stany Dolphina mają numery 1–99.")
        return f"{m.group('prefix')}.s{n:02d}"
    if b.emulator_id == "ppsspp":
        m = savebundles._PPSSPP_STATE.match(like)
        if not m or not 0 <= n <= 99:
            raise EditError("Stany PPSSPP mają numery 0–99.")
        return f"{m.group('prefix')}_{n}.ppst"
    if n < 0:
        raise EditError("Nieprawidłowy numer.")
    return "game.state" if n == 0 else f"game.state{n}"


def _states(b) -> dict:
    """{nazwa w paczce: ścieżka} stanów (bez miniatur)."""
    return {arc: Path(p) for p, arc in b.collect().files if not arc.endswith(".png")}


def used_numbers(b) -> set:
    return {n for n in (state_no(a) for a in _states(b)) if n is not None}


def next_free(b, like: str) -> str:
    """Nazwa dla nowego stanu: RetroArch — po najwyższym numerze (jak savestate_auto_index);
    PCSX2 — pierwszy wolny 0–9."""
    used = used_numbers(b)
    if b.emulator_id == "pcsx2":
        free = [n for n in range(10) if n not in used]
        if not free:
            raise EditError("Wszystkie numery stanów PS2 (0–9) są zajęte.")
        return _arc_no(b, like, free[0])
    if b.emulator_id in ("dolphin", "ppsspp"):      # pierwszy wolny (klawisze emulatora: niskie numery)
        lo = 1 if b.emulator_id == "dolphin" else 0
        return _arc_no(b, like, next(n for n in range(lo, 100) if n not in used))
    return _arc_no(b, like, max(used, default=0) + 1)


def free_numbers(b, limit: int = 12) -> list:
    """Wolne numery do „Zmień numer”: RetroArch od 1 (0 — gdy wolny) do kilku za najwyższym."""
    used = used_numbers(b)
    if b.emulator_id == "pcsx2":
        return [n for n in range(10) if n not in used]
    if b.emulator_id in ("dolphin", "ppsspp"):
        lo = 1 if b.emulator_id == "dolphin" else 0
        return [n for n in range(lo, 100) if n not in used][:limit]
    top = max(used, default=0)
    return [n for n in range(0, top + limit) if n not in used][:limit]


def _move_with_png(b_from, arc_from: str, b_to, arc_to: str, copy: bool = False) -> None:
    src = b_from.local_path(arc_from)
    dst = b_to.local_path(arc_to)
    if not src.is_file():
        raise EditError("Tego stanu już nie ma.")
    if dst.exists():
        raise EditError("Ten numer jest zajęty.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    pairs = [(src, dst)]
    png = src.with_name(src.name + ".png")
    if png.is_file():
        pairs.append((png, dst.with_name(dst.name + ".png")))
    for s, d in pairs:
        if copy:
            _copy(s, d)
        else:
            shutil.move(str(s), str(d))


# ── edycja stanów (opcje gry) ──

def _edit(fn):
    """Zmiana plików stanów pod blokadą synchronizacji (pobieranie w tle — pull_all, before — nie
    wchodzi w jej środek), wysyłka zmienionych paczek na serwer dopiero po niej."""
    @functools.wraps(fn)
    def run(self, *a, **k):
        from emustart import syncclient
        self._items = []
        with syncclient._lock:
            out = fn(self, *a, **k)
        if not self._items:
            return out
        r = syncclient.push_edit(self.cfg, self.pid, self.game, self._items)
        return {**out, "ok": True, "offline": r.offline}
    return run


class Editor:
    """Zmiany stanów aktywnego slotu gry; każda od razu na serwer (syncclient.push_edit)."""

    def __init__(self, cfg: dict, pid: int, game: dict, bundles: list):
        from emustart import syncclient
        self.cfg, self.pid, self.game = cfg, pid, game
        self.plat, self.gid = syncclient.game_ids(game)
        self.bundles = bundles
        self._items: list = []
        with syncclient._lock:
            self.slot = finish_swap(pid, self.plat, self.gid, bundles)
        self.st = next((b for b in bundles if b.kind == "states"), None)
        if self.st is None:
            raise EditError("Ten emulator nie ma stanów tej gry w zapisach.")
        self.st.collect()

    def _sha(self, b, arc: str) -> str:
        return savebundles.file_sha(b.local_path(arc))

    def _ops(self, b, slot: int, add=(), drop=()) -> None:
        from emustart import syncclient
        syncclient.add_ops(self.pid, self.plat, self.gid, b, slot, add, drop)

    def _push(self, items: list) -> dict:
        """Paczki do wysłania po zmianie (wysyła `_edit`, już bez blokady)."""
        self._items += items
        return {"ok": True, "offline": ""}

    def _need(self, arc: str) -> None:
        if arc not in _states(self.st):
            raise EditError("Tego stanu już nie ma.")

    def numbers(self) -> list:
        return free_numbers(self.st)

    def slots(self) -> list:
        return [n for n in SLOTS if n != self.slot]

    @_edit
    def renumber(self, arc: str, n: int) -> dict:
        self._need(arc)
        if arc == AUTO:
            raise EditError("„Kontynuuj” nie ma numeru — skopiuj go jako nowy numer.")
        new = _arc_no(self.st, arc, int(n))
        if new == arc:
            return {"ok": True, "arc": arc}
        sha = self._sha(self.st, arc)
        _move_with_png(self.st, arc, self.st, new)
        self._ops(self.st, self.slot, add=[{"sha256": sha, "name": arc}], drop=[{"sha256": sha, "name": new}])
        log.info("stany %s/%s slot %d: %s → %s", self.plat, self.gid, self.slot, arc, new)
        return {**self._push([(self.st, self.slot)]), "arc": new}

    @_edit
    def copy_new(self, arc: str) -> dict:
        self._need(arc)
        new = next_free(self.st, arc)
        _move_with_png(self.st, arc, self.st, new, copy=True)
        return {**self._push([(self.st, self.slot)]), "arc": new}

    @_edit
    def to_slot(self, arc: str, slot: int, move: bool = False) -> dict:
        self._need(arc)
        slot = int(slot)
        if slot not in SLOTS or slot == self.slot:
            raise EditError("Wybierz inny slot.")
        tb = store_bundle(self.pid, self.plat, self.gid, slot, self.st)
        new = next_free(tb, arc)
        sha = self._sha(self.st, arc)
        _move_with_png(self.st, arc, tb, new, copy=True)
        items = [(tb, slot)]
        if move:
            self._remove(arc, sha)
            self._ops(self.st, self.slot, add=[{"sha256": sha, "name": arc}])
            items.insert(0, (self.st, self.slot))
        log.info("stany %s/%s: %s slot %d → %s slot %d%s", self.plat, self.gid, arc, self.slot, new, slot,
                 " (przeniesiony)" if move else "")
        return {**self._push(items), "arc": new, "slot": slot}

    def _remove(self, arc: str, _sha: str) -> None:
        p = self.st.local_path(arc)
        p.unlink(missing_ok=True)
        p.with_name(p.name + ".png").unlink(missing_ok=True)

    @_edit
    def set_continue(self, arc: str) -> dict:
        """Stan jako „Kontynuuj” (autozapis wczytywany przy „Graj”): kopia; dotychczasowy
        „Kontynuuj” dostaje nowy numer (nic nie ginie)."""
        self._need(arc)
        if self.st.emulator_id != "retroarch":
            raise EditError("„Kontynuuj” jest tylko w RetroArchu.")
        if arc == AUTO:
            return {"ok": True, "arc": AUTO}
        sha = self._sha(self.st, arc)
        add = []
        if AUTO in _states(self.st):
            old = self._sha(self.st, AUTO)
            if old == sha:
                return {"ok": True, "arc": AUTO}
            new = next_free(self.st, arc)
            _move_with_png(self.st, AUTO, self.st, new)
            add.append({"sha256": old, "name": AUTO})
        _move_with_png(self.st, arc, self.st, AUTO, copy=True)
        self._ops(self.st, self.slot, add=add, drop=[{"sha256": sha, "name": AUTO}])
        return {**self._push([(self.st, self.slot)]), "arc": AUTO}

    @_edit
    def delete(self, arc: str) -> dict:
        """Stan do kosza slotu (synchronizowany — cofnięcie także na innym urządzeniu)."""
        self._need(arc)
        sha = self._sha(self.st, arc)
        tb = trash_bundle(self.pid, self.plat, self.gid, self.slot, self.st)
        name = f"{sha[:12]}.{arc}"
        if tb.local_path(name).exists():
            self._remove(arc, sha)
        else:
            _move_with_png(self.st, arc, tb, name)             # czas zapisu zostaje (po przywróceniu też)
        self._ops(self.st, self.slot, add=[{"sha256": sha, "name": arc}])
        self._ops(tb, self.slot, drop=[{"sha256": sha, "name": name}])
        self._prune_trash(tb)
        log.info("stany %s/%s slot %d: %s → kosz", self.plat, self.gid, self.slot, arc)
        return self._push([(self.st, self.slot), (tb, self.slot)])

    def _prune_trash(self, tb) -> None:
        items = sorted(_states(tb).items(), key=lambda x: x[1].stat().st_mtime, reverse=True)
        for name, p in items[TRASH_KEEP:]:
            self._ops(tb, self.slot, add=[{"sha256": savebundles.file_sha(p), "name": name}])
            p.unlink(missing_ok=True)
            p.with_name(p.name + ".png").unlink(missing_ok=True)

    def trash(self) -> list:
        """Kosz aktywnego slotu, od najnowszego zapisu: [{name, arc, time, path}]."""
        tb = trash_bundle(self.pid, self.plat, self.gid, self.slot, self.st)
        out = [{"name": n, "arc": n[13:], "time": p.stat().st_mtime, "path": str(p)} for n, p in _states(tb).items()]
        return sorted(out, key=lambda x: -x["time"])

    @_edit
    def restore(self, name: str) -> dict:
        tb = trash_bundle(self.pid, self.plat, self.gid, self.slot, self.st)
        if name not in _states(tb):
            raise EditError("Tego stanu nie ma już w koszu.")
        arc = name[13:]
        sha = savebundles.file_sha(tb.local_path(name))
        target = arc if arc not in _states(self.st) else next_free(self.st, arc)
        _move_with_png(tb, name, self.st, target)
        self._ops(self.st, self.slot, drop=[{"sha256": sha, "name": target}])
        self._ops(tb, self.slot, add=[{"sha256": sha, "name": name}])
        log.info("stany %s/%s slot %d: %s z kosza → %s", self.plat, self.gid, self.slot, arc, target)
        return {**self._push([(self.st, self.slot), (tb, self.slot)]), "arc": target}


# ── stany innych rdzeni RetroArcha (lista „Wczytaj zapis”) ──

_CORE = re.compile(r"^[\w.+-]+$")


def other_core_bundle(pid: int, plat: str, gid: str, core: str, slot: int) -> savebundles.Bundle:
    """Stany innego rdzenia RetroArcha w pamięci podręcznej profilu (z serwera —
    syncclient.other_core_states), nazwy umowne paczki („game.state3”)."""
    from emustart import profiles
    if not _CORE.match(core or ""):
        raise EditError("Nieznany rdzeń.")
    folder = profiles.LOCAL / str(pid) / "_inne_rdzenie" / plat / gid / (core if slot == 1 else f"{core}@{slot}")
    return savebundles.Bundle("retroarch", core, "states", folder, savebundles.ARC, [savebundles.ARC]).collect()


class OtherCore(Editor):
    """Stany innego rdzenia RetroArcha tej gry w aktywnym slocie: usuń (do kosza tego rdzenia)
    i przywróć. Najpierw aktualne paczki tego rdzenia z serwera (bez połączenia — odmowa),
    zmiana od razu na serwer; usunięty stan nie wraca przy synchronizacji urządzenia z tym rdzeniem."""

    def __init__(self, cfg: dict, pid: int, game: dict, core: str):
        from emustart import syncclient
        self.cfg, self.pid, self.game = cfg, pid, game
        self.plat, self.gid = syncclient.game_ids(game)
        self.bundles, self._items = [], []
        self.slot = here(pid, self.plat, self.gid)
        self.st = other_core_bundle(pid, self.plat, self.gid, core, self.slot)
        tb = trash_bundle(pid, self.plat, self.gid, self.slot, self.st)
        r = syncclient.push_edit(cfg, pid, game, [(self.st, self.slot), (tb, self.slot)])   # tu: pobranie
        if r.offline:
            raise EditError("Brak połączenia z serwerem — stany innego rdzenia zmienisz po połączeniu.")
        self.st.collect()

    def settle(self, out: dict) -> dict:
        """Po zmianie (albo samym podglądzie kosza): pamięć podręczna = wersja z serwera."""
        from emustart import syncclient
        syncclient.settle_other(self.pid, self.plat, self.gid, self.st, self.slot)
        return out

    def delete(self, arc: str) -> dict:
        return self.settle(super().delete(arc))

    def restore(self, name: str) -> dict:
        return self.settle(super().restore(name))


def summary(pid: int, plat: str, gid: str, bundles: list) -> list:
    """Sloty gry dla opcji gry: [{slot, active, states, saves, time, trash}] — z plików na tym urządzeniu
    (po synchronizacji wszystkie sloty są tutaj)."""
    at = here(pid, plat, gid)
    out = []
    for n in SLOTS:
        bs = [b.collect() for b in bundles] if n == at else [store_bundle(pid, plat, gid, n, b) for b in bundles]
        states = sum(1 for b in bs if b.kind == "states" for _p, a in b.files if not a.endswith(".png"))
        saves = sum(1 for b in bs if b.kind in ("saves", "memcards") for _p in b.files)
        t = max((Path(p).stat().st_mtime for b in bs for p, _a in b.files), default=0.0)
        trash = sum(1 for b in bundles if b.kind == "states"
                    for _p, a in trash_bundle(pid, plat, gid, n, b).files if not a.endswith(".png"))
        out.append({"slot": n, "active": n == at, "states": states, "saves": saves, "time": t, "trash": trash})
    return out


# ── straż numerów (sesja emulatora) ──

class Guard:
    """Stany o istniejących numerach przed sesją; po sesji zmieniony stan dostaje nowy numer,
    poprzedni wraca (zapis po wczytaniu „Auto” w RetroArchu nie nadpisuje slotów)."""

    def __init__(self, bundles: list, folder: Path):
        self.st = next((b for b in bundles if b.kind == "states"), None)
        self.folder = Path(folder) / "_straz_stanow"
        self.before: dict = {}
        if self.st is None:
            return
        shutil.rmtree(self.folder, ignore_errors=True)
        for arc, p in _states(self.st).items():
            if state_no(arc) is None:
                continue                 # „Kontynuuj” nadpisuje się zawsze (autozapis przy wyjściu)
            try:
                self.before[arc] = savebundles.file_sha(p)
                self.folder.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, self.folder / arc)
                png = p.with_name(p.name + ".png")
                if png.is_file():
                    shutil.copy2(png, self.folder / (arc + ".png"))
            except OSError:
                self.before.pop(arc, None)

    def finish(self) -> list:
        """[(numer nadpisany, nowa nazwa)] — stany przeniesione na nowe numery."""
        moved = []
        if self.st is None or not self.before:
            return moved
        try:
            now = _states(self.st)
            for arc, sha in sorted(self.before.items(), key=lambda x: state_no(x[0])):
                p = now.get(arc)
                if p is None or savebundles.file_sha(p) == sha or not (self.folder / arc).is_file():
                    continue
                new = next_free(self.st, arc)
                _move_with_png(self.st, arc, self.st, new)
                _copy(self.folder / arc, self.st.local_path(arc))
                old_png = self.folder / (arc + ".png")
                if old_png.is_file():
                    _copy(old_png, self.st.local_path(arc).with_name(self.st.local_path(arc).name + ".png"))
                moved.append((arc, new))
                log.warning("stan %s nadpisany w trakcie gry — nowy zapis jako %s, poprzedni wrócił", arc, new)
        except (OSError, EditError):
            log.exception("straż numerów stanów")
        finally:
            shutil.rmtree(self.folder, ignore_errors=True)
        return moved
