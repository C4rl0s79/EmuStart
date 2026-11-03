"""mediaprogress — postęp pobierania grafik platform w tle (logo, zdjęcia konsol) dla panelu serwera.

Każdy rodzaj ma jeden licznik: kolejne partie (nowe systemy w trakcie) dopisują się do bieżącej.
Wynik systemu: found (zapisane), none (sprawdzone — nie ma), retry (błąd, następnym razem od nowa).
Czas i błąd każdego systemu idą też do logu serwera.
"""

from __future__ import annotations

import logging
import threading
import time


class Progress:
    def __init__(self, what: str):
        self.what = what
        self.log = logging.getLogger(f"emustart.{what}")
        self._lock = threading.Lock()
        self._d = self._empty()

    @staticmethod
    def _empty() -> dict:
        return {"running": False, "done": 0, "total": 0, "current": "", "found": 0, "none": 0, "retry": 0,
                "last_error": "", "slowest": 0.0, "seconds": 0.0, "started": 0.0, "label": ""}

    def start(self, n: int, label: str = "") -> None:
        """`label` — co trwa (np. „paczka Canvas”); bez niego pobieranie brakujących w tle."""
        with self._lock:
            if not self._d["running"]:
                self._d = {**self._empty(), "running": True, "started": time.time()}
            self._d["total"] += n
            if label:
                self._d["label"] = label

    def begin(self, es: str) -> float:
        with self._lock:
            self._d["current"] = es
        return time.monotonic()

    def end(self, es: str, t0: float, result: str, error: str = "") -> None:
        dt = time.monotonic() - t0
        with self._lock:
            d = self._d
            d["done"] += 1
            d[result] += 1
            d["slowest"] = max(d["slowest"], round(dt, 1))
            d["seconds"] = round(time.time() - d["started"], 1)
            if error:
                d["last_error"] = f"{es}: {error}"
            if d["done"] >= d["total"]:
                d.update(running=False, current="")
        self.log.info("%s %s: %s w %.1f s%s", self.what, es, result, dt, f" ({error})" if error else "")

    def finish(self) -> None:
        """Koniec partii także wtedy, gdy przerwał ją błąd (pasek nie zostaje „w toku”)."""
        with self._lock:
            self._d.update(running=False, current="")

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._d)
