# -*- mode: python ; coding: utf-8 -*-
# Budowa:  pyinstaller --noconfirm emustart.spec  (albo build.ps1)
# Wynik: dist/EmuStart/ (EmuStart.exe + _internal/). Wersja katalogowa, nie
# onefile: jednoplikowy exe (rozpakowujący się do %TEMP%) Defender oznaczał
# heurystyką jako Trojan:Win32/Bearfoos.A!ml.
# Portable: config.json / data/ / cache/ / logs/ powstają OBOK exe (paths.app_dir),
# a web/ i assets/ leżą w _internal/ (sys._MEIPASS).

from PyInstaller.utils.hooks import collect_all, collect_submodules

import re
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo)

# Metadane wersji w exe (Właściwości → Szczegóły). Exe bez nich heurystyki
# antywirusów oceniają gorzej — to jeden ze sposobów ograniczania fałszywych alarmów.
VERSION = re.search(r'__version__ = "(.+?)"', open("emustart/__init__.py", encoding="utf-8").read()).group(1)
_v = tuple(int(x) for x in VERSION.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v, prodvers=_v),
    kids=[
        StringFileInfo([StringTable("041504B0", [
            StringStruct("CompanyName", "C4rl0s79"),
            StringStruct("FileDescription", "EmuStart — frontend emulatorów"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "EmuStart"),
            StringStruct("LegalCopyright", "C4rl0s79"),
            StringStruct("OriginalFilename", "EmuStart.exe"),
            StringStruct("ProductName", "EmuStart"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0415, 1200])]),
    ],
)

datas = [("web", "web"), ("assets", "assets")]
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

# własne moduły jawnie — PyInstaller potrafi przeoczyć importy leniwe
hiddenimports += collect_submodules("emustart")

a = Analysis(
    ["main.py"],
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
    name="EmuStart",
    debug=False,
    strip=False,
    upx=False,                 # UPX psuje część DLL WebView2
    console=False,
    icon="assets/icon.ico",
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="EmuStart")
