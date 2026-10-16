"""savesync — server-authoritative synchronizacja zapisów gier między urządzeniami.

Serwer jest jedynym źródłem prawdy. Klient nie zna ścieżek serwera ani NAS;
odbiera wyłącznie krótkotrwałe, podpisane URL-e do pobrania lub wysłania bundle.

Klucz logiczny:
    account_id + device_id + platform_id + game_id + emulator_id + core_id + bundle_kind

Dla RetroArch rdzeń jest obowiązkowy: dane Snes9x i bsnes są całkowicie oddzielne.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from emustart import paths

log = logging.getLogger("emustart.savesync")

TOKEN_TTL = 900
MAX_BUNDLE = 256 * 1024 * 1024

_lock = threading.Lock()
_tokens: dict[str, dict] = {}
_uploads: dict[str, dict] = {}

_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _safe(part: str, name: str) -> str:
    if not part or not _SAFE.fullmatch(part):
        raise ValueError(f"Nieprawidłowy {name}.")
    return part


def _root() -> Path:
    return Path(paths.SERVER) / "savesync"


def _game_dir(account_id: str, platform_id: str, game_id: str) -> Path:
    return (_root() / _safe(account_id, "account_id") / _safe(platform_id, "platform_id")
            / _safe(game_id, "game_id"))


def _manifest_path(account_id: str, platform_id: str, game_id: str) -> Path:
    return _game_dir(account_id, platform_id, game_id) / "manifest.json"


def _bundle_key(emulator_id: str, core_id: str, bundle_kind: str) -> tuple[str, str, str]:
    emulator_id = _safe(emulator_id, "emulator_id")
    core_id = _safe(core_id or "_", "core_id")
    if bundle_kind not in ("saves", "states", "memcards"):
        raise ValueError("Nieznany rodzaj bundle.")
    return emulator_id, core_id, bundle_kind


def manifest_load(account_id: str, platform_id: str, game_id: str) -> dict:
    path = _manifest_path(account_id, platform_id, game_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def manifest_save(account_id: str, platform_id: str, game_id: str, manifest: dict) -> None:
    directory = _game_dir(account_id, platform_id, game_id)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".manifest.{secrets.token_hex(8)}.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_manifest_path(account_id, platform_id, game_id))


def _bundle_node(manifest: dict, emulator_id: str, core_id: str, bundle_kind: str,
                 create: bool = False) -> dict | None:
    emulator_id, core_id, bundle_kind = _bundle_key(emulator_id, core_id, bundle_kind)
    node = manifest.setdefault("emulators", {}).setdefault(emulator_id, {})
    if emulator_id == "retroarch":
        node = node.setdefault("cores", {}).setdefault(core_id, {})
    bundles = node.setdefault("bundles", {})
    if create:
        return bundles.setdefault(bundle_kind, {})
    return bundles.get(bundle_kind)


def _now() -> float:
    return time.time()


def _purge_tokens() -> None:
    now = _now()
    for token in [t for t, v in _tokens.items() if v["expires"] < now]:
        _tokens.pop(token, None)
    for upload_id in [u for u, v in _uploads.items() if v["expires"] < now]:
        _uploads.pop(upload_id, None)


def _new_token(op: str, **data) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _purge_tokens()
        _tokens[token] = {"op": op, "expires": _now() + TOKEN_TTL, **data}
    return token


def _token_get(token: str, op: str) -> dict | None:
    with _lock:
        _purge_tokens()
        item = _tokens.get(token)
        if item and item.get("op") == op and item["expires"] >= _now():
            return item
    return None


def init_upload(account_id: str, device_id: str, platform_id: str, game_id: str,
                emulator_id: str, core_id: str, bundle_kind: str,
                base_revision: int, sha256: str, size: int) -> dict:
    """Rezerwuje upload. Zwraca upload_id oraz krótkotrwały URL do PUT."""
    _safe(account_id, "account_id")
    _safe(device_id, "device_id")
    _safe(platform_id, "platform_id")
    _safe(game_id, "game_id")
    _bundle_key(emulator_id, core_id, bundle_kind)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256 or ""):
        raise ValueError("Nieprawidłowy SHA-256.")
    size = int(size)
    if size <= 0 or size > MAX_BUNDLE:
        raise ValueError("Nieprawidłowy rozmiar bundle.")
    manifest = manifest_load(account_id, platform_id, game_id)
    current = int(manifest.get("revision") or 0)
    if int(base_revision) != current:
        return {"ok": False, "conflict": True, "reason": "Nieaktualny base_revision.",
                "current_revision": current}
    upload_id = secrets.token_urlsafe(24)
    token = _new_token("upload", upload_id=upload_id, account_id=account_id,
                       device_id=device_id, platform_id=platform_id, game_id=game_id,
                       emulator_id=emulator_id, core_id=core_id or "_",
                       bundle_kind=bundle_kind, base_revision=int(base_revision),
                       sha256=sha256.lower(), size=size)
    with _lock:
        _uploads[upload_id] = {"token": token, "expires": _now() + TOKEN_TTL,
                               "account_id": account_id, "device_id": device_id,
                               "platform_id": platform_id, "game_id": game_id,
                               "emulator_id": emulator_id, "core_id": core_id or "_",
                               "bundle_kind": bundle_kind, "base_revision": int(base_revision),
                               "sha256": sha256.lower(), "size": size, "file": ""}
    return {"ok": True, "upload_id": upload_id,
            "upload_url": f"/v1/files/ul/{token}", "expires_in": TOKEN_TTL}


def store_upload(token: str, data: bytes) -> dict:
    """Zapisuje przesłany bundle w katalogu tymczasowym i weryfikuje SHA-256."""
    item = _token_get(token, "upload")
    if not item:
        return {"ok": False, "reason": "Token uploadu wygasł lub jest nieprawidłowy."}
    if len(data) != item["size"]:
        return {"ok": False, "reason": "Rozmiar pliku nie zgadza się z deklaracją."}
    digest = hashlib.sha256(data).hexdigest()
    if digest != item["sha256"]:
        return {"ok": False, "reason": "SHA-256 pliku nie zgadza się z deklaracją."}
    tmp_dir = _game_dir(item["account_id"], item["platform_id"], item["game_id"]) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / f"{item['upload_id']}.zip"
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    with _lock:
        upload = _uploads.get(item["upload_id"])
        if upload:
            upload["file"] = str(target)
    return {"ok": True, "upload_id": item["upload_id"]}


def commit_upload(account_id: str, device_id: str, upload_id: str,
                  base_revision: int, sha256: str, size: int,
                  force: bool = False) -> dict:
    """Zatwierdza upload jako nowy revision. force=True służy do rozwiązania konfliktu."""
    with _lock:
        upload = _uploads.get(upload_id)
    if not upload or upload.get("account_id") != account_id or upload.get("device_id") != device_id:
        return {"ok": False, "reason": "Nieznany upload."}
    if upload["expires"] < _now():
        return {"ok": False, "reason": "Upload wygasł."}
    if not upload.get("file") or not Path(upload["file"]).is_file():
        return {"ok": False, "reason": "Plik uploadu nie został przesłany."}
    if int(base_revision) != upload["base_revision"] or sha256.lower() != upload["sha256"] or int(size) != upload["size"]:
        return {"ok": False, "reason": "Dane commitu nie zgadzają się z inicjacją uploadu."}

    manifest = manifest_load(account_id, upload["platform_id"], upload["game_id"])
    current = int(manifest.get("revision") or 0)
    if not force and int(base_revision) != current:
        return {"ok": False, "conflict": True, "reason": "Nieaktualny base_revision.",
                "current_revision": current}

    emulator_id, core_id, bundle_kind = _bundle_key(upload["emulator_id"], upload["core_id"],
                                                     upload["bundle_kind"])
    game_dir = _game_dir(account_id, upload["platform_id"], upload["game_id"])
    bundles_dir = game_dir / "bundles" / emulator_id / core_id
    bundles_dir.mkdir(parents=True, exist_ok=True)
    target = bundles_dir / f"{bundle_kind}.zip"

    if target.is_file():
        backup_dir = game_dir / "backups" / str(int(_now()))
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_dir / target.name)

    tmp_target = target.with_suffix(".tmp")
    shutil.copy2(upload["file"], tmp_target)
    tmp_target.replace(target)

    new_revision = current + 1
    node = _bundle_node(manifest, emulator_id, core_id, bundle_kind, create=True)
    node.clear()
    node.update({
        "bundle_id": secrets.token_urlsafe(16),
        "revision": new_revision,
        "sha256": upload["sha256"],
        "size": upload["size"],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_by_device_id": device_id,
    })
    manifest["schema_version"] = 3
    manifest["revision"] = new_revision
    manifest["updated_at"] = node["updated_at"]
    manifest["updated_by_device_id"] = device_id
    manifest_save(account_id, upload["platform_id"], upload["game_id"], manifest)

    with _lock:
        _uploads.pop(upload_id, None)
    log.info("savesync commit: %s/%s/%s %s/%s/%s -> r%d", account_id, upload["platform_id"],
             upload["game_id"], emulator_id, core_id, bundle_kind, new_revision)
    return {"ok": True, "revision": new_revision}


def make_download(account_id: str, platform_id: str, game_id: str,
                  emulator_id: str, core_id: str, bundle_kind: str) -> str:
    """Zwraca krótkotrwałą ścieżkę URL do pobrania bundle albo pusty string."""
    emulator_id, core_id, bundle_kind = _bundle_key(emulator_id, core_id, bundle_kind)
    target = (_game_dir(account_id, platform_id, game_id) / "bundles" / emulator_id
              / core_id / f"{bundle_kind}.zip")
    if not target.is_file():
        return ""
    token = _new_token("download", account_id=account_id, platform_id=platform_id,
                       game_id=game_id, path=str(target))
    return f"/v1/files/dl/{token}"


def manifest_for_client(account_id: str, platform_id: str, game_id: str) -> dict:
    manifest = manifest_load(account_id, platform_id, game_id)
    if not manifest:
        return {"schema_version": 3, "platform_id": platform_id, "game_id": game_id,
                "revision": 0, "emulators": {}}
    out = json.loads(json.dumps(manifest))
    for emulator_id, emu in out.get("emulators", {}).items():
        cores = emu.get("cores") if emulator_id == "retroarch" else {"_": emu}
        for core_id, core in (cores or {}).items():
            for bundle_kind, bundle in (core.get("bundles") or {}).items():
                bundle["download_url"] = make_download(account_id, platform_id, game_id,
                                                        emulator_id, core_id, bundle_kind)
    return out


def _read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > 1024 * 1024:
        return {}
    try:
        data = json.loads(handler.rfile.read(length) or b"{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _send_json(handler, code: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _account(handler, data: dict | None = None) -> str:
    from emustart import remote
    if not remote.authorized(handler):
        raise PermissionError("Brak autoryzacji urządzenia.")
    account_id = (data or {}).get("account_id") or ""
    if not account_id:
        import urllib.parse
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
        account_id = (query.get("account_id") or [""])[0]
    return _safe(account_id, "account_id")


def handle(handler, method: str) -> None:
    """Routing /v1/savesync/* i /v1/files/*."""
    import urllib.parse
    path = urllib.parse.urlsplit(handler.path).path
    try:
        if method == "GET":
            if path.startswith("/v1/files/dl/"):
                token = path.rsplit("/", 1)[1]
                item = _token_get(token, "download")
                if not item:
                    return _send_json(handler, 403, {"ok": False, "reason": "Token wygasł."})
                data = Path(item["path"]).read_bytes()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/zip")
                handler.send_header("Content-Length", str(len(data)))
                handler.send_header("Cache-Control", "no-store")
                handler.end_headers()
                handler.wfile.write(data)
                return
            if path.startswith("/v1/savesync/"):
                parts = path.strip("/").split("/")
                if len(parts) != 5 or parts[-1] not in ("manifest", "status"):
                    return _send_json(handler, 404, {"ok": False, "reason": "Nieznany endpoint."})
                account_id = _account(handler)
                manifest = manifest_for_client(account_id, parts[2], parts[3])
                return _send_json(handler, 200, manifest)
            return _send_json(handler, 404, {"ok": False, "reason": "Nieznany endpoint."})

        if method == "POST":
            data = _read_json(handler)
            if path == "/v1/savesync/upload/init":
                account_id = _account(handler, data)
                result = init_upload(account_id, data.get("device_id", ""),
                                     data.get("platform_id", ""), data.get("game_id", ""),
                                     data.get("emulator_id", ""), data.get("core_id", ""),
                                     data.get("bundle_kind", ""), data.get("base_revision", -1),
                                     data.get("sha256", ""), data.get("size", 0))
                return _send_json(handler, 409 if result.get("conflict") else 200, result)
            if path == "/v1/savesync/upload/commit":
                account_id = _account(handler, data)
                result = commit_upload(account_id, data.get("device_id", ""),
                                       data.get("upload_id", ""), data.get("base_revision", -1),
                                       data.get("sha256", ""), data.get("size", 0))
                return _send_json(handler, 409 if result.get("conflict") else 200, result)
            if path.endswith("/resolve"):
                parts = path.strip("/").split("/")
                if len(parts) != 5:
                    return _send_json(handler, 404, {"ok": False, "reason": "Nieznany endpoint."})
                account_id = _account(handler, data)
                choice = data.get("choice")
                if choice == "remote":
                    with _lock:
                        upload = _uploads.pop(data.get("upload_id", ""), None)
                    return _send_json(handler, 200, {"ok": True, "resolved": "remote",
                                                     "removed_upload": bool(upload)})
                if choice == "local":
                    result = commit_upload(account_id, data.get("device_id", ""),
                                           data.get("upload_id", ""), data.get("base_revision", -1),
                                           data.get("sha256", ""), data.get("size", 0), force=True)
                    return _send_json(handler, 200 if result.get("ok") else 400, result)
                return _send_json(handler, 400, {"ok": False, "reason": "Nieznany wybór."})
            return _send_json(handler, 404, {"ok": False, "reason": "Nieznany endpoint."})

        if method == "PUT":
            if path.startswith("/v1/files/ul/"):
                token = path.rsplit("/", 1)[1]
                length = int(handler.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BUNDLE:
                    return _send_json(handler, 400, {"ok": False, "reason": "Nieprawidłowy rozmiar."})
                result = store_upload(token, handler.rfile.read(length))
                return _send_json(handler, 200 if result.get("ok") else 400, result)
            return _send_json(handler, 404, {"ok": False, "reason": "Nieznany endpoint."})

        return _send_json(handler, 405, {"ok": False, "reason": "Nieobsługiwana metoda."})
    except PermissionError as ex:
        return _send_json(handler, 403, {"ok": False, "reason": str(ex)})
    except ValueError as ex:
        return _send_json(handler, 400, {"ok": False, "reason": str(ex)})
    except Exception:
        log.exception("savesync %s %s", method, path)
        return _send_json(handler, 500, {"ok": False, "reason": "Błąd wewnętrzny serwera."})
