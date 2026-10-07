"""hotkey — A+Y przytrzymane 2 s w trakcie gry otwiera menu EmuStart.

Przycisk Xbox/PS zajmują same emulatory, dlatego kombinacja na zwykłych
przyciskach (na padzie PS: X + trójkąt). Pad czytamy przez XInput w osobnym
wątku, bo w trakcie gry fokus ma emulator, nie nasze okno.

Gdy menu jest otwarte, ten sam wątek steruje menu (krzyżak, A, B) i podaje
akcje do UI. Gamepad API w oknie jest wtedy ignorowane, żeby nie liczyć
naciśnięć podwójnie.
"""

from __future__ import annotations

import logging
import threading
import time

from emustart import ingame, library, winutil, xinput

log = logging.getLogger("emustart.hotkey")

HOLD_SECONDS = 2.0
POLL = 1 / 60


class GameMenu:
    """Obsługa menu dla jednej uruchomionej gry."""

    def __init__(self, session, adapter: ingame.Adapter, ui):
        self.s = session
        self.adapter = adapter
        self.ui = ui                 # menu_show(items, msg), menu_hide(), menu_input(a)
        self.open = False
        self.busy = False
        self.new_resume = False
        self._thread = threading.Thread(target=self._watch, daemon=True, name="hotkey")

    def start(self) -> None:
        backend = getattr(self.ui, "pad_backend", lambda: "python")()
        if backend == "none":
            log.info("obsługa padów wyłączona — menu w grze (A+Y) nieaktywne")
            return
        if xinput.available():
            self._thread.start()
        else:
            log.warning("XInput niedostępny — menu w grze wyłączone")

    def items(self) -> list:
        a = self.adapter
        return [
            {"id": "back", "label": "Wróć do gry", "on": True},
            {"id": "save", "label": "Zapisz stan", "on": a.can_states()},
            {"id": "load", "label": "Wczytaj stan", "on": a.can_states()},
            {"id": "quicksave", "label": "Quicksave i wyjdź", "on": a.can_resume(),
             "hint": "przy następnym uruchomieniu gra wczyta ten stan"},
            {"id": "exit", "label": "Wyjdź z gry", "on": True},
        ]

    # ── pad ──
    def _watch(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("wątek pada padł")

    def _loop(self) -> None:
        proc = self.s.proc
        release_since = quiet_since = 0.0
        held_since = None
        armed = True
        prev = 0
        repeat_at = 0.0
        wait_release = False
        while proc.poll() is None:
            b = xinput.all_buttons()
            now = time.monotonic()
            if not self.open:
                combo = (b & xinput.A) and (b & xinput.Y)
                if combo and armed:
                    held_since = held_since or now
                    if now - held_since >= HOLD_SECONDS and not self.busy:
                        armed = False
                        held_since = None
                        wait_release = True
                        quiet_since = 0.0
                        self._open()
                elif not combo:
                    held_since = None
                    if not b & (xinput.A | xinput.Y):
                        armed = True
            else:
                if wait_release:              # A+Y wciąż wciśnięte po otwarciu menu
                    release_since = release_since or now
                    if b:
                        quiet_since = 0.0
                    elif not quiet_since:
                        quiet_since = now
                    # pad puszczony przez 0,2 s (A i Y nie puszcza się równocześnie)
                    wait_release = b != 0 or now - quiet_since < 0.2
                    if wait_release and b and now - release_since > 3:
                        log.warning("pad wciąż zgłasza przyciski 0x%04x — ignoruję", b)
                        wait_release = False
                    if not wait_release:
                        release_since = 0.0
                else:
                    pressed = b & ~prev
                    for mask, act in ((xinput.A, "a"), (xinput.B, "b"),
                                      (xinput.START, "b")):
                        if pressed & mask:
                            self.ui.menu_input(act)
                    for mask, act in ((xinput.UP, "up"), (xinput.DOWN, "down")):
                        if pressed & mask:
                            self.ui.menu_input(act)
                            repeat_at = now + 0.4
                        elif b & mask and now >= repeat_at:
                            self.ui.menu_input(act)
                            repeat_at = now + 0.12
            prev = b
            time.sleep(POLL)

    def _emu_window(self) -> int:
        wins = winutil.windows_of(self.s.proc.pid)
        return wins[0] if wins else 0

    def _focus_emulator(self) -> None:
        winutil.bring_to_front(self._emu_window())
        time.sleep(0.35)

    def _wait_release(self, timeout: float = 1.5) -> None:
        """Puszczamy fokus do gry dopiero, gdy nikt nie trzyma przycisku —
        inaczej A wybierające „Wróć do gry” trafiłoby też do gry."""
        end = time.monotonic() + timeout
        while xinput.all_buttons() and time.monotonic() < end:
            time.sleep(POLL)

    def _open(self) -> None:
        log.info("menu w grze")
        self.adapter.pause()               # emulator ma jeszcze fokus
        self.open = True
        self.ui.menu_show(self.items(), "")

    def _close_to_game(self, unpause: bool = True) -> None:
        self.open = False
        self.ui.menu_hide()
        self._wait_release()
        self._focus_emulator()
        if unpause:
            self.adapter.pause()

    # ── akcje (wywoływane z UI) ──
    def action(self, name: str) -> None:
        if self.busy or not self.open:
            return
        self.busy = True
        threading.Thread(target=self._do, args=(name,), daemon=True, name="menu-act").start()

    def _do(self, name: str) -> None:
        try:
            {"back": self._back, "save": self._save, "load": self._load,
             "quicksave": self._quicksave, "exit": self._exit}[name]()
        except Exception:
            log.exception("akcja menu %s", name)
        finally:
            self.busy = False

    def _back(self) -> None:
        self._close_to_game()

    def _save(self) -> None:
        self._close_to_game(unpause=False)
        self.adapter.save_state()        # emulator pokazuje własny komunikat
        time.sleep(0.5)
        self.adapter.pause()

    def _load(self) -> None:
        self._close_to_game(unpause=False)
        self.adapter.load_state()
        time.sleep(0.5)
        self.adapter.pause()

    def _quicksave(self) -> None:
        g, a = self.s.game, self.adapter
        self.open = False
        self.ui.menu_hide()
        self._wait_release()
        self._focus_emulator()
        if a.family == "retroarch":
            # RetroArch zapisze autostan przy wyjściu; przy starcie włączymy autowczytanie
            library.set_resume(self.s.profile_id, g["id"], a.family, "")
        else:
            since = time.time() - 1
            a.save_state()
            state = a.wait_for_state(since)
            if not state:
                log.warning("quicksave: nie znaleziono zapisanego stanu")
                self.open = True
                self.ui.menu_show(self.items(), "Nie udało się zapisać stanu. Gra nie została zamknięta.")
                return
            kept = ingame.keep_resume(self.s.profile_id, g["id"], state)
            library.set_resume(self.s.profile_id, g["id"], a.family, str(kept))
        self.new_resume = True
        self._quit()

    def _exit(self) -> None:
        self.open = False
        self.ui.menu_hide()
        self._wait_release()
        self._focus_emulator()
        self._quit()

    def _quit(self) -> None:
        proc = self.s.proc
        self.adapter.quit(proc)
        # Emulator może zapytać o potwierdzenie (DuckStation: ConfirmPowerOff) —
        # ma wtedy fokus, więc wystarczy A na padzie. Po 15 s zamykamy siłą.
        end = time.monotonic() + 15
        while proc.poll() is None and time.monotonic() < end:
            time.sleep(0.2)
        if proc.poll() is None:
            log.warning("emulator nie zamknął się sam — terminate")
            proc.terminate()
