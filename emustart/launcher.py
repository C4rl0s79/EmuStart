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

from emustart import arcade, cache, emulators, hotkey, ingame, library, paths, systems, winutil

log = logging.getLogger("emustart.launcher")

PROBE_SECONDS = 1.5
PROBE_BYTES = 24 * 1024 * 1024


class LaunchError(Exception):
    pass


class Session:
    """Jedno uruchomienie gry. UI odpytuje `status()`."""

    def __init__(self, cfg: dict, game_id: int, profile_id: int,
                 on_running=None, on_finished=None, ui=None):
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
        exe = emu.get("exe") or ""
        if not exe or not Path(exe).is_file():
            raise LaunchError(f"Brak emulatora dla systemu {info['display']}. "
                              "Wybierz go w ustawieniach (Start → Ustawienia).")
        sysrow = library.system_row(es) or {}
        rom_dir = Path(sysrow.get("rom_dir") or Path(self.cfg["rom_root"]) / es)
        cache_dir = cache.root(self.cfg) / es

        extra = []
        if info["kind"] == "arcade":
            for dep in arcade.dependencies(Path(g["rel"]).stem):
                src = rom_dir / f"{dep}.zip"
                try:
                    st = src.stat()
                except OSError:
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

        if main.suffix.lower() == ".zip" and info["kind"] != "arcade" \
                and not emulators.zip_native(exe):
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
        return max(pick or files, key=lambda f: f.stat().st_size)

    # ── gra ──
    def _play(self, g: dict, exe: str, args: str, rom: Path) -> None:
        cmd = emulators.build_command(exe, args, str(rom))
        adapter = ingame.adapter_for(exe)
        resume = library.get_resume(self.profile_id, g["id"])
        if resume and resume["family"] != adapter.family:
            resume = None              # stan zapisał inny emulator — nie wczytamy go
        if resume and resume["path"] and not Path(resume["path"]).is_file():
            resume = None
        extra = adapter.launch_args(self.run_dir, bool(resume))
        if resume and resume["path"]:
            extra += adapter.resume_args(Path(resume["path"]))
        cmd[1:1] = extra
        self.resumed = bool(resume)
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
        self.phase = "finished"
        cache.enforce_limit(self.cfg, keep_ids=(g["id"],))

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
