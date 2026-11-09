"""Syntetyczne archiwum depotów GOG (format goggui-depot-archive v1) do testów — kilka KB, bez sieci.

Gra 1001 „Testowa Gra”, build 777:
  depot A (neutralny):  bin/game.exe (2 chunki), data/big.dat (3 chunki), empty.txt (0 B), small/*.txt (SFC),
                        goggame-1001.script (supportData + setIni + setRegistry HKCU), app/dosbox.conf
  depot EN:             goggame-1001.info (playTasks, język en), lang/en.txt
  depot PL:             goggame-1001.info (inna treść, język pl), lang/pl.txt
  DLC 2001 (pliki):     dlc/extra.pak, goggame-2001.info
  DLC 2002 (bez plików)
  zależność MSVC2017:   __redist/MSVC2017/vc_redist.x64.exe
"""

from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path

PID, BUILD, DLC_FILES, DLC_META = "1001", "777", "2001", "2002"


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


class Builder:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "archive.json").write_text(json.dumps({"format": "goggui-depot-archive", "version": 1}))
        self.content: dict = {}          # path → bytes (expected after install, per language variant)

    def chunk(self, plain: bytes) -> dict:
        comp = zlib.compress(plain, 6)
        m = _md5(comp)
        p = self.root / "chunks" / m[:2] / m[2:4] / m
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(comp)
        return {"compressedMd5": m, "compressedSize": len(comp), "md5": _md5(plain), "size": len(plain)}

    def file_item(self, path: str, data: bytes, parts: int = 1, md5: bool = True, flags=None) -> dict:
        n = max(1, parts)
        step = max(1, -(-len(data) // n)) if data else 1
        chunks = [self.chunk(data[i:i + step]) for i in range(0, len(data), step)] if data else []
        it = {"type": "DepotFile", "path": path, "chunks": chunks}
        if md5 and data:
            it["md5"] = _md5(data)
        if flags:
            it["flags"] = flags
        return it

    def manifest(self, kind: str, items: list, sfc_files: list | None = None, sfc_only: list | None = None) -> str:
        """sfc_files — małe pliki w kontenerze, z własnym chunkiem (jak w prawdziwych archiwach GOG Managera);
        sfc_only — małe pliki tylko w kontenerze (bez własnych chunków: instalator tnie kontener)."""
        depot = {"items": list(items)}
        if sfc_files or sfc_only:
            blob, off = b"", 0
            for path, data in sfc_files or []:
                depot["items"].append({"type": "DepotFile", "path": path, "sfcRef": {"offset": off, "size": len(data)},
                                       "chunks": [self.chunk(data)]})
                blob += data
                off += len(data)
            for path, data in sfc_only or []:
                depot["items"].append({"type": "DepotFile", "path": path, "sfcRef": {"offset": off, "size": len(data)},
                                       "md5": _md5(data)})
                blob += data
                off += len(data)
            depot["smallFilesContainer"] = {"chunks": [self.chunk(blob)]}
        man = {"version": 2, "depot": depot}
        raw = json.dumps(man, sort_keys=True).encode()
        mid = _md5(raw)
        p = self.root / "manifests" / kind / mid[:2] / mid[2:4] / f"{mid}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return mid

    def record(self, pid: str, build: str, rec: dict, meta: dict) -> None:
        d = self.root / "products" / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{build}.meta.json").write_text(json.dumps(meta))
        (d / f"{build}.json").write_text(json.dumps({"format": "goggui-depot-archive", "version": 1, **rec}))


GAME_EXE = b"MZ" + bytes(range(256)) * 40          # ~10 KB, 2 chunki
BIG = bytes((i * 7) % 251 for i in range(30000))     # 3 chunki
SCRIPT = {
    "actions": [
        {"install": {"action": "supportData", "arguments": {"type": "file", "source": "{app}\\app\\dosbox.conf",
                                                            "target": "{app}"}}},
        {"install": {"action": "setIni", "arguments": {"filename": "{app}\\settings.ini", "section": "Lang",
                                                        "keyName": "Voice", "keyValue": "PL"}}},
        {"install": {"action": "setRegistry", "arguments": {"root": "HKEY_CURRENT_USER",
                                                             "subkey": "Software\\EmuStartTest\\Game1001",
                                                             "valueName": "Installed To:", "valueData": "{app}\\",
                                                             "valueType": "string"}}},
        {"install": {"action": "savePath", "arguments": {"savePath": "{app}\\saves", "type": "folder"}}},
    ], "productId": PID}


def info(lang: str) -> bytes:
    return json.dumps({"buildId": BUILD, "gameId": PID, "name": "Testowa Gra", "languages": [lang],
                       "clientId": "123", "playTasks": [
                           {"category": "launcher", "isPrimary": True, "path": "Launcher.exe", "type": "FileTask"},
                           {"category": "game", "path": "bin\\game.exe", "arguments": "-lang " + lang,
                            "workingDir": "bin", "type": "FileTask"}]}).encode()


def build_archive(root: Path, state: str = "complete", script: dict | None = None, extra_items: list | None = None) -> dict:
    """Archiwum z jedną grą; zwraca słownik z hashami i oczekiwaną zawartością."""
    b = Builder(root)
    a_items = [
        b.file_item("bin/game.exe", GAME_EXE, parts=2, flags=["executable"]),
        b.file_item("data\\big.dat", BIG, parts=3, md5=False),
        b.file_item("empty.txt", b""),
        b.file_item("goggame-1001.script", json.dumps(script or SCRIPT).encode()),
        b.file_item("app/dosbox.conf", b"[dosbox]\nmemsize=16\n"),
        {"type": "DepotDirectory", "path": "saves"},
        *(extra_items or []),
    ]
    sfc = [(f"small/s{i}.txt", f"maly plik {i}\n".encode()) for i in range(5)]
    sfc_only = [("small/tylko_kontener.txt", b"tylko w kontenerze SFC\n")]
    dep_a = b.manifest("depot", a_items, sfc, sfc_only)
    dep_en = b.manifest("depot", [b.file_item("goggame-1001.info", info("en-US")), b.file_item("lang/en.txt", b"hello")])
    dep_pl = b.manifest("depot", [b.file_item("goggame-1001.info", info("pl-PL")), b.file_item("lang/pl.txt", b"czesc")])
    dep_dlc = b.manifest("depot", [b.file_item("dlc/extra.pak", b"DLC" * 500),
                                   b.file_item("goggame-2001.info", json.dumps({"gameId": DLC_FILES, "rootGameId": PID}).encode())])
    dep_redist = b.manifest("dep", [b.file_item("__redist/MSVC2017/vc_redist.x64.exe", b"MZredist" * 100)])
    depots = [
        {"manifest": dep_a, "productId": PID, "languages": ["*"], "size": 1, "compressedSize": 1},
        {"manifest": dep_en, "productId": PID, "languages": ["en-US"], "size": 1, "compressedSize": 1},
        {"manifest": dep_pl, "productId": PID, "languages": ["pl-PL"], "size": 1, "compressedSize": 1},
        {"manifest": dep_dlc, "productId": DLC_FILES, "languages": ["*"], "size": 1, "compressedSize": 1},
    ]
    rec = {"product_id": PID, "title": "Testowa Gra", "slug": "testowa_gra", "build_id": BUILD, "version_name": "1.0",
           "os": "windows", "generation": 2, "install_directory": "Testowa Gra",
           "products": [{"id": PID, "role": "base", "name": "Testowa Gra", "has_files": True},
                        {"id": DLC_FILES, "role": "dlc", "name": "Dodatek z plikami", "has_files": True},
                        {"id": DLC_META, "role": "dlc", "name": "Dodatek bez plików", "has_files": False}],
           "depots": depots, "dependencies": [{"id": "MSVC2017", "manifest": dep_redist}],
           "scope": {"languages": ["en", "pl"], "dlcs": [DLC_FILES, DLC_META]},
           "state": state, "created": "2026-11-08T00:00:00", "verified": "2026-11-08T00:01:00",
           "size": len(GAME_EXE) + len(BIG), "compressed_size": 1}
    meta = {"version": 2, "baseProductId": PID, "buildId": BUILD, "installDirectory": "Testowa Gra",
            "depots": depots, "products": [{"productId": PID, "name": "Testowa Gra"}], "dependencies": ["MSVC2017"],
            "clientId": "48602034575163689", "clientSecret": "SEKRET-NIE-WYSYLAC"}
    b.record(PID, BUILD, rec, meta)
    return {"builder": b, "depots": {"a": dep_a, "en": dep_en, "pl": dep_pl, "dlc": dep_dlc, "redist": dep_redist}}


def all_chunk_files(root: Path) -> list:
    return sorted(p for p in (Path(root) / "chunks").rglob("*") if p.is_file())
