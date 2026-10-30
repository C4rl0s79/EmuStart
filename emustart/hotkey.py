"""hotkey — skróty pada w trakcie gry, jednakowe w każdym emulatorze: przytrzymany LB +

    A        menu (własne menu emulatora, gdy je ma na pada — inaczej menu EmuStart)
    Y        zapisz w kolejnym slocie        X   zapisz w wybranym slocie
    ↑ / ↓    wybór slotu: wyżej / niżej na liście slotów (↑ = niższy numer); potem przez 3 s
             samo A wczytuje wybrany slot, Y zapisuje do niego, B zamyka wybór
    RB       przyspieszenie
    R3       zrzut ekranu                    L3  nagrywanie (gdzie emulator je ma)

RetroArch robi to sam (ustawienia sesji, ingame.RetroArch.launch_args) — tu nic.
Pozostałe: pad czytamy przez XInput i hidpad (pady HID: PS, Switch, GameSir w trybie HID) w osobnym wątku (fokus ma emulator, nie nasze okno)
i naciskamy skróty klawiszowe emulatora (Adapter.menu_key, slot_keys…). LB trafia też do
gry — emulator czyta pada sam.

Gdy menu jest otwarte, ten sam wątek steruje menu (krzyżak, A, B) i podaje
akcje do UI. Gamepad API w oknie jest wtedy ignorowane, żeby nie liczyć
naciśnięć podwójnie.
"""

from __future__ import annotations

import logging
import threading
import time

from emustart import hidpad, ingame, library, winutil, xinput

log = logging.getLogger("emustart.hotkey")

POLL = 1 / 60
PICK = 3.0          # po LB + ↑/↓: tyle sekund A = wczytaj, Y = zapisz, B = anuluj (lista slotów na ekranie)


def _buttons() -> int:
    """Przyciski wszystkich padów: XInput + HID (maska XInput)."""
    return xinput.all_buttons() | hidpad.all_buttons()
# LB + przycisk → akcja (kolejność: pierwsze wciśnięte wygrywa)
COMBOS = ((xinput.A, "menu"), (xinput.Y, "save_next"), (xinput.X, "save_here"), (xinput.UP, "slot_up"),
          (xinput.DOWN, "slot_down"), (xinput.RB, "ff"), (xinput.R3, "screenshot"), (xinput.L3, "record"))


class GameMenu:
    """Obsługa menu dla jednej uruchomionej gry."""

    def __init__(self, session, adapter: ingame.Adapter, ui):
        self.s = session
        self.adapter = adapter
        self.ui = ui                 # menu_show(items, msg), menu_hide(), menu_input(a)
        self.open = False
        self.busy = False
        self.new_resume = False
        self.slot = 1                # slot wybierany przez EmuStart (emulator bez własnego wyboru slotu)
        self._thread = threading.Thread(target=self._watch, daemon=True, name="hotkey")

    def start(self) -> None:
        backend = getattr(self.ui, "pad_backend", lambda: "python")()
        if backend == "none":
            log.info("obsługa padów wyłączona — skróty LB + … nieaktywne")
            return
        if self.adapter.family == "retroarch":
            return                   # RetroArch: skróty LB w ustawieniach sesji
        if xinput.available() or hidpad.available():
            self._thread.start()
        else:
            log.warning("XInput i HID niedostępne — menu w grze wyłączone")

    def items(self) -> list:
        a = self.adapter
        return [
            {"id": "back", "label": "Wróć do gry", "on": True},
            {"id": "save", "label": "Zapisz stan", "on": a.can_states()},
            {"id": "load", "label": "Wczytaj stan", "on": a.can_states()},
            # gra od początku (np. wczytany przy starcie stan był zły) — bez wychodzenia
            {"id": "reset", "label": "Reset gry", "on": a.can_reset(), "hint": "od początku, jak przycisk RESET konsoli"},
            {"id": "quicksave", "label": "Quicksave i wyjdź", "on": a.can_resume(),
             "hint": "przy następnym uruchomieniu gra wczyta ten stan"},
            # czarny ekran po zmianie ustawień (shader niezgodny np. ze skalą rozdzielczości)
            {"id": "shader", "label": "Włącz / wyłącz shader", "on": a.family == "retroarch",
             "hint": "do końca tej gry"},
            {"id": "shader_off", "label": "Bez shadera w tym systemie", "on": a.family == "retroarch",
             "hint": "na stałe — przywrócisz w opcjach gry"},
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
        prev = 0
        repeat_at = 0.0
        wait_release = False
        while proc.poll() is None:
            b = _buttons()
            now = time.monotonic()
            if not self.open:
                pressed = b & ~prev
                if getattr(self, "_pick_until", 0) > now and pressed and not b & xinput.LB and not self.busy:
                    # lista slotów z LB + ↑/↓ na ekranie: A wczytuje wybrany slot, Y zapisuje, B zamyka
                    act = next((a for m, a in ((xinput.A, "load_here"), (xinput.Y, "save_here"),
                                               (xinput.B, "cancel")) if pressed & m), "")
                    if act:
                        self._pick_until = 0.0
                        if act != "cancel":
                            self._shortcut(act)
                        else:
                            log.info("skrót: wybór slotu anulowany")
                elif b & xinput.LB and pressed and not self.busy:
                    act = next((a for m, a in COMBOS if pressed & m), "")
                    if act == "menu" and not self.adapter.menu_key():
                        wait_release = True          # LB + A wciąż wciśnięte po otwarciu menu
                        quiet_since = 0.0
                        self._open()
                    elif act:
                        self._shortcut(act)
            else:
                if wait_release:              # LB + A wciąż wciśnięte po otwarciu menu
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

    def _shortcut(self, act: str) -> None:
        """LB + przycisk: skrót klawiszowy emulatora (fokus ma emulator)."""
        a = self.adapter
        nxt, prv = a.slot_keys()
        if act == "menu":
            keys = a.menu_key()
        elif act == "save_next":
            if nxt:                              # następny slot emulatora, potem zapis do wybranego
                winutil.send_keys(nxt)
                time.sleep(0.15)
                keys = getattr(a, "save_here_key", lambda: "")() or a.save_key()
            elif a.slot_save_key(1):
                self.slot = self.slot % 8 + 1
                keys = a.slot_save_key(self.slot)
            else:
                keys = a.save_key()
        elif act == "save_here":
            keys = getattr(a, "save_here_key", lambda: "")() or a.slot_save_key(self.slot) or a.save_key()
        elif act == "load_here":
            keys = getattr(a, "load_here_key", lambda: "")() or a.load_key()
        elif act in ("slot_up", "slot_down"):
            # ↑ = wyżej na liście slotów emulatora (niższy numer), ↓ = niżej — jak znacznik na ekranie
            if a.slot_save_key(1) and not (nxt or prv):
                self.slot = ((self.slot - 2) % 8 + 1) if act == "slot_up" else (self.slot % 8 + 1)
                log.info("skrót: slot zapisu %d", self.slot)
                self._pick_until = time.monotonic() + PICK
                return
            keys = prv if act == "slot_up" else nxt
            self._pick_until = time.monotonic() + PICK
        elif act == "ff" and a.ff_hold():
            # przyspieszenie „trzymane” (Dolphin): LB + RB wciska klawisz, kolejne LB + RB puszcza
            self._ff_down = not getattr(self, "_ff_down", False)
            log.info("skrót LB + ff: %s %s", a.ff_key(), "wciśnięty" if self._ff_down else "puszczony")
            winutil.key_state(a.ff_key(), self._ff_down)
            return
        else:
            keys = {"ff": a.ff_key, "screenshot": a.screenshot_key, "record": a.record_key}[act]()
        log.info("skrót LB + %s: %s", act, keys or "(emulator nie ma takiego skrótu)")
        winutil.send_keys(keys)

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
        while _buttons() and time.monotonic() < end:
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
             "quicksave": self._quicksave, "exit": self._exit,
             "shader": self._shader, "shader_off": self._shader_off, "reset": self._reset}[name]()
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

    def _reset(self) -> None:
        self._close_to_game(unpause=False)
        self.adapter.reset_game()
        time.sleep(0.3)
        self.adapter.pause()

    def _shader(self) -> None:
        self._close_to_game(unpause=False)
        self.adapter.toggle_shader()
        self._shader_flipped = not getattr(self, "_shader_flipped", False)

    def _shader_off(self) -> None:
        """Shader wyłączony dla systemu na stałe (ustawienie EmuStart, emustart/launcher.py)
        i od razu w tej grze, jeśli jeszcze działa."""
        es = (self.s.game or {}).get("es", "")
        if es:
            self.s.cfg.setdefault("systems", {}).setdefault(es, {})["shader_off"] = True
            from emustart import config
            config.save(self.s.cfg)
        if not self.adapter.shader_off and not getattr(self, "_shader_flipped", False):
            self._shader()
        else:
            self._close_to_game()

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
