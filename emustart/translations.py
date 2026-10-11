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
    """„Nintendo - SNES [T-En]” / „Nintendo - SNES T-Eng” → „Nintendo - SNES” (rozpoznanie systemu)."""
    out = re.sub(r"\s*[\[(]\s*T[-+][^\])]*[\])]", "", folder or "")
    return re.sub(r"\s*(?:^|\s)T\s*[-+]\s*[A-Za-z]{2,}\s*$", "", out, flags=re.I).strip(" -_")


def label(name: str, lang: str) -> str:
    """Etykieta wersji: tag `[T-…]` z nazwy (autor, wersja) albo język."""
    m = _T_LABEL.search(name or "")
    if m:
        return m.group(0)
    return f"Tłumaczenie ({lang.upper()})" if lang else "Tłumaczenie"


def mark(rows: list, pool_of: dict) -> None:
    """Skan systemu: oznacza tłumaczenia w wierszach gier (słowniki z kluczami
    name, src, hidden; dopisuje trans i tbase). `pool_of[src]` — czy folder jest
    kolekcją tłumaczeń (i jego nazwy, z których można odczytać język)."""
    originals = set()
    for r in rows:
        pool = pool_of.get(r["src"])
        r["tbase"] = base_title(r["name"])
        if pool is not None or is_translation(r["name"]):
            r["trans"] = lang_of(r["name"], *(pool or ())) or "?"
        else:
            r["trans"] = ""
            originals.add(r["tbase"])
    for r in rows:
        if r["trans"] and r["tbase"] in originals and not r["hidden"]:
            r["hidden"] = HIDDEN
