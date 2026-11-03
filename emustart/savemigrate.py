"""savemigrate — zapisy rdzeni sprzed 0.47 → wspólne paczki grup zgodnych rdzeni (savesync).

Migracja zapisów sprzed 0.46 (foldery profili → paczki) usunięta w 0.56.1: nikt już nie używa
wersji sprzed 0.46. Wersje, które dodała do historii paczek, zostają i przywraca się je
jak każdą inną wersję z historii.
"""

from __future__ import annotations

import json
import os

from emustart import savesync


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
