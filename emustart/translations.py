"""translations — fanowskie tłumaczenia jako wersje gry (jak w RomHelperze).

Tłumaczenia leżą w osobnym folderze kolekcji, np. „[T-En] Collection\\<system>\\”
(folder z gierami dodany w ustawieniach) albo w folderze systemu z tagiem w nazwie
(„Nintendo - SNES [T-En]”). Plik z takiego folderu albo z tagiem `[T-…]` w nazwie
jest WERSJĄ gry o tym samym tytule bazowym (bez tagów () i []): na liście jest
jedna gra, a w opcjach gry „Wersja” wybiera się oryginał albo tłumaczenie.
Tłumaczenie bez oryginału w bibliotece jest zwykłą grą na liście.

Pliki na dysku zostają nietknięte. Wybór wersji należy do profilu gracza
(`<profil>/variants.json` w folderze profili): {"<system>/<rel oryginału>":
"<rel tłumaczenia>"} — ścieżki względne, więc ten sam wybór działa na komputerze
i na telefonie (inne numery gier w bazach).
"""

from __future__ import annotations

import re

FILE = "variants.json"
HIDDEN = 2                     # games.hidden: wersja (tłumaczenie) schowana pod oryginałem

_LANG = {
    "en": "en", "eng": "en", "english": "en",
    "fr": "fr", "fre": "fr", "french": "fr", "francais": "fr", "français": "fr",
    "de": "de", "ger": "de", "german": "de", "deutsch": "de",
    "es": "es", "spa": "es", "spanish": "es", "espanol": "es", "español": "es",
    "it": "it", "ita": "it", "italian": "it", "italiano": "it",
    "pt": "pt", "por": "pt", "portuguese": "pt", "br": "pt",
    "pl": "pl", "pol": "pl", "polish": "pl", "polski": "pl",
    "ru": "ru", "rus": "ru", "russian": "ru",
    "nl": "nl", "dut": "nl", "dutch": "nl",
    "sv": "sv", "swe": "sv", "swedish": "sv",
    "ja": "ja", "jp": "ja", "jpn": "ja", "japanese": "ja",
    "ko": "ko", "kor": "ko", "korean": "ko",
    "zh": "zh", "chi": "zh", "chinese": "zh",
    "ca": "ca", "cat": "ca", "catalan": "ca",
    "da": "da", "dan": "da", "danish": "da",
    "fi": "fi", "fin": "fi", "finnish": "fi",
    "no": "no", "nor": "no", "norwegian": "no",
    "el": "el", "gre": "el", "greek": "el",
    "hu": "hu", "hun": "hu", "hungarian": "hu",
    "cs": "cs", "cze": "cs", "czech": "cs",
    "tr": "tr", "tur": "tr", "turkish": "tr",
    "ar": "ar", "ara": "ar", "arabic": "ar",
}

# [T-En], [T+Eng], (T-En), [T-En by Foo v1.1], [T+Pol]
_T_TAG = re.compile(r"[\[(]\s*T[-+]\s*([A-Za-z]+)", re.I)
_T_LABEL = re.compile(r"[\[(]\s*T[-+][^\])]*[\])]", re.I)
_BRACKETS = re.compile(r"[\[(][^\])]*[\])]")
# folder kolekcji tłumaczeń: tag [T-…], sam „T-Eng” / „T-En” / „T+Pol” albo słowo w nazwie
_FOLDER_T = re.compile(r"(?:^|[\s\[(_])T\s*[-+]\s*([A-Za-z]{2,})", re.I)
_POOL = re.compile(r"(?:^|[\s\[(_])T\s*[-+]\s*[A-Za-z]{2,}|translat|t[łl]umacz|traduc|[üu]bersetz", re.I)


def base_title(name: str) -> str:
    """Tytuł bez tagów () i [] — do parowania tłumaczenia z oryginałem."""
    return re.sub(r"\s+", " ", _BRACKETS.sub(" ", name or "")).strip().lower()


def _lang(tok: str) -> str:
    t = (tok or "").strip().lower()
    for cand in (t, t[:3], t[:2]):
        if cand in _LANG:
            return _LANG[cand]
    return ""


def lang_of(*names: str) -> str:
    """Język tłumaczenia z tagu `[T-…]` w nazwie pliku albo `T-Eng` w nazwie folderu
    (pierwsza nazwa, która go ma) — kod ISO albo ""."""
    for n in names:
        for m in list(_T_TAG.finditer(n or "")) + list(_FOLDER_T.finditer(n or "")):
            c = _lang(m.group(1))
            if c:
                return c
    return ""


def is_translation(name: str) -> bool:
    return bool(_T_TAG.search(name or ""))


def is_pool(*folder_names: str) -> bool:
    """Czy folder (albo folder nadrzędny) jest kolekcją tłumaczeń."""
    return any(_POOL.search(n or "") for n in folder_names)


def strip_pool_tag(folder: str) -> str:
    """Nazwa systemu z nazwy folderu kolekcji (do rozpoznania systemu):
    „Nintendo - Super Nintendo Entertainment System [T-En] Collection (20240101)”,
    „Nintendo - SNES T-Eng”, „[T-En] Collection” → „Nintendo - …” / „Nintendo - SNES” / „”."""
    out = _BRACKETS.sub(" ", folder or "")                         # [T-En], (20240101), (Translated)
    out = _FOLDER_T.sub(" ", out)                                   # T-Eng, T+Pol
    out = re.sub(r"\b(collection|translations?|translated|t[łl]umaczenia|kolekcja)\b", " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" -_")


def split_system(folder: str, match) -> tuple:
    """(system, dopisek) folderu z kolekcji: „Nintendo - Super Famicom - Speed Hacks”
    → ("snes", "Speed Hacks"); „Nintendo - Super Famicom [T-En] Collection” → ("snes", "").
    `match` — systems.match_folder."""
    base = strip_pool_tag(folder)
    if not base:
        return None, ""
    es = match(base)
    if es:
        return es, ""
    parts = base.split(" - ")
    for k in range(len(parts) - 1, 0, -1):
        es = match(" - ".join(parts[:k]))
        if es:
            prefix = " - ".join(parts[:k])
            rest = folder[len(prefix):] if folder.startswith(prefix) else " - ".join(parts[k:])
            return es, rest.strip(" -_")
    return None, ""


def pool_trans(folder: str, parent: str, match) -> str:
    """Wartość games.trans dla gier z folderu kolekcji: kod języka (z `[T-En]` w nazwie
    folderu), „+dopisek” dla wersji innych niż tłumaczenie (Speed Hacks, MSU1 — folder
    bez tagu w kolekcji) albo język kolekcji / „?”."""
    lang = lang_of(folder)
    if lang:
        return lang
    _es, rest = split_system(folder, match)
    if rest and not is_pool(folder):
        return "+" + rest
    return lang_of(parent) or "?"


def label(name: str, lang: str) -> str:
    """Etykieta wersji: tag `[T-…]` z nazwy (autor, wersja), dopisek folderu albo język."""
    m = _T_LABEL.search(name or "")
    if m:
        return m.group(0)
    if lang.startswith("+"):
        return lang[1:]
    return f"Tłumaczenie ({lang.upper()})" if lang and lang != "?" else "Tłumaczenie"


def mark(rows: list) -> None:
    """Skan systemu: oznacza wersje w wierszach gier (słowniki z kluczami name, hidden
    i pool — trans folderu kolekcji albo None; dopisuje trans i tbase). Wersja (z kolekcji
    albo z tagiem `[T-…]` w nazwie) z oryginałem w bibliotece chowa się pod nim."""
    originals = set()
    for r in rows:
        r["tbase"] = base_title(r["name"])
        pool = r.get("pool")
        if pool is not None or is_translation(r["name"]):
            r["trans"] = lang_of(r["name"]) or pool or "?"
        else:
            r["trans"] = ""
            originals.add(r["tbase"])
    for r in rows:
        if r["trans"] and r["tbase"] in originals and not r["hidden"]:
            r["hidden"] = HIDDEN
