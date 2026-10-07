"""uipad — sterowanie interfejsem padem przez XInput (w Pythonie).

Gamepad API w WebView2 potrafi nie zauważyć pada, który rozłączył się i połączył
ponownie w trakcie działania programu (np. pad Bluetooth uśpiony i wybudzony) —
interfejs przestawał wtedy reagować. XInput odpytywany w Pythonie widzi pad od
razu po podłączeniu, więc pady XInput obsługujemy tu, a Gamepad API w oknie
zostaje dla pozostałych (UI pomija pady z „XInput” w nazwie).

Zdarzenia zbieramy tylko, gdy okno EmuStart jest na pierwszym planie i nie trwa
gra (w grze pad czyta hotkey.GameMenu). UI odbiera je przez api.ui_pad_poll().
"""

from __future__ import annotations

import collections
import threading
import time

from emustart import xinput

POLL = 1 / 60
REPEAT_DELAY, REPEAT_SLOW, REPEAT_FAST, FAST_AFTER = 0.38, 0.075, 0.035, 1.6
DEADZONE = 16000

_BUTTONS = ((xinput.A, "a"), (xinput.B, "b"), (xinput.X, "x"), (xinput.Y, "y"),
            (0x0100, "lb"), (0x0200, "rb"), (xinput.BACK, "select"), (xinput.START, "start"),
            (xinput.UP, "up"), (xinput.DOWN, "down"), (xinput.LEFT, "left"), (xinput.RIGHT, "right"))
_REPEATABLE = {"up", "down", "left", "right", "lb", "rb", "lt", "rt"}


def _actions() -> set:
    """Wciśnięte akcje ze wszystkich padów (spusty jako lt/rt)."""
    out = set()
    if not xinput._dll:
        return out
    for slot in range(4):
        st = xinput._State()
        if xinput._dll.XInputGetState(slot, xinput.ctypes.byref(st)) != 0:
            continue
        g = st.Gamepad
        b = g.wButtons
        if g.sThumbLY > DEADZONE:
            b |= xinput.UP
        elif g.sThumbLY < -DEADZONE:
            b |= xinput.DOWN
        if g.sThumbLX < -DEADZONE:
            b |= xinput.LEFT
        elif g.sThumbLX > DEADZONE:
            b |= xinput.RIGHT
        out |= {a for mask, a in _BUTTONS if b & mask}
        if g.bLeftTrigger > 128:
            out.add("lt")
        if g.bRightTrigger > 128:
            out.add("rt")
    return out


class UiPad:
    def __init__(self, active):
        """`active()` → czy teraz zbierać zdarzenia (okno na wierzchu, brak gry)."""
        self._active = active
        self._q: collections.deque = collections.deque(maxlen=64)
        self._held: dict = {}
        self._lock = threading.Lock()
        self.last_event = 0.0          # kiedy ostatnio pad coś nacisnął (monotonic)
        if xinput.available():
            threading.Thread(target=self._loop, daemon=True, name="uipad").start()

    def poll(self) -> list:
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out

    def _emit(self, a: str, up: bool = False) -> None:
        self.last_event = time.monotonic()
        with self._lock:
            self._q.append({"a": a, "up": up})

    def _loop(self) -> None:
        while True:
            try:
                self._tick(time.monotonic())
            except Exception:
                pass
            time.sleep(POLL)

    def _tick(self, now: float) -> None:
        if not self._active():
            # po powrocie nie powtarzamy starych przycisków — ale pamiętamy, że są
            # wciśnięte, żeby nie wysłać ich drugi raz jako nowe naciśnięcie
            self._held = {a: [now, now + 3600] for a in _actions()} if xinput.available() else {}
            return
        active = _actions()
        for a in active:
            h = self._held.get(a)
            if h is None:
                self._held[a] = [now, now + REPEAT_DELAY]
                self._emit(a)
            elif a in _REPEATABLE and now >= h[1]:
                h[1] = now + (REPEAT_FAST if now - h[0] > FAST_AFTER else REPEAT_SLOW)
                self._emit(a)
        for a in list(self._held):
            if a not in active:
                del self._held[a]
                self._emit(a, up=True)
