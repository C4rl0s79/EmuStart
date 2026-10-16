"""savesync_client — klient synchronizacji zapisów z serwerem EmuStart.

Klient nie zna ścieżek serwera. Pobiera manifest, porównuje revision i SHA-256,
pobiera lub wysyła bundle przez krótkotrwałe URL-e serwera.

Dla RetroArch dane są zawsze per rdzeń: Snes9x i bsnes mają osobne bundle.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import zipfile
from pathlib import Path

from emustart import netsrc, profiles

log = logging.getLogger("emustart.savesync_client")


class SyncError(OSError):
    pass


class SyncConflict(SyncError):
    def __init__(self, current_revision: int):
        super().__init__(f"Konflikt zapisów: serwer ma revision {current_revision}.")
        self.current_revision = current_revision


def _state_path(pid: int) -> Path:
    return profiles.LOCAL / str(pid) / "_savesync.json"


def _state_load(pid: int) -> dict:
    try:
        data = json.loads(_state_path(pid).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _state_save(pid: int, data: dict) -> None:
    path = _state_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _account(cfg: dict) -> str:
    account = profiles.account_of_device(cfg)
    if not account:
        raise SyncError("To urządzenie nie jest zalogowane na konto serwera.")
    return account


def _device_id(cfg: dict) -> str:
    key = str(cfg.get("server_key") or "")
    return key[len(netsrc.DEV):] if key.startswith(netsrc.DEV) else "local"


def _request(cfg: dict, method: str, path: str, body: dict | None = None,
             raw: bytes | None = None, timeout: float = 30):
    ep = netsrc.endpoint(cfg)
    if not ep:
        raise SyncError("Serwer EmuStart nie jest ustawiony.")
    host, port, key = ep
    conn = netsrc.conn(host, port, timeout)
    headers = netsrc.auth_headers(key)
    payload = b""
    if raw is not None:
        payload = raw
    elif body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if payload:
        headers["Content-Length"] = str(len(payload))
    try:
        conn.request(method, path, body=payload if payload else None, headers=headers)
        response = conn.getresponse()
        data = response.read()
        return response.status, data
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bundle_kinds(adapter) -> list[str]:
    family = getattr(adapter, "family", "")
    if family == "retroarch":
        return ["saves", "states"]
    if family in ("duckstation", "pcsx2", "armsx2", "nethersx2"):
        return ["memcards", "states"]
    return []


def _manifest_url(game: dict) -> str:
    return f"/v1/savesync/{game['es']}/{game['id']}/manifest?account_id={{account}}"


def _manifest(cfg: dict, pid: int, game: dict) -> dict:
    account = _account(cfg)
    status, data = _request(cfg, "GET", _manifest_url(game).format(account=account))
    if status == 401:
        raise SyncError("Serwer odrzucił token urządzenia — zaloguj się ponownie.")
    if status != 200:
        raise SyncError(f"Serwer zwrócił {status} dla manifestu zapisów.")
    try:
        manifest = json.loads(data)
    except ValueError as ex:
        raise SyncError("Serwer zwrócił uszkodzony manifest.") from ex
    state = _state_load(pid)
    state.setdefault("manifests", {})[str(game["id"])] = {
        "revision": manifest.get("revision", 0),
        "checked_at": time.time(),
    }
    _state_save(pid, state)
    return manifest


def _bundle_for(manifest: dict, adapter, core_id: str, kind: str) -> dict | None:
    emulator_id = getattr(adapter, "family", "")
    if emulator_id == "retroarch":
        node = (manifest.get("emulators", {}).get("retroarch", {}).get("cores", {})
                .get(core_id or "_", {}))
    else:
        node = manifest.get("emulators", {}).get(emulator_id, {})
    return (node.get("bundles") or {}).get(kind)


def _download(cfg: dict, pid: int, game: dict, emulator_id: str, core_id: str,
              kind: str, bundle: dict) -> None:
    url = bundle.get("download_url") or ""
    if not url:
        raise SyncError("Serwer nie podał adresu pobierania bundle.")
    status, data = _request(cfg, "GET", url)
    if status != 200:
        raise SyncError(f"Pobieranie bundle zakończone kodem {status}.")
    if _sha(data) != bundle.get("sha256"):
        raise SyncError("Pobrany bundle ma zły SHA-256.")
    profile_root = profiles.LOCAL / str(pid)
    profile_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target = (profile_root / member.filename).resolve()
            if profile_root.resolve() not in target.parents:
                raise SyncError("Bundle zawiera niedozwoloną ścieżkę.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    state = _state_load(pid)
    key = _state_key(game, emulator_id, core_id, kind)
    state.setdefault("bundles", {})[key] = {
        "bundle_id": bundle.get("bundle_id", ""),
        "synced_revision": bundle.get("revision", 0),
        "synced_hash": bundle.get("sha256", ""),
        "local_dirty": False,
        "conflict": False,
    }
    _state_save(pid, state)
    log.info("savesync: pobrano %s/%s/%s r%s", emulator_id, core_id, kind,
             bundle.get("revision"))


def _state_key(game: dict, emulator_id: str, core_id: str, kind: str) -> str:
    return f"{game['id']}|{emulator_id}|{core_id or '_'}|{kind}"


def prepare(cfg: dict, pid: int, game: dict, adapter, core_id: str = "") -> dict:
    """Przed grą: pobiera nowsze zapisy i stany tego rdzenia/emulatora z serwera."""
    kinds = _bundle_kinds(adapter)
    if not kinds:
        return {"ok": True, "downloaded": 0}
    emulator_id = getattr(adapter, "family", "")
    manifest = _manifest(cfg, pid, game)
    state = _state_load(pid)
    downloaded = 0
    for kind in kinds:
        bundle = _bundle_for(manifest, adapter, core_id, kind)
        if not bundle:
            continue
        key = _state_key(game, emulator_id, core_id, kind)
        local = state.get("bundles", {}).get(key, {})
        remote_revision = int(bundle.get("revision") or 0)
        synced_revision = int(local.get("synced_revision") or 0)
        if remote_revision > synced_revision and not local.get("local_dirty"):
            _download(cfg, pid, game, emulator_id, core_id, kind, bundle)
            downloaded += 1
    return {"ok": True, "downloaded": downloaded}


def _changed_files(adapter, since: float) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    family = getattr(adapter, "family", "")
    dirs: list[Path] = []
    for getter in ("save_dirs", "state_dirs"):
        try:
            dirs.extend(getattr(adapter, getter)() or [])
        except Exception:
            log.exception("savesync: %s", getter)
    for directory in dirs:
        if not directory.is_dir():
            continue
        prefix = f"{family}/{directory.name}"
        for file in directory.rglob("*"):
            if not file.is_file() or file.stat().st_mtime < since - 1:
                continue
            rel = file.relative_to(directory).as_posix()
            out.append((file, f"{prefix}/{rel}"))
    return out


def _pack(files: list[tuple[Path, str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in files:
            archive.write(path, arcname)
    return buffer.getvalue()


def publish(cfg: dict, pid: int, game: dict, adapter, core_id: str,
            started_at: float) -> dict:
    """Po grze: pakuje zmienione pliki i wysyła je jako nowy revision."""
    kinds = _bundle_kinds(adapter)
    if not kinds:
        return {"ok": True, "uploaded": 0}
    emulator_id = getattr(adapter, "family", "")
    files = _changed_files(adapter, started_at)
    if not files:
        return {"ok": True, "uploaded": 0}

    by_kind: dict[str, list[tuple[Path, str]]] = {kind: [] for kind in kinds}
    for path, arcname in files:
        lower = arcname.lower()
        if emulator_id == "retroarch":
            kind = "states" if ".state" in Path(arcname).name.lower() else "saves"
        elif ".p2s" in lower or ".state" in lower:
            kind = "states"
        else:
            kind = "memcards"
        if kind in by_kind:
            by_kind[kind].append((path, arcname))

    account = _account(cfg)
    device_id = _device_id(cfg)
    uploaded = 0
    state = _state_load(pid)
    for kind, items in by_kind.items():
        if not items:
            continue
        payload = _pack(items)
        digest = _sha(payload)
        key = _state_key(game, emulator_id, core_id, kind)
        local = state.get("bundles", {}).get(key, {})
        base_revision = int(local.get("synced_revision") or 0)
        status, data = _request(cfg, "POST", "/v1/savesync/upload/init", body={
            "account_id": account,
            "device_id": device_id,
            "platform_id": game["es"],
            "game_id": game["id"],
            "emulator_id": emulator_id,
            "core_id": core_id or "_",
            "bundle_kind": kind,
            "base_revision": base_revision,
            "sha256": digest,
            "size": len(payload),
        })
        if status == 409:
            result = json.loads(data)
            state.setdefault("bundles", {}).setdefault(key, {})["conflict"] = True
            _state_save(pid, state)
            raise SyncConflict(int(result.get("current_revision") or 0))
        if status != 200:
            raise SyncError(f"Serwer odrzucił inicjację uploadu ({status}).")
        init = json.loads(data)
        status, _data = _request(cfg, "PUT", init["upload_url"], raw=payload)
        if status != 200:
            raise SyncError(f"Wysyłanie bundle zakończone kodem {status}.")
        status, data = _request(cfg, "POST", "/v1/savesync/upload/commit", body={
            "account_id": account,
            "device_id": device_id,
            "upload_id": init["upload_id"],
            "base_revision": base_revision,
            "sha256": digest,
            "size": len(payload),
        })
        if status != 200:
            raise SyncError(f"Serwer odrzucił commit uploadu ({status}).")
        result = json.loads(data)
        state.setdefault("bundles", {})[key] = {
            "bundle_id": "",
            "synced_revision": result.get("revision", base_revision + 1),
            "synced_hash": digest,
            "local_dirty": False,
            "conflict": False,
        }
        _state_save(pid, state)
        uploaded += 1
        log.info("savesync: wysłano %s/%s/%s r%s", emulator_id, core_id, kind,
                 result.get("revision"))
    return {"ok": True, "uploaded": uploaded}
