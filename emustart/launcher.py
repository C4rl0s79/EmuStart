"""launcher — od wyboru gry do zamknięcia emulatora.

Kolejność decyzji:
  1. gra kompletna w cache        → start z cache
  2. NAS niedostępny              → błąd „offline”
  3. mała gra / arcade / katalog  → najpierw kopia do cache, potem start
  4. duża płyta:
       pomiar prędkości na pierwszych sekundach kopiowania
       szybko (LAN)   → start wprost z NAS, kopia leci dalej w tle
       wolno (zdalnie) → ekran pobierania; „Graj mimo to” = start z NAS
Potem: rozpakowanie ZIP do %TEMP% (gdy emulator nie czyta ZIP), playlista .m3u
dla gier wielopłytowych, start procesu, czas gry, sprzątanie, limit cache.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

from emustart import (arcade, cache, emulators, hotkey, ingame, library, pads,
                      paths, profiles, systems, winutil)

log = logging.getLogger("emustart.launcher")

PROBE_SECONDS = 1.5
PROBE_BYTES = 24 * 1024 * 1024


class LaunchError(Exception):
    pass


class Session:
    """Jedno uruchomienie gry. UI odpytuje `status()`."""

    def __init__(self, cfg: dict, game_id: int, profile_id: int,
                 on_running=None, on_finished=None, ui=None, start_state: str = ""):
        self.cfg = cfg
        self.game_id = game_id
        self.profile_id = profile_id
        self.on_running = on_running
        self.on_finished = on_finished
        self.phase = "preparing"
        self.message = ""
        self.mode = ""                  # lan | remote | cache
        self.prog: cache.Progress | None = None
        self.cancel = threading.Event()
        self.play_now = threading.Event()
        self.copy_thread: threading.Thread | None = None
        self.copy_error = ""
        self.proc: subprocess.Popen | None = None
        self.ui = ui                    # menu w grze (api.Api); None = bez menu
        self.menu: hotkey.GameMenu | None = None
        self.resumed = False
        self.start_state = start_state  # stan wybrany ręcznie w opcjach gry
        self.profiles_on = True         # wyłączane w testach
        self.game = library.game(game_id) or {}
        self.run_dir = paths.RUN_TMP / f"{game_id}_{int(time.time())}"
        self.thread = threading.Thread(target=self._run, daemon=True, name="launch")

    # ── API dla UI ──
    def start(self) -> None:
        self.thread.start()

    def status(self) -> dict:
        info = systems.info(self.game.get("es", ""))
        out = {"phase": self.phase, "message": self.message, "mode": self.mode,
               "title": self.game.get("title", ""), "tags": self.game.get("tags", ""),
               "system": info["display"], "game_id": self.game_id,
               "can_play_now": self.phase == "downloading" and not self.game.get("is_dir")
               and info["kind"] in ("disc", "mixed")}
        if self.prog:
            out["progress"] = self.prog.snapshot()
        out["copying"] = bool(self.copy_thread and self.copy_thread.is_alive())
        return out

    def abort(self) -> None:
        self.cancel.set()

    def request_play_now(self) -> None:
        self.play_now.set()

    # ── przebieg ──
    def _run(self) -> None:
        try:
            self._launch()
        except cache.Cancelled:
            self.phase, self.message = "cancelled", "Anulowano. Pobrana część zostanie użyta następnym razem."
        except LaunchError as ex:
            self.phase, self.message = "error", str(ex)
        except Exception as ex:          # nie zostawiamy UI w zawieszeniu
            log.exception("launch failed")
            self.phase, self.message = "error", f"Nieoczekiwany błąd: {ex}"
        finally:
            shutil.rmtree(self.run_dir, ignore_errors=True)
            if self.on_finished:
                try:
                    self.on_finished(self)
                except Exception:
                    log.exception("on_finished")

    def _launch(self) -> None:
        g = self.game
        if not g:
            raise LaunchError("Gra zniknęła z biblioteki. Odśwież listę.")
        es = g["es"]
        info = systems.info(es)
        emu = (self.cfg.get("systems") or {}).get(es) or {}
        own = library.db().execute("SELECT * FROM game_emu WHERE game_id=?", (g["id"],)).fetchone()
        if own and Path(own["exe"]).is_file():
            emu = {"exe": own["exe"], "args": own["args"], "label": own["label"]}
        exe = emu.get("exe") or ""
        if not exe or not Path(exe).is_file():
            raise LaunchError(f"Brak emulatora dla systemu {info['display']}. "
                              "Wybierz go w ustawieniach (Start → Ustawienia).")
        rom_dir = library.game_dir(g)
        cache_dir = cache.root(self.cfg) / es

        extra = []
        if info["kind"] == "arcade":
            # rodzic/BIOS najpierw obok gry, potem w innych folderach tego systemu
            search = [rom_dir] + [d for d in library.system_dirs(es) if d != rom_dir]
            for dep in arcade.dependencies(Path(g["rel"]).stem):
                st = None
                for d in search:
                    src = d / f"{dep}.zip"
                    try:
                        st = src.stat()
                        break
                    except OSError:
                        continue
                if st is None:
                    continue
                extra.append((src, cache_dir / f"{dep}.zip", st.st_size, st.st_mtime))

        cache.touch(g["id"])
        if cache.is_complete(self.cfg, g) and all(
                cache._fresh(d, s, m) for _src, d, s, m in extra):
            base, self.mode = cache_dir, "cache"
        else:
            base = self._fetch(g, info, rom_dir, extra)

        rom = self._prepare(g, info, base, exe)
        self._play(g, exe, emu.get("args", ""), rom)

    def _fetch(self, g: dict, info: dict, rom_dir: Path, extra: list) -> Path:
        """Kopiuje grę do cache; zwraca katalog, z którego uruchamiamy."""
        if not (rom_dir / g["rel"]).exists():
            raise LaunchError("NAS niedostępny, a tej gry nie ma w cache. "
                              "Połącz się z siecią domową lub Tailscale.")
        total = cache.missing_bytes(self.cfg, g) + sum(s for _a, _b, s, _m in extra)
        self.prog = cache.Progress(total, len(g["files"]) + len(extra))
        self.phase = "downloading"
        self.copy_thread = threading.Thread(target=self._copy, args=(g, rom_dir, extra),
                                            daemon=True, name="copy")
        self.copy_thread.start()
        cache_dir = cache.root(self.cfg) / g["es"]

        small = g["size"] <= int(self.cfg.get("small_game_mb", 64)) * 1024 * 1024
        streamable = info["kind"] in ("disc", "mixed") and not g["is_dir"] and not small
        if not streamable:
            self.mode = "copy"
            self._wait_copy()
            return cache_dir

        self.mode = self._network_mode()
        if self.mode == "lan":
            log.info("LAN: start z NAS, kopia w tle (%s)", g["title"])
            return rom_dir
        while self.copy_thread.is_alive():
            if self.play_now.wait(0.2):
                log.info("Graj mimo to: start z NAS (%s)", g["title"])
                return rom_dir
            if self.cancel.is_set():
                raise cache.Cancelled()
        self._raise_copy_error()
        return cache_dir

    def _copy(self, g, rom_dir, extra) -> None:
        try:
            cache.copy_game(self.cfg, g, rom_dir, self.prog, self.cancel, extra)
        except cache.Cancelled:
            pass
        except Exception as ex:
            log.exception("copy failed")
            self.copy_error = str(ex)

    def _wait_copy(self) -> None:
        while self.copy_thread.is_alive():
            self.copy_thread.join(0.2)
        if self.cancel.is_set():
            raise cache.Cancelled()
        self._raise_copy_error()

    def _raise_copy_error(self) -> None:
        if self.copy_error:
            raise LaunchError(f"Kopiowanie nie powiodło się: {self.copy_error}")

    def _network_mode(self) -> str:
        forced = self.cfg.get("network_mode", "auto")
        if forced in ("lan", "remote"):
            return forced
        t0 = time.monotonic()
        while (time.monotonic() - t0 < PROBE_SECONDS and self.prog.done < PROBE_BYTES
               and self.copy_thread.is_alive()):
            time.sleep(0.05)
        elapsed = max(0.001, time.monotonic() - t0)
        mbps = self.prog.done * 8 / elapsed / 1e6
        log.info("pomiar sieci: %.0f Mb/s", mbps)
        return "lan" if mbps >= float(self.cfg.get("lan_threshold_mbps", 200)) else "remote"

    # ── przygotowanie pliku dla emulatora ──
    def _prepare(self, g: dict, info: dict, base: Path, exe: str) -> Path:
        main = base / g["rel"]
        if g["is_dir"]:
            eboot = main / "PS3_GAME" / "USRDIR" / "EBOOT.BIN"
            return eboot if eboot.is_file() else main

        # RetroArch czyta zip sam, ale wypakowuje z niego tylko ROM — paczka
        # z plikami towarzyszącymi (MSU-1: .msu + ścieżki .pcm) musi leżeć
        # rozpakowana w jednym folderze, więc wypakowujemy ją całą
        if main.suffix.lower() == ".zip" and info["kind"] != "arcade" \
                and (not emulators.zip_native(exe) or _zip_multi(main)):
            self.phase, self.message = "extracting", "Rozpakowuję do pamięci…"
            main = self._extract(main, info)

        if g["multidisc"] and emulators.supports_m3u(exe):
            self.run_dir.mkdir(parents=True, exist_ok=True)
            m3u = self.run_dir / (g["name"] + ".m3u")
            m3u.write_text("\n".join(str(base / rel) for rel, _s, _m in g["files"]) + "\n",
                           encoding="utf-8")
            return m3u
        if main.suffix.lower() == ".m3u" and not emulators.supports_m3u(exe):
            discs = [base / rel for rel, _s, _m in g["files"][1:]
                     if not rel.lower().endswith((".bin", ".raw"))]
            if discs:
                return discs[0]
        return main

    def _extract(self, zpath: Path, info: dict) -> Path:
        out_dir = self.run_dir / "rom"
        out_dir.mkdir(parents=True, exist_ok=True)
        wanted = systems.ext_set(info) - {"zip", "7z"}
        with zipfile.ZipFile(zpath) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if not members:
                raise LaunchError("Archiwum ZIP jest puste.")
            for m in members:
                target = out_dir / Path(m.filename).name
                with z.open(m) as src, winutil.open_temp_write(str(target)) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        files = [out_dir / Path(m.filename).name for m in members]
        pick = [f for f in files if f.suffix.lower().lstrip(".") in wanted]
        if not pick and len(files) > 1:
            exts = ", ".join(sorted(wanted - {"m3u"}))
            raise LaunchError(f"W archiwum nie ma pliku gry ({exts}) — są tylko pliki dodatkowe "
                              f"(np. muzyka MSU-1). Dołóż ROM do archiwum.")
        return max(pick or files, key=lambda f: f.stat().st_size)

    # ── gra ──
    def _play(self, g: dict, exe: str, args: str, rom: Path) -> None:
        cmd = emulators.build_command(exe, args, str(rom))
        adapter = ingame.adapter_for(exe)
        # profil najpierw: ustawienia, save'y i konto RA muszą być na miejscu,
        # zanim policzymy parametry startu (RetroArch czyta z nich foldery)
        save_names = self._profile_prepare(adapter)
        po = (self.cfg.get("pad_order") or {}).get("mode", "windows")
        # bateria (rodzaj zasilania) potrzebna tylko w trybie „bezprzewodowe pierwsze”
        order = pads.order(self.cfg, pads.connected(battery=po == "wireless_first"))
        if self.start_state and Path(self.start_state).is_file():
            # stan wybrany ręcznie ma pierwszeństwo przed stanem wznowienia
            resume = None
            extra = adapter.start_state_args(self.run_dir, Path(self.start_state), order)
        else:
            if self.profiles_on:
                resume_from_nas(self.cfg, self.profile_id, g)
            resume = library.get_resume(self.profile_id, g["id"])
            if resume and resume["family"] != adapter.family:
                resume = None              # stan zapisał inny emulator — nie wczytamy go
            if resume and resume["path"] and not Path(resume["path"]).is_file():
                resume = None
            extra = adapter.launch_args(self.run_dir, bool(resume), order)
            if resume and resume["path"]:
                extra += adapter.resume_args(Path(resume["path"]))
        cmd[1:1] = extra
        self.resumed = bool(resume)

        restore_pads = None
        if not pads.is_identity(order):
            try:
                restore_pads = adapter.remap_pads(order)
            except Exception:
                log.exception("przepinanie padów")
        started_at = time.time()
        try:
            self._run_process(g, exe, cmd, adapter)
        finally:
            if restore_pads:
                try:
                    restore_pads()
                except Exception:
                    log.exception("przywracanie padów")
            # zapis na NAS i porządki — w tle: ekran gry znika od razu po wyjściu
            # z emulatora, a następna gra poczeka na koniec synchronizacji
            def post() -> None:
                try:
                    if save_names:
                        self._resume_to_nas(g, started_at)
                    self._profile_finish(adapter, save_names)
                    self._learn_states(g, adapter, started_at)
                    cache.enforce_limit(self.cfg, keep_ids=(g["id"],))
                except Exception:
                    log.exception("po grze")
            start_post(post, g["title"])
        self.phase = "finished"

    # ── profile: save'y emulatora na czas gry należą do profilu ──
    def _profile_prepare(self, adapter) -> list:
        if post_busy():
            self.message = "Kończę zapis poprzedniej gry na NAS…"
            wait_post()
        if not self.profiles_on or not (adapter.save_dirs() or adapter.settings_files()):
            return []
        try:
            if profiles.nas_online(self.cfg):
                profiles.resolve_moves(self.cfg)   # folder przeniesiony z innego komputera
        except Exception:
            log.exception("przeniesione profile")
        host = profiles.lock(self.cfg, self.profile_id)
        if host:
            prof = profiles.get(self.profile_id) or {}
            raise LaunchError(f"Profil „{prof.get('name', '?')}” gra teraz na komputerze {host}. "
                              "Wybierz inny profil albo zakończ tamtą grę.")
        self._settings_on = self.cfg.get("profile_settings", True)
        if self._settings_on:
            try:
                profiles.settings_load(self.cfg, self.profile_id, adapter)
            except Exception:
                log.exception("ustawienia profilu")
        try:
            ra = profiles.ra_for_launch(self.cfg, self.profile_id, adapter)
            adapter.cheevos = ra
            adapter.apply_cheevos(ra)
        except Exception:
            log.exception("RetroAchievements profilu")
        dirs = adapter.save_dirs()               # foldery z (już podmienionych) ustawień
        names = [d.name for d in dirs]
        try:
            n = profiles.sync_down(self.cfg, self.profile_id, adapter.family, names)
            if n:
                log.info("pobrano z NAS %d plików save'ów", n)
            for d in dirs:
                profiles.attach(self.profile_id, adapter.family, d)
        except Exception:
            log.exception("podpinanie save'ów profilu")
        return names or ["-"]

    def _resume_to_nas(self, g: dict, started_at: float) -> None:
        """Nowy stan wznowienia → NAS (drugi komputer go zobaczy); zużyty → usunięty."""
        try:
            res = library.get_resume(self.profile_id, g["id"])
            if res and res["created"] >= started_at - 1:
                profiles.resume_put(self.cfg, self.profile_id, g, res["family"], res["path"], res["created"])
            elif self.resumed and not res:
                profiles.resume_drop(self.cfg, self.profile_id, g)
        except Exception:
            log.exception("wznowienie na NAS")

    def _profile_finish(self, adapter, names: list) -> None:
        if not names:
            return
        names = [n for n in names if n != "-"]
        if getattr(self, "_settings_on", False):
            try:
                n = profiles.settings_save(self.cfg, self.profile_id, adapter)
                if n:
                    log.info("ustawienia %s zapisane w profilu: %d plików", adapter.family, n)
            except Exception:
                log.exception("zapis ustawień profilu")
        try:
            n = profiles.sync_up(self.cfg, self.profile_id, adapter.family, names)
            if n:
                log.info("wysłano na NAS %d plików save'ów", n)
        except Exception:
            log.exception("synchronizacja save'ów")
        profiles.unlock(self.cfg, self.profile_id)

    def _learn_states(self, g: dict, adapter, since: float) -> None:
        """Zapamiętuje, jak nazywają się pliki stanów tej gry (np. GALE01.s01 →
        „GALE01.”), żeby opcje gry mogły je potem wylistować."""
        try:
            for d in adapter.state_dirs():
                if not d.is_dir():
                    continue
                for f in d.rglob("*"):
                    if f.is_file() and f.stat().st_mtime >= since - 1:
                        prefix = adapter.prefix_of(f.name)
                        if prefix:
                            with library.db() as c:
                                c.execute("INSERT OR REPLACE INTO states VALUES(?,?,?,?)",
                                          (self.profile_id, g["id"], adapter.family, prefix))
                            return
        except Exception:
            log.exception("stany")

    def _run_process(self, g: dict, exe: str, cmd: list, adapter) -> None:
        log.info("start: %s", cmd)
        self.phase, self.message = "running", ""
        t0 = time.monotonic()
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(Path(exe).parent))
        except OSError as ex:
            raise LaunchError(f"Nie udało się uruchomić emulatora: {ex}")
        if self.on_running:
            try:
                self.on_running(self)
            except Exception:
                log.exception("on_running")
        if self.ui:
            self.menu = hotkey.GameMenu(self, adapter, self.ui)
            self.menu.start()
        self.proc.wait()
        if self.menu:
            self.menu.open = False
            self.ui.menu_hide()
        seconds = int(time.monotonic() - t0)
        if self.resumed and not (self.menu and self.menu.new_resume) and seconds >= 3:
            library.clear_resume(self.profile_id, g["id"])   # stan wznowienia jest jednorazowy
        library.record_play(self.profile_id, g["id"], seconds)
        cache.touch(g["id"])
        if seconds < 3 and self.proc.returncode not in (0, None):
            raise LaunchError(f"Emulator zakończył się od razu (kod {self.proc.returncode}). "
                              "Sprawdź emulator i argumenty w ustawieniach.")

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def resume_from_nas(cfg: dict, pid: int, g: dict) -> None:
    """Stan wznowienia zapisany na innym komputerze → lokalnie (nowszy wygrywa);
    zużyty gdzie indziej → usunięty też tutaj."""
    try:
        e = profiles.resume_pull(cfg, pid, g)
    except Exception:
        log.exception("wznowienie z NAS")
        return
    if not e:
        return
    local = library.get_resume(pid, g["id"])
    if "dropped" in e:
        if local and local["created"] <= e["dropped"] + 1:
            library.clear_resume(pid, g["id"])
        return
    if not local or e["created"] > local["created"] + 1:
        library.set_resume(pid, g["id"], e["family"], e["path"], e["created"])


def _zip_multi(path: Path) -> bool:
    """Archiwum z więcej niż jednym plikiem (ROM + pliki towarzyszące)."""
    try:
        with zipfile.ZipFile(path) as z:
            return sum(1 for m in z.infolist() if not m.is_dir()) > 1
    except (OSError, zipfile.BadZipFile):
        return False


# ── synchronizacja po grze (w tle) ──
_post: list = []          # [(wątek, tytuł)]
_post_lock = threading.Lock()


def start_post(fn, title: str) -> None:
    t = threading.Thread(target=fn, daemon=False, name="po-grze")
    with _post_lock:
        _post[:] = [(th, ti) for th, ti in _post if th.is_alive()]
        _post.append((t, title))
    t.start()


def post_busy() -> str:
    """Tytuł gry, której save'y właśnie idą na NAS ('' = nic)."""
    with _post_lock:
        alive = [ti for th, ti in _post if th.is_alive()]
    return alive[0] if alive else ""


def wait_post(timeout: float | None = None) -> bool:
    with _post_lock:
        threads = [th for th, _ti in _post if th.is_alive()]
    end = None if timeout is None else time.monotonic() + timeout
    for th in threads:
        th.join(None if end is None else max(0.0, end - time.monotonic()))
    return not post_busy()
