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
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

from emustart import (bios, cache, emulators, hotkey, ingame, library, pads, savebundles,
                      paths, profiles, syncclient, systems, winutil)

log = logging.getLogger("emustart.launcher")
FRESH = "__od_poczatku__"         # start_state: gra od początku, bez wczytywania stanu



class LaunchError(Exception):
    pass


class ProfileLocked(LaunchError):
    """Profil gra na innym urządzeniu — interfejs proponuje przejęcie."""

    def __init__(self, msg: str, host: str):
        super().__init__(msg)
        self.host = host


class Session:
    """Jedno uruchomienie gry. UI odpytuje `status()`."""

    def __init__(self, cfg: dict, game_id: int, profile_id: int,
                 on_running=None, on_finished=None, ui=None, start_state: str = "",
                 takeover: bool = False, configure: bool = False):
        self.cfg = cfg
        self.configure = configure        # sam emulator, bez gry — zmiana ustawień profilu
        self.takeover = takeover          # przejęcie profilu zablokowanego przez inne urządzenie
        self.locked_by = ""
        self.conflict: list = []          # konflikt zapisów z serwerem — czeka na decyzję gracza
        self._choice = ""
        self._choice_event = threading.Event()
        self.notice = ""                  # komunikat dla gracza w trakcie gry (np. stan z telefonu)
        self._t_created = time.monotonic()
        self.game_id = game_id
        self.profile_id = profile_id
        self.on_running = on_running
        self.on_finished = on_finished
        self.phase = "preparing"
        self.message = ""
        self.mode = ""                  # lan | remote | cache
        self.srcs: dict = {}            # {rel: netsrc.RemoteFile} — pliki z serwera EmuStart
        self.source = ""                # serwer | serwer + NAS | NAS
        self.prog: cache.Progress | None = None
        self.cancel = threading.Event()
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
        out = {"phase": self.phase, "message": self.message, "mode": self.mode, "source": self.source,
               "title": self.game.get("title", ""), "tags": self.game.get("tags", ""),
               "system": info["display"], "game_id": self.game_id, "locked_by": self.locked_by,
               "notice": getattr(self, "notice", ""), "conflict": getattr(self, "conflict", [])}
        if self.prog:
            out["progress"] = self.prog.snapshot()
        out["copying"] = bool(self.copy_thread and self.copy_thread.is_alive())
        return out

    def abort(self) -> None:
        self.cancel.set()
        self._choice_event.set()

    def choose(self, choice: str) -> bool:
        """Decyzja gracza przy konflikcie zapisów: local | remote | skip."""
        if self.phase != "conflict" or choice not in ("local", "remote", "skip"):
            return False
        self._choice = choice
        self._choice_event.set()
        return True

    # ── przebieg ──
    def _run(self) -> None:
        try:
            self._launch()
        except cache.Cancelled:
            self.phase, self.message = "cancelled", "Anulowano. Pobrana część zostanie użyta następnym razem."
        except ProfileLocked as ex:
            self.phase, self.message, self.locked_by = "error", str(ex), ex.host
        except LaunchError as ex:
            self.phase, self.message = "error", str(ex)
        except Exception as ex:          # nie zostawiamy UI w zawieszeniu
            log.exception("launch failed")
            self.phase, self.message = "error", f"Nieoczekiwany błąd: {ex}"
        finally:
            try:
                self._stream_done()
            except Exception:
                log.exception("dysk strumieniowy po grze")
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
        own = library.game_emu(g)
        if own and Path(own["exe"]).is_file():
            emu = {"exe": own["exe"], "args": own["args"], "label": own["label"]}
        exe = emu.get("exe") or ""
        if not exe or not Path(exe).is_file():
            raise LaunchError(f"Brak emulatora dla systemu {info['display']}. "
                              "Wybierz go w ustawieniach (Start → Ustawienia).")
        if self.configure:
            return self._configure(exe, emu.get("label") or Path(exe).stem)
        rom_dir = library.game_dir(g)
        cache_dir = cache.root(self.cfg) / es

        extra = []
        self._found = None
        if info["kind"] == "arcade":
            # gra nadrzędna i BIOS (np. neogeo.zip) z serwera — obok gry w pamięci podręcznej;
            # bez serwera gra startuje z tego, co już jest pobrane
            from emustart import netsrc
            self._found = netsrc.find(self.cfg, g)
            extra = [(src, cache_dir / rel, size, 0) for rel, src, size in self._found[1]]

        cache.touch(g["id"])
        if cache.is_complete(self.cfg, g) and all(
                cache._fresh(d, s, m) for _src, d, s, m in extra):
            base, self.mode = cache_dir, "cache"
        else:
            base = self._fetch(g, info, rom_dir, extra)

        rom = self._prepare(g, info, base, exe)
        self._play(g, exe, emu.get("args", ""), rom)

    def _fetch(self, g: dict, info: dict, rom_dir: Path, extra: list) -> Path:
        """Pobiera grę z serwera EmuStart do pamięci podręcznej; zwraca katalog, z którego
        uruchamiamy (długie pobieranie gry płytowej — dysk strumieniowy, gra od razu)."""
        from emustart import netsrc
        self.srcs = self._found[0] if self._found else netsrc.sources(self.cfg, g)
        if len(self.srcs) != len(g["files"]):
            raise LaunchError("Serwer EmuStart nie odpowiada (albo nie ma tej gry), a tej gry nie ma "
                              "na tym komputerze. Połącz się z siecią domową lub Tailscale.")
        self.source = "serwer"
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
        self.mode = "remote"
        if self.copy_thread.is_alive() and self.cfg.get("stream_play", True) and self._worth_streaming():
            base = self._stream(g)
            if base:
                self.mode = "stream"
                return base
        self._wait_copy()
        return cache_dir

    def _worth_streaming(self) -> bool:
        """Granie w trakcie pobierania ma sens tylko przy długim pobieraniu — krótsze
        (np. gra do ~1 GB przy ~250 Mb/s) szybciej po prostu pobrać i uruchomić."""
        mbps = getattr(self, "_mbps", 0) or 0
        if mbps <= 0:
            return True
        left = max(0, self.prog.total - self.prog.done)
        est = left * 8 / (mbps * 1e6)
        limit = float(self.cfg.get("stream_min_seconds", 0))
        log.info("pobieranie potrwa ok. %.0f s (próg grania w trakcie: %.0f s)", est, limit)
        return est > limit

    def _stream(self, g: dict) -> Path | None:
        """Gra od razu z dysku strumieniowego (WinFsp): pobrane fragmenty z cache,
        brakujące na żądanie, reszta dalej w tle. None = nie da się (brak WinFsp,
        błąd montowania) — wtedy jak dotąd: czekanie na pobranie albo „Graj teraz”."""
        from emustart import partial, vfs
        if not vfs.available():
            return None
        try:
            disk = vfs.disk()
        except Exception as ex:
            log.warning("dysk strumieniowy niedostępny: %s", ex)
            return None
        self.message = "Przygotowuję grę do grania w trakcie pobierania…"
        top = str(g["id"])
        end = time.monotonic() + 30
        for rel, size, mt in g["files"]:
            dst = cache._local_file(self.cfg, g, rel)
            src = self.srcs[rel]
            if size >= partial.MIN_SIZE:
                source = partial.open_partial(src, dst, size, mt)
                source.prewarm()          # połączenia z serwerem i początek pliku — zanim emulator zapyta
            else:
                # małe pliki (.cue, .m3u) kopiują się jako pierwsze — czekamy chwilę
                while not cache._fresh(dst, size, mt) and self.copy_thread.is_alive() \
                        and time.monotonic() < end and not self.cancel.is_set():
                    time.sleep(0.1)
                source = dst if cache._fresh(dst, size, mt) else src
            disk.add_file(f"{top}/{rel}", source, size, mt)
        self._streamed = (disk, top, g)
        log.info("gra strumieniowo z dysku %s: (%s), pobieranie w tle trwa", disk.letter, g["title"])
        return Path(f"{disk.letter}:\\") / top

    def _stream_done(self) -> None:
        """Po grze: zdjęcie gry z dysku strumieniowego i dokończenie plików, które
        pobrały się w trakcie (przemianowanie blokowane przez otwarty plik)."""
        st = getattr(self, "_streamed", None)
        if not st:
            return
        from emustart import partial
        disk, top, g = st
        disk.remove(top)
        for rel, size, mt in g["files"]:
            p = partial.active(cache._local_file(self.cfg, g, rel))
            if p:
                p.close_demand()
                if p.finalize():
                    cache._mark(g["id"], complete=1, size=g["size"]) if cache.is_complete(self.cfg, g) else None
        self._streamed = None

    def _copy(self, g, rom_dir, extra) -> None:
        try:
            cache.copy_game(self.cfg, g, rom_dir, self.prog, self.cancel, extra,
                            srcs=getattr(self, "srcs", None))
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
            main = self._extract(main, info, m3u_ok=emulators.supports_m3u(exe))

        if g["multidisc"] and emulators.supports_m3u(exe):
            self.run_dir.mkdir(parents=True, exist_ok=True)
            m3u = self.run_dir / (g["name"] + ".m3u")
            from emustart import scanner      # tylko płyty, bez ścieżek .bin / .wav
            rels = scanner.playlist_entries([rel for rel, _s, _m in g["files"]])
            m3u.write_text("\n".join(str(base / rel) for rel in rels) + "\n", encoding="utf-8")
            return m3u
        if main.suffix.lower() == ".m3u" and not emulators.supports_m3u(exe):
            discs = [base / rel for rel, _s, _m in g["files"][1:]
                     if not rel.lower().endswith((".bin", ".raw"))]
            if discs:
                return discs[0]
        return main

    def _extract(self, zpath: Path, info: dict, m3u_ok: bool = False) -> Path:
        out_dir = self.run_dir / "rom"
        out_dir.mkdir(parents=True, exist_ok=True)
        wanted = systems.ext_set(info) - {"zip", "7z"}
        with zipfile.ZipFile(zpath) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if not members:
                raise LaunchError("Archiwum ZIP jest puste.")
            # postęp w oknie (paczki MSU-1 mają setki MB ścieżek dźwiękowych)
            self.prog = cache.Progress(sum(m.file_size for m in members), len(members))
            for i, m in enumerate(members, 1):
                self.prog.file_index = i
                target = out_dir / Path(m.filename).name
                with z.open(m) as src, winutil.open_temp_write(str(target)) as dst:
                    for buf in iter(lambda: src.read(1024 * 1024), b""):
                        if getattr(self, "cancel", None) is not None and self.cancel.is_set():
                            raise LaunchError("Przerwano.")
                        dst.write(buf)
                        self.prog.add(len(buf))
        files = [out_dir / Path(m.filename).name for m in members]
        pick = pick_rom(files, wanted)
        if not pick and len(files) > 1:
            exts = ", ".join(sorted(wanted - {"m3u"}))
            raise LaunchError(f"W archiwum nie ma pliku gry ({exts}) — są tylko pliki dodatkowe "
                              f"(np. muzyka MSU-1). Dołóż ROM do archiwum.")
        disks = sorted((f for f in pick if f.suffix.lower() in DISK_EXTS), key=_disk_order)
        if m3u_ok and len(disks) > 1:
            # gra na kilku dyskietkach (Amiga .ipf/.adf): playlista — emulator
            # zmienia dyskietki sam albo z menu (RetroArch: sterowanie dyskami)
            m3u = out_dir / (zpath.stem + ".m3u")
            m3u.write_text("\n".join(d.name for d in disks) + "\n", encoding="utf-8")
            return m3u
        if disks:
            return disks[0]
        return max(pick or files, key=lambda f: f.stat().st_size)

    # ── gra ──
    def _play(self, g: dict, exe: str, args: str, rom: Path) -> None:
        cmd = emulators.build_command(exe, args, str(rom))
        adapter = ingame.adapter_for(exe)
        # profil najpierw: ustawienia, save'y i konto RA muszą być na miejscu,
        # zanim policzymy parametry startu (RetroArch czyta z nich foldery)
        t_prof = time.monotonic()
        save_names = self._profile_prepare(adapter)
        try:
            if save_names:
                self._sync_before(g, adapter, args, rom)    # zapisy tej gry z serwera
        except BaseException:
            self._release_dirs()
            if save_names:
                profiles.unlock(self.cfg, self.profile_id)
            raise
        self._t_prof = time.monotonic() - t_prof
        try:
            adapter.keyboard_game = systems.info(g["es"])["plat"] in systems.COMPUTER_PLATS
            adapter.shader_off = bool(((self.cfg.get("systems") or {}).get(g["es"]) or {}).get("shader_off"))
            if adapter.family == "retroarch" and g["es"] in systems.WHD_SYSTEMS:
                try:   # po podpięciu profilu — folder saves wskazuje już na właściwego gracza
                    bios.whdload_kickstarts(self.cfg, adapter.home,
                                            adapter._cfg_dir("savefile_directory", "saves"))
                except Exception:
                    log.exception("Kickstarty WHDLoad")
            po = (self.cfg.get("pad_order") or {}).get("mode", "windows")
            # bateria (rodzaj zasilania) potrzebna tylko w trybie „bezprzewodowe pierwsze”
            order = pads.order(self.cfg, pads.connected(battery=po == "wireless_first"))
            if self.start_state == FRESH:
                # „Graj od początku”: bez wczytania stanu (autozapis, „Kontynuuj”, wznowienie)
                resume = None
                extra = adapter.launch_args(self.run_dir, False, order)
            elif self.start_state.startswith("arc:"):
                # „Wczytaj zapis”: stan z paczki (po synchronizacji — także z innego urządzenia)
                hit = next((b.local_path(self.start_state[4:]) for b in getattr(self, "_sync_bundles", [])
                            if b.kind == "states"), None)
                resume = None
                if hit and hit.is_file():
                    extra = adapter.start_state_args(self.run_dir, hit, order)
                else:
                    self.notice = "Nie ma już tego stanu na serwerze — gra startuje bez niego."
                    extra = adapter.launch_args(self.run_dir, False, order)
            elif self.start_state and Path(self.start_state).is_file():
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
        except BaseException:
            # blokada profilu założona, a emulator nie wystartował — zdejmujemy ją
            self._release_dirs()
            if save_names:
                profiles.unlock(self.cfg, self.profile_id)
            raise
        started_at = time.time()
        try:
            self._run_process(g, exe, cmd, adapter)
        finally:
            try:
                adapter.after_session()          # ustawienia zmienione w trakcie gry zostają
            except Exception:
                log.exception("ustawienia po sesji")
            self._release_dirs()
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
                    self._learn_states(g, adapter, started_at)   # przedrostek stanów PS2 przed wysłaniem
                    self._sync_after(g)                       # zapisy tej gry → serwer
                    self._profile_finish(adapter, save_names)
                    cache.enforce_limit(self.cfg, keep_ids=(g["id"],))
                except Exception:
                    log.exception("po grze")
            start_post(post, g["title"])
        self.phase = "finished"

    def _configure(self, exe: str, label: str) -> None:
        """Emulator bez gry, z ustawieniami profilu: zmiany (obraz, dźwięk, sterowanie)
        po zamknięciu wracają do profilu — jak zmiany w trakcie gry."""
        adapter = ingame.adapter_for(exe)
        self.mode = "config"
        self.message = f"Ustawienia: {label}"
        save_names = self._profile_prepare(adapter)
        try:
            cmd = [exe] + adapter.config_args(self.run_dir)
        except BaseException:
            self._release_dirs()
            if save_names:
                profiles.unlock(self.cfg, self.profile_id)
            raise
        log.info("ustawienia emulatora: %s", cmd)
        self.phase = "running"
        try:
            try:
                self.proc = subprocess.Popen(cmd, cwd=str(Path(exe).parent))
            except OSError as ex:
                raise LaunchError(f"Nie udało się uruchomić emulatora: {ex}")
            if self.on_running:
                try:
                    self.on_running(self)
                except Exception:
                    log.exception("on_running")
            self.proc.wait()
        finally:
            try:
                adapter.after_session()
            except Exception:
                log.exception("ustawienia po sesji")
            self._release_dirs()
            if save_names:
                start_post(lambda: self._profile_finish(adapter, save_names), "ustawienia " + label)
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
        host = profiles.lock(self.cfg, self.profile_id, force=getattr(self, "takeover", False))
        if host:
            prof = profiles.get(self.profile_id) or {}
            raise ProfileLocked(f"Profil „{prof.get('name', '?')}” gra teraz na urządzeniu: {host}. "
                                "Jeśli tam już nie grasz (np. aplikację zamknięto w trakcie gry), "
                                "przejmij profil — inaczej wybierz inny profil.", host)
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
        try:
            adapter.first_run(self.cfg)               # bez kreatora pierwszego uruchomienia, z BIOS-em
        except Exception:
            log.exception("pierwsze uruchomienie emulatora")
        try:
            from emustart import installer           # wymagane ustawienia z serwera (wygrywają z profilem)
            adapter.apply_policy(installer.policy(adapter.family))
        except Exception:
            log.exception("wymagane ustawienia emulatora")
        dirs = adapter.save_dirs()               # foldery z (już podmienionych) ustawień
        names = [d.name for d in dirs]
        # zapisy, stany i karty pamięci w paczkach z serwerem (syncclient) — bez
        # dotychczasowej synchronizacji całych folderów (jedna prawda)
        covered = {d.name for d in savebundles.covered_dirs(adapter)}
        self._folder_names = [n for n in names if n not in covered]
        if covered:
            profiles.mark_bundled(adapter.family, covered)
        try:
            n = profiles.sync_down(self.cfg, self.profile_id, adapter.family, self._folder_names)
            if n:
                log.info("pobrano z NAS %d plików save'ów", n)
            for d in dirs:
                if adapter.can_redirect():
                    profiles.adopt(adapter.family, d)        # bez junction (folder w ustawieniach)
                else:
                    profiles.attach(self.profile_id, adapter.family, d)
            if profiles.bind(adapter, self.profile_id):
                g = getattr(self, "game", None) or {}
                card = savebundles.card_stem(Path(g.get("rel", "")), g) if g.get("name") else ""
                self._restore_dirs = adapter.redirect_session(card)
        except Exception:
            log.exception("podpinanie save'ów profilu")
        return names or ["-"]

    # ── zapisy gry z serwerem (emustart/syncclient.py) ──
    def _sync_before(self, g: dict, adapter, args: str, rom) -> None:
        self._sync_bundles, self._sync_skip = [], set()
        try:
            bundles = savebundles.for_game(adapter, savebundles.core_id(args), Path(rom), ps2_info(self.cfg, g))
            for b in bundles:
                b.adopt_names()                     # nazwy od pliku, który uruchomi emulator
        except Exception:
            log.exception("paczki zapisów gry")
            return
        if not bundles:
            return
        self._sync_bundles = bundles
        self.message = "Synchronizuję zapisy z serwerem…"
        res = syncclient.before(self.cfg, self.profile_id, bundles, g)
        if res.conflicts:
            choice = self._ask_conflict(res.conflicts)
            if choice != "skip":
                try:
                    syncclient.resolve(self.cfg, self.profile_id, g, res.conflicts, choice)
                except (syncclient.Offline, syncclient.ConflictError, OSError, ValueError) as ex:
                    log.info("konflikt zapisów: %s", ex)
                    self.notice = "Nie udało się rozstrzygnąć konfliktu zapisów — gram na zapisach z tego urządzenia."
                    choice = "skip"
            if choice == "skip":
                # bez wysyłania po grze: konflikt wróci przy następnej grze
                self._sync_skip = {c.bundle.key for c in res.conflicts}
        elif res.offline:
            self.notice = "Zapisy bez synchronizacji z serwerem — wyślę je, gdy wróci połączenie."
        self.message = ""

    def _ask_conflict(self, conflicts: list) -> str:
        """Faza „conflict”: okno uruchamiania pokazuje obie wersje i czeka na decyzję gracza."""
        self.conflict = [c.describe() for c in conflicts]
        self._choice, prev = "", self.phase
        self._choice_event.clear()
        self.phase = "conflict"
        self.message = "Zapisy tej gry zmieniły się na tym urządzeniu i na innym."
        self._choice_event.wait()
        self.phase, self.conflict = prev, []
        if self.cancel.is_set() and not self._choice:
            raise cache.Cancelled()
        return self._choice or "skip"

    def _sync_after(self, g: dict) -> None:
        bundles = getattr(self, "_sync_bundles", None)
        if not bundles:
            return
        for b in bundles:                           # stan PS2 zapisany pierwszy raz — poznany przedrostek
            if (b.emulator_id, b.kind) == ("pcsx2", "states"):
                b.names = savebundles.ps2_prefixes(ps2_info(self.cfg, g))
        try:
            syncclient.after(self.cfg, self.profile_id, bundles, g, getattr(self, "_sync_skip", set()))
        except Exception:
            log.exception("zapisy na serwer")

    def _release_dirs(self) -> None:
        """Ustawienia folderów zapisów i karty pamięci sprzed gry (redirect_session)."""
        restore, self._restore_dirs = getattr(self, "_restore_dirs", None), None
        if restore:
            try:
                restore()
            except Exception:
                log.exception("przywracanie folderów zapisów")

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
                n = profiles.settings_save(self.cfg, self.profile_id, adapter,
                                           (getattr(self, "game", None) or {}).get("title", "") or "ustawienia emulatora")
                if n:
                    log.info("ustawienia %s zapisane w profilu: %d plików", adapter.family, n)
            except Exception:
                log.exception("zapis ustawień profilu")
        names = [n for n in names if n in getattr(self, "_folder_names", names)]
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
        log.info("czas do startu: %.1f s (profil: zapisy, ustawienia, NAS %.1f s)",
                 time.monotonic() - self._t_created, getattr(self, "_t_prof", 0.0))
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


def ps2_info(cfg: dict, g: dict) -> dict:
    """Gra z numerem płyty (metadane) i przedrostkami stanów poznanymi po grze — do paczki
    stanów PCSX2 (nazwy stanów są od numeru płyty, nie od nazwy gry)."""
    out = dict(g)
    try:
        from emustart import metadata
        out["serial"] = (metadata.ensure_local(cfg, g) or {}).get("serial", "") if g.get("id") else ""
    except Exception:
        out["serial"] = ""
    try:
        out["state_prefixes"] = [r["prefix"] for r in library.db().execute(
            "SELECT prefix FROM states WHERE game_id=? AND family='pcsx2'", (g.get("id"),))]
    except Exception:
        out["state_prefixes"] = []
    return out


def content_guess(cfg: dict, g: dict, exe: str) -> Path:
    """Plik gry, który dostanie emulator (bez uruchamiania): ZIP rozpakowany do `rom` (MSU-1,
    emulator bez ZIP-ów) — ROM ze środka; inaczej plik z pamięci podręcznej. Do nazw zapisów."""
    local = cache.local_path(cfg, g)
    if local.suffix.lower() == ".zip" and local.is_file() and (not emulators.zip_native(exe) or _zip_multi(local)):
        from emustart import savemgr
        stems = savemgr.zip_stems(local)
        if stems:
            return paths.RUN_TMP / "_" / "rom" / (stems[0] + ".rom")
    return local


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


def cleanup_run_dirs(max_age: float = 3600) -> int:
    """Foldery sesji po awarii (wypakowane gry, konfiguracja sesji RetroArcha
    z tokenem RA) — usuwane przy starcie, jeśli starsze niż godzina."""
    n = 0
    try:
        dirs = list(paths.RUN_TMP.iterdir())
    except OSError:
        return 0
    for d in dirs:
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n


DISK_EXTS = {".ipf", ".adf", ".adz", ".dms", ".fdi", ".d64", ".st", ".msa", ".dsk"}
# pliki towarzyszące w archiwum — nigdy nie są grą (MSU-1: .msu, .pcm; łatki; opisy)
COMPANION_EXTS = {"msu", "pcm", "bps", "ips", "ups", "xdelta", "bml", "xml", "txt", "nfo",
                  "diz", "md", "pdf", "jpg", "jpeg", "png", "sha1", "md5", "sfv", "cfg",
                  "ini", "sav", "srm", "m3u"}


def pick_rom(names: list, wanted: set) -> list:
    """Pliki gry spośród rozpakowanych: z rozszerzeniem systemu, a gdy żadnego
    (nieznany system, stary serwer bez listy rozszerzeń) — wszystko poza plikami
    towarzyszącymi."""
    pick = [n for n in names if Path(n).suffix.lower().lstrip(".") in wanted]
    return pick or [n for n in names if Path(n).suffix.lower().lstrip(".") not in COMPANION_EXTS]


def _disk_order(p: Path):
    """„Gra (Disk 2).ipf” po „Gra (Disk 1).ipf”, „Disk 10” po „Disk 9”."""
    m = re.search(r"\((?:Disk|Disc|Side)\s*(\d+)", p.name, re.I)
    return (int(m.group(1)) if m else 0, p.name.lower())
