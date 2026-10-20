"""devices — urządzenia zalogowane na serwerze EmuStart.

Urządzenie = klucz urządzenia (klucz publiczny EC P-256; prywatny nie opuszcza urządzenia:
TPM / DPAPI / Android Keystore) przypisany do **konta**. Jedyna droga do rejestru to
logowanie kontem i hasłem (emustart/enroll.py): zatwierdzone na innym urządzeniu konta,
przez administratora albo kodem QR z panelu serwera. Każde zapytanie: token sesji z
logowania podpisem.

Uprawnienia wynikają z konta (emustart/accounts.py): konto administratora widzi wszystkie
konta i zatwierdza nowe; zwykłe — tylko swoje. Urządzenie ma jeszcze przełącznik „gry”
(pobieranie gier z serwera), ustawiany przy zatwierdzeniu i w panelu.

Rejestr: `<katalog serwera>\\devices.json` (paths.SERVER) — klucze publiczne, nazwy, stan.
Wpisy sprzed 0.43 bez konta (parowanie kodem, stary klucz-hasło) są unieważniane przy
wczytaniu: urządzenie loguje się ponownie.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time

from emustart import paths

log = logging.getLogger("emustart.devices")

SEEN_SAVE = 60                   # ostatni kontakt zapisywany na dysk najwyżej co tyle s
CLASSES = ("pc", "phone")

_lock = threading.Lock()
_cache: dict | None = None
_dirty_at = 0.0


def _path():
    return paths.SERVER / "devices.json"


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
        _retire_unbound()
    return _cache


def _retire_unbound() -> None:
    """Wpisy bez konta albo bez klucza urządzenia (parowanie kodem 0.32–0.42, klucz-hasło)
    — dostęp tylko przez logowanie kontem."""
    gone = []
    for did, d in (_cache or {}).items():
        if not d.get("revoked") and not (d.get("account") and d.get("pubkey")):
            d["revoked"] = time.time()
            d["revoked_reason"] = "stare parowanie bez konta — zaloguj urządzenie ponownie"
            gone.append(d.get("name", did))
    if gone:
        _save()
        log.info("unieważnione urządzenia bez konta (stare parowanie): %s", ", ".join(gone))


def _save() -> None:
    global _dirty_at
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_cache or {}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _dirty_at = 0.0


def reset_cache() -> None:
    """Dla testów i po zmianie katalogu serwera."""
    global _cache
    with _lock:
        _cache = None


def app_name(app: str, cls: str = "pc") -> str:
    """Program urządzenia („EmuStart”, „EmuStart Android”, „Lumen”…); bez nazwy — EmuStart
    tej klasy urządzenia (rejestracje sprzed 0.47)."""
    a = re.sub(r"[^\w .+-]+", "", str(app or "")).strip()[:40]
    return a or ("EmuStart Android" if cls == "phone" else "EmuStart")


def app_of(d: dict) -> str:
    return app_name(d.get("app", ""), d.get("class", "pc"))


def add_keyed(name: str, cls: str, ip: str, version: str, pubkey: str, account: str,
              games: bool = False, by: str = "", hw: str = "", app: str = "") -> str:
    """Nowe urządzenie konta (po zatwierdzeniu logowania)."""
    did = secrets.token_hex(6)
    now = time.time()
    cls = cls if cls in CLASSES else "pc"
    with _lock:
        _load()[did] = {"name": (str(name).strip() or "urządzenie")[:40], "class": cls, "app": app_name(app, cls),
                        "pubkey": pubkey, "account": account, "games": bool(games), "created": now,
                        "last_seen": now, "ip": ip, "version": version, "approved_by": by}
        if _valid_hw(hw):
            _load()[did]["hw"] = _valid_hw(hw)
            replace_same_hw(did, hw)
        _save()
    log.info("nowe urządzenie %s (%s) konta %s — %s", name, cls, account, by or "?")
    return did


def verify(token: str) -> str:
    """Id urządzenia dla tokenu sesji (logowanie podpisem, emustart/enroll.py); '' = nieznany,
    unieważniony, wygasły albo urządzenie bez konta."""
    if not token:
        return ""
    from emustart import enroll
    did = enroll.token_device(token)
    if not did:
        return ""
    with _lock:
        d = _load().get(did)
    return did if d and not d.get("revoked") and d.get("account") else ""


def _valid_hw(hw: str) -> str:
    hw = (hw or "").strip().lower()
    return hw if len(hw) == 32 and all(c in "0123456789abcdef" for c in hw) else ""


def replace_same_hw(did: str, hw: str) -> list:
    """Jedna rejestracja na sprzęt i program: inne aktywne rejestracje tego samego programu na
    tym samym sprzęcie (ponowne zalogowanie, nowa instalacja aplikacji) są unieważniane — inny
    program na tym komputerze (EmuStart i Lumen) zostaje. Wołający trzyma _lock. Zwraca nazwy.
    Wołane tylko przy dodaniu urządzenia (zatwierdzone logowanie, które pokazuje „zastąpi: …”).
    Bez sięgania po blokadę próśb (emustart/enroll.py) — odwrotna kolejność blokad groziłaby
    zakleszczeniem; sesji unieważnionych i tak nie przyjmie `verify`."""
    hw = _valid_hw(hw)
    if not hw:
        return []
    gone = []
    mine = app_of(_load().get(did) or {})
    for other, d in _load().items():
        if other != did and not d.get("revoked") and d.get("hw") == hw and app_of(d) == mine:
            d["revoked"] = time.time()
            d["replaced_by"] = did
            gone.append(d.get("name", other))
    if gone:
        log.info("ten sam sprzęt — zastąpione rejestracje: %s", ", ".join(gone))
    return gone


def same_hw(hw: str, app: str = "", cls: str = "pc") -> list:
    """Aktywne rejestracje, które zastąpi nowa (ten sam sprzęt i program) — do prośby o
    zalogowanie: „zastąpi: <nazwa> (<program>, ostatnio …)”."""
    hw, mine = _valid_hw(hw), app_name(app, cls)
    with _lock:
        return [f"{d.get('name', k)} ({app_of(d)}{', wersja ' + d['version'] if d.get('version') else ''})"
                for k, d in _load().items() if hw and not d.get("revoked") and d.get("hw") == hw and app_of(d) == mine]


def touch(did: str, ip: str = "", version: str = "", hw: str = "") -> None:
    """Ostatni kontakt urządzenia (na dysk najwyżej co SEEN_SAVE s). `hw` — skrót
    identyfikatora sprzętu: zapamiętywany raz, gdy rejestracja go nie ma; bez unieważniania
    innych (nagłówek ustawia samo urządzenie)."""
    global _dirty_at
    with _lock:
        d = _load().get(did)
        if not d:
            return
        now = time.time()
        hw = _valid_hw(hw)
        if hw and not d.get("hw"):
            d["hw"] = hw
            _save()
        d["last_seen"] = now
        if ip:
            d["ip"] = ip
        if version:
            d["version"] = version
        if not _dirty_at:
            _dirty_at = now
        if now - _dirty_at >= SEEN_SAVE:
            _save()


def flush() -> None:
    with _lock:
        if _dirty_at:
            _save()


def listing() -> list:
    from emustart import accounts
    with _lock:
        out = [{"id": did, **{k: v for k, v in d.items() if k != "pubkey"},
                "admin": accounts.is_admin(d.get("account", ""))} for did, d in _load().items()]
    out.sort(key=lambda d: -d.get("last_seen", 0))
    return out


def listing_one(did: str) -> dict:
    """Wpis urządzenia (bez klucza publicznego) — {} gdy nie ma."""
    with _lock:
        d = dict(_load().get(did) or {})
    d.pop("pubkey", None)
    return d


def revoke(did: str) -> bool:
    with _lock:
        d = _load().get(did)
        if not d:
            return False
        d["revoked"] = time.time()
        _save()
    from emustart import enroll
    enroll.drop_tokens(did)
    log.info("unieważniono urządzenie %s", d.get("name"))
    return True


def name_of(did: str) -> str:
    with _lock:
        return (_load().get(did) or {}).get("name", "") if did else "panel serwera"


# ── konto i uprawnienia ──
def account_raw(did: str) -> str:
    """Konto urządzenia (zawsze, także administratora)."""
    with _lock:
        d = _load().get(did) or {}
    return "" if d.get("revoked") else d.get("account", "")


def is_admin(did: str) -> bool:
    """'' = ten komputer (panel, wspólny klucz tylko lokalnie); urządzenie — konto administratora."""
    if not did:
        return True
    from emustart import accounts
    acc = account_raw(did)
    return bool(acc) and accounts.is_admin(acc)


def account_of(did: str) -> str:
    """Konto, do którego urządzenie jest ograniczone ('' = wszystkie: administrator, ten komputer)."""
    if not did or is_admin(did):
        return ""
    return account_raw(did)


def games_ok(did: str) -> bool:
    if not did or is_admin(did):
        return True
    with _lock:
        d = _load().get(did) or {}
    return bool(d.get("games"))


def accounts_in_use() -> set:
    """Konta, którymi zalogowane są aktywne urządzenia."""
    with _lock:
        return {d["account"] for d in _load().values() if not d.get("revoked") and d.get("account")}


def of_account(account: str) -> list:
    """Aktywne urządzenia konta."""
    with _lock:
        return [did for did, d in _load().items()
                if not d.get("revoked") and (d.get("account") or "").lower() == account.lower()]


def pubkey_of(did: str) -> str:
    with _lock:
        d = _load().get(did) or {}
    return "" if d.get("revoked") or not d.get("account") else d.get("pubkey", "")


def set_games(did: str, games: bool) -> bool:
    with _lock:
        d = _load().get(did)
        if not d:
            return False
        d["games"] = bool(games)
        _save()
    return True


def rename(did: str, name: str) -> bool:
    with _lock:
        d = _load().get(did)
        if not d or not str(name).strip():
            return False
        d["name"] = str(name).strip()[:40]
        _save()
    return True
