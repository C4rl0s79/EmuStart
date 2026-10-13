# -*- mode: python ; coding: utf-8 -*-
# Budowa:  pyinstaller --noconfirm emustart.spec  (albo build.ps1)
# Wynik: dwa osobne programy, każdy z własnym _internal/:
#   dist/EmuStart/       EmuStart.exe       — EmuStart na PC (main.py)
#   dist/EmuStartServer/ EmuStartServer.exe — EmuStart Server (server_main.py)
# Wersja katalogowa, nie
# onefile: jednoplikowy exe (rozpakowujący się do %TEMP%) Defender oznaczał
# heurystyką jako Trojan:Win32/Bearfoos.A!ml.
# Portable: config.json / data/ / cache/ / logs/ powstają OBOK exe (paths.app_dir),
# a web/ i assets/ leżą w _internal/ (sys._MEIPASS).

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

import re
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo)

# Metadane wersji w exe (Właściwości → Szczegóły). Exe bez nich heurystyki
# antywirusów oceniają gorzej — to jeden ze sposobów ograniczania fałszywych alarmów.
VERSION = re.search(r'__version__ = "(.+?)"', open("emustart/__init__.py", encoding="utf-8").read()).group(1)
_v = tuple(int(x) for x in VERSION.split(".")) + (0,)


def version_info(name: str, desc: str):
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=_v, prodvers=_v),
        kids=[
            StringFileInfo([StringTable("041504B0", [
                StringStruct("CompanyName", "C4rl0s79"),
                StringStruct("FileDescription", desc),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", name),
                StringStruct("LegalCopyright", "C4rl0s79"),
                StringStruct("OriginalFilename", f"{name}.exe"),
                StringStruct("ProductName", name),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0415, 1200])]),
        ],
    )


# wbudowane logo platform — jeden plik (tools/pack_assets.py) zamiast 40 PNG; ikona jest już w exe
import sys as _sys
_sys.path.insert(0, ".")
from tools.pack_assets import build as _pack_logos
from pathlib import Path as _P
_logos = _P("build") / "systems.media"
_pack_logos(_P("assets") / "systems", _logos)
datas = [("web", "web"), (str(_logos), "assets")]
binaries = []
hiddenimports = [
    "webview.platforms.edgechromium",   # backend WebView2 (Win10/11)
    "webview.platforms.winforms",
]

# pywebview + most .NET (pythonnet/clr_loader) — DLL-e WebView2 z webview/lib
for pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# metadane wersji — EmuStart zapisuje w logu wersje pywebview/pythonnet (diagnostyka okna)
for dist in ("pywebview", "pythonnet", "clr_loader"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

# własne moduły jawnie — PyInstaller potrafi przeoczyć importy leniwe
hiddenimports += collect_submodules("emustart")
# kod QR parowania (emustart/qr.py, import leniwy) — w paczce programu, żeby komputer
# z serwerem nie musiał mieć Pythona ani biblioteki
try:
    hiddenimports += collect_submodules("segno")
except Exception:
    pass
# certyfikat portu szyfrowanego serwera (emustart/tls.py, import leniwy)
hiddenimports += ["cryptography.x509", "cryptography.x509.oid", "cryptography.hazmat.primitives.hashes",
                  "cryptography.hazmat.primitives.serialization",
                  "cryptography.hazmat.primitives.asymmetric.ec"]



def app(script: str, name: str, desc: str):
    a = Analysis(
        [script],
        pathex=["."],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=["tkinter", "test", "unittest", "pytest"],
    )
    pyz = PYZ(a.pure, a.zipped_data)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        strip=False,
        upx=False,                 # UPX psuje część DLL WebView2
        console=False,
        icon="assets/icon.ico",
        version=version_info(name, desc),
    )
    COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)


app("main.py", "EmuStart", "EmuStart — frontend emulatorów")
app("server_main.py", "EmuStartServer", "EmuStart Server — biblioteka gier dla EmuStart")
