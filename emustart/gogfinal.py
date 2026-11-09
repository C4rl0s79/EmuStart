"""gogfinal — finalizacja i uruchamianie gier GOG zainstalowanych z serwera (emustart/goginstall.py).

Instalacja z depotów omija instalator GOG, więc kroki z `goggame-<id>.script` wykonujemy sami — wyłącznie
znane, sprawdzone akcje, nigdy programy z manifestu:
  • supportData — kopia pliku/folderu wewnątrz katalogu gry (np. dosbox.conf do katalogu głównego),
  • setIni — wpis w pliku INI w katalogu gry albo w Dokumentach,
  • setRegistry — wartość pod HKEY_CURRENT_USER\\Software\\… (bez uprawnień administratora);
    HKEY_LOCAL_MACHINE wymaga administratora: zostaje jako krok „do zatwierdzenia” (UAC), gra nie jest
    „zainstalowana”, dopóki go nie wykonasz,
  • savePath — folder zapisów (zapamiętany: odinstalowanie go nie rusza).
Inne akcje są pomijane z wpisem w logu. Biblioteki systemowe z zależności (__redist: Visual C++, DirectX)
instaluje się osobnym, jawnym krokiem z UAC (`run_runtimes`) — pliki zależności są w katalogu gry i
weryfikowane jak pozostałe.

Uruchamianie: playTask z `goggame-<id>.info` (zadanie gry przed launcherem zewnętrznym), tylko plik .exe
wewnątrz katalogu gry. Śledzenie: proces gry albo dowolny proces z plikiem wewnątrz katalogu gry (launcher,
który uruchamia grę i kończy się, nie kończy sesji).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from emustart.gogarchive import UnsafePath, safe_join

log = logging.getLogger("emustart.gogfinal")


class FinalizeError(Exception):
    pass


def user_documents() -> Path:
    """Folder Dokumenty użytkownika (także przekierowany, np. do OneDrive)."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v, _ = winreg.QueryValueEx(k, "Personal")
            if v:
                return Path(os.path.expandvars(v))
        except OSError:
            pass
    return Path.home() / "Documents"


def _inside(p: Path, *roots: Path) -> bool:
    rp = Path(os.path.abspath(p))
    for r in roots:
        try:
            rp.relative_to(Path(os.path.abspath(r)))
            return True
        except ValueError:
            continue
    return False


def ini_set(path: Path, section: str, key: str, value: str) -> None:
    """Jeden klucz w pliku INI; pozostałe linie i końce linii bez zmian (jak setIni instalatora GOG)."""
    raw = path.read_bytes().decode("latin-1") if path.exists() else ""
    nl = "\r\n" if "\r\n" in raw or not raw else "\n"
    lines = raw.splitlines()
    hdr = f"[{section}]".lower()
    start = next((i for i, ln in enumerate(lines) if ln.strip().lower() == hdr), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", f"{key}={value}"]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        hit = next((i for i in range(start + 1, end) if lines[i].split("=", 1)[0].strip().lower() == key.lower()), None)
        if hit is not None:
            lines[hit] = f"{key}={value}"
        else:
            ins = end
            while ins > start + 1 and not lines[ins - 1].strip():
                ins -= 1
            lines.insert(ins, f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((nl.join(lines) + nl).encode("latin-1", errors="replace"))


_REG_TYPES = {"string", "dword", "binary", "expandstring"}
_SUBKEY = re.compile(r"^Software\\[^\x00-\x1f]{1,240}$", re.I)


def registry_action(args: dict, app: Path) -> dict:
    """Sprawdzona akcja setRegistry → {"root": "HKCU"|"HKLM", "subkey", "name", "type", "data"} albo
    FinalizeError. Tylko gałąź Software, znane typy, {app} = katalog gry."""
    root = str(args.get("root") or "").upper()
    hive = {"HKEY_CURRENT_USER": "HKCU", "HKCU": "HKCU", "HKEY_LOCAL_MACHINE": "HKLM", "HKLM": "HKLM"}.get(root)
    if not hive:
        raise FinalizeError(f"setRegistry: nieobsługiwana gałąź {root!r}")
    sub = str(args.get("subkey") or "").replace("/", "\\").strip("\\")
    if not _SUBKEY.match(sub) or "\\..\\" in f"\\{sub}\\":
        raise FinalizeError(f"setRegistry: klucz poza Software\\…: {sub!r}")
    typ = str(args.get("valueType") or "string").lower()
    if typ not in _REG_TYPES:
        raise FinalizeError(f"setRegistry: nieobsługiwany typ {typ!r}")
    name = str(args.get("valueName") or "")
    if len(name) > 255 or any(ord(c) < 32 for c in name):
        raise FinalizeError("setRegistry: zła nazwa wartości")
    data = str(args.get("valueData") or "").replace("{app}", str(app))
    if "{" in data and "}" in data:
        raise FinalizeError(f"setRegistry: nieznany symbol w wartości {data!r}")
    if typ == "dword":
        data = str(_dword(data))
    elif typ == "binary":
        _binary(data)
    return {"root": hive, "subkey": sub, "name": name, "type": typ, "data": data}


def _dword(data: str) -> int:
    """Liczba DWORD ze skryptu: dziesiętnie (także z zerami na początku, np. „0001”) albo 0x…; inaczej błąd
    finalizacji (nie wyjątek w połowie zapisu rejestru)."""
    s = (data or "").strip() or "0"
    try:
        v = int(s[2:], 16) if s.lower().startswith("0x") else int(s, 10)
    except ValueError:
        raise FinalizeError(f"setRegistry: zła wartość dword {data!r}")
    if not 0 <= v <= 0xFFFFFFFF:
        raise FinalizeError(f"setRegistry: wartość dword poza zakresem {data!r}")
    return v


def _binary(data: str) -> bytes:
    import base64
    import binascii
    try:
        return base64.b64decode(data or "", validate=True)
    except (binascii.Error, ValueError):
        raise FinalizeError(f"setRegistry: zła wartość binary {data[:40]!r}")


def _reg_value(a: dict):
    import winreg
    if a["type"] == "dword":
        return winreg.REG_DWORD, _dword(a["data"])
    if a["type"] == "binary":
        return winreg.REG_BINARY, _binary(a["data"])
    if a["type"] == "expandstring":
        return winreg.REG_EXPAND_SZ, a["data"]
    return winreg.REG_SZ, a["data"]


def set_hkcu(a: dict) -> None:
    import winreg
    typ, val = _reg_value(a)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, a["subkey"], 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, a["name"], 0, typ, val)


def remove_hkcu(actions: list) -> int:
    """Odinstalowanie: wartości HKCU ustawione przez finalizację (tylko te; klucz znika, gdy jest pusty)."""
    if os.name != "nt":
        return 0
    import winreg
    n = 0
    for a in actions:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, a["subkey"], 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
                winreg.DeleteValue(k, a["name"])
                n += 1
                empty = winreg.QueryInfoKey(k)[0] == 0 and winreg.QueryInfoKey(k)[1] == 0
            if empty:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, a["subkey"])
        except OSError:
            continue
    return n


def windows_64() -> bool:
    """64-bitowy Windows: HKLM\\Software ma osobny widok dla programów 32-bitowych (WOW6432Node)."""
    import platform
    return os.name == "nt" and (platform.machine().endswith("64") or "PROCESSOR_ARCHITEW6432" in os.environ)


def _views(subkey: str, wow64: bool) -> list:
    """Klucze do zapisu przez 64-bitowy regedit: widok natywny i — na 64-bitowym Windows — widok 32-bitowy
    (Software\\WOW6432Node\\…), bo większość gier GOG to programy 32-bitowe i czyta właśnie ten."""
    out = [subkey]
    if wow64:
        rest = subkey.split("\\", 1)[1]
        if not rest.lower().startswith("wow6432node\\"):
            out.append(f"Software\\WOW6432Node\\{rest}")
    return out


def reg_file(actions: list, wow64: bool | None = None) -> str:
    """Plik .reg dla kroków HKLM (wykonywany za zgodą użytkownika przez regedit z UAC)."""
    wow64 = windows_64() if wow64 is None else wow64
    out = ["Windows Registry Editor Version 5.00", ""]
    for a in actions:
        name = "@" if not a["name"] else '"' + a["name"].replace("\\", "\\\\").replace('"', '\\"') + '"'
        if a["type"] == "dword":
            line = f"{name}=dword:{_dword(a['data']):08x}"
        elif a["type"] == "binary":
            line = f"{name}=hex:" + ",".join(f"{b:02x}" for b in _binary(a["data"]))
        elif a["type"] == "expandstring":           # REG_EXPAND_SZ: w .reg tylko jako hex(2) (UTF-16LE z \0)
            line = f"{name}=hex(2):" + ",".join(f"{b:02x}" for b in (a["data"] + "\0").encode("utf-16-le"))
        else:
            line = f'{name}="' + a["data"].replace("\\", "\\\\").replace('"', '\\"') + '"'
        for sub in _views(a["subkey"], wow64):
            out += [f"[HKEY_LOCAL_MACHINE\\{sub}]", line, ""]
    return "\r\n".join(out)


def read_script(app: Path, pid: str) -> dict | None:
    p = app / f"goggame-{pid}.script"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except ValueError as ex:
        raise FinalizeError(f"goggame-{pid}.script: zły JSON ({ex})")


def apply_script(app: Path, pid: str, hkcu=None, docs: Path | None = None) -> dict:
    """Akcje instalacyjne `goggame-<pid>.script`. Zwraca {"done", "admin" (kroki HKLM), "skipped", "saves",
    "created" (pliki utworzone kopią — do odinstalowania)}; błąd akcji, która musi się udać → FinalizeError."""
    res = {"done": [], "admin": [], "skipped": [], "saves": [], "created": [], "hkcu": []}
    hkcu = hkcu or set_hkcu
    data = read_script(app, pid)
    if not data:
        return res
    docs_dir = docs or user_documents()

    def resolve(s: str) -> Path:
        s = str(s or "").replace("{app}", str(app)).replace("{supportDir}", str(app)).replace("{userdocs}", str(docs_dir))
        if "{" in s:
            raise FinalizeError(f"nieznany symbol w ścieżce {s!r}")
        return Path(s.replace("\\", os.sep).replace("/", os.sep))

    for act in data.get("actions") or []:
        ins = (act or {}).get("install") or {}
        kind, args = ins.get("action"), ins.get("arguments") or {}
        if kind == "supportData":
            src, dst = resolve(args.get("source")), resolve(args.get("target"))
            if not (_inside(src, app) and _inside(dst, app)):
                raise FinalizeError(f"supportData poza katalogiem gry: {args.get('source')} → {args.get('target')}")
            typ = str(args.get("type") or "").lower()
            if typ == "folder" and src.is_dir():
                for f in src.rglob("*"):
                    if f.is_file():
                        t = dst / f.relative_to(src)
                        t.parent.mkdir(parents=True, exist_ok=True)
                        if not t.exists():
                            res["created"].append(str(t))
                        shutil.copy2(f, t)
            elif typ == "file" and src.is_file():
                t = (dst / src.name) if (dst.is_dir() or not dst.suffix) else dst
                t.parent.mkdir(parents=True, exist_ok=True)
                if not t.exists():
                    res["created"].append(str(t))
                shutil.copy2(src, t)
            else:
                raise FinalizeError(f"supportData: brak źródła {args.get('source')}")
            res["done"].append(f"supportData {src.name}")
        elif kind == "setIni":
            fn = resolve(args.get("filename"))
            if not _inside(fn, app, docs_dir):
                raise FinalizeError(f"setIni poza katalogiem gry i Dokumentami: {args.get('filename')}")
            if not fn.exists() and _inside(fn, app):
                res["created"].append(str(fn))       # plik w katalogu gry utworzony przez instalację
            ini_set(fn, str(args.get("section") or ""), str(args.get("keyName") or ""), str(args.get("keyValue") or ""))
            res["done"].append(f"setIni {fn.name} [{args.get('section')}] {args.get('keyName')}")
        elif kind == "setRegistry":
            a = registry_action(args, app)
            if a["root"] == "HKCU":
                hkcu(a)
                res["hkcu"].append(a)
                res["done"].append(f"rejestr HKCU\\{a['subkey']} {a['name']}")
            else:
                res["admin"].append(a)
        elif kind == "savePath":
            try:
                res["saves"].append(str(resolve(args.get("savePath"))))
            except FinalizeError:
                res["skipped"].append("savePath z nieznanym symbolem")
        else:
            res["skipped"].append(str(kind))
            log.info("GOG %s: pomijam akcję %s", pid, kind)
    return res


def run_admin_registry(actions: list, work: Path, elevate=None) -> bool:
    """Kroki HKLM: plik .reg + regedit /s z prośbą o uprawnienia administratora (UAC). True = wykonano
    i wartości są w rejestrze. Bez zgody użytkownika (anulowany UAC) — False."""
    work.mkdir(parents=True, exist_ok=True)
    reg = work / "hklm.reg"
    reg.write_text(reg_file(actions), encoding="utf-16")
    regedit = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "regedit.exe"
    ok = (elevate or _shell_runas)(str(regedit), f'/s "{reg}"')
    if not ok:
        return False
    return all(_hklm_has(a) for a in actions)


def _hklm_has(a: dict) -> bool:
    """Wartość jest w każdym widoku, który gra może czytać (na 64-bitowym Windows: w obu)."""
    try:
        import winreg
    except ImportError:
        return False
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY) if windows_64() else (0,)
    for view in views:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, a["subkey"], 0, winreg.KEY_READ | view) as k:
                winreg.QueryValueEx(k, a["name"])
        except OSError:
            return False
    return True


def _shell_runas(exe: str, params: str, cwd: str | None = None) -> bool:
    """Program z UAC (ShellExecuteEx „runas”), czeka na koniec; True = kod 0."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    sei = SEI(cbSize=ctypes.sizeof(SEI), fMask=0x40, lpVerb="runas", lpFile=exe, lpParameters=params,
              lpDirectory=cwd, nShow=0)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return False                                   # odmowa w oknie UAC
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    k32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    k32.CloseHandle(sei.hProcess)
    return code.value == 0


# biblioteki systemowe z zależności GOG — znane instalatory i ich ciche parametry; inne pliki nie są uruchamiane
RUNTIMES = {
    re.compile(r"^MSVC20(05|08)(_x64)?$", re.I): ("vcredist_x*.exe", "/q"),
    re.compile(r"^MSVC201[0-3](_x64)?$", re.I): ("vcredist_x*.exe", "/q /norestart"),
    re.compile(r"^MSVC20(1[5-9]|2\d)(_x64)?$", re.I): ("vc_redist.x*.exe", "/install /quiet /norestart"),
    re.compile(r"^DirectX$", re.I): ("DXSETUP.exe", "/silent"),
}


def runtime_steps(app: Path, dep_ids: list) -> list:
    """[{"id", "exe", "args"}] dla zależności, które są znanymi bibliotekami systemowymi (plik w __redist)."""
    out = []
    for did in dep_ids:
        for pat, (glob, args) in RUNTIMES.items():
            if not pat.match(did):
                continue
            base = app / "__redist" / did
            hits = sorted(base.glob(glob)) if base.is_dir() else []
            for exe in hits[:1]:
                try:
                    safe_join(app, exe.relative_to(app).as_posix())
                except (UnsafePath, ValueError):
                    continue
                out.append({"id": did, "exe": str(exe), "args": args})
    return out


def run_runtimes(steps: list, elevate=None) -> dict:
    """Instalatory bibliotek — jeden po drugim, każdy z UAC (zgoda użytkownika). {id: ok}."""
    res = {}
    for s in steps:
        ok = (elevate or _shell_runas)(s["exe"], s["args"], str(Path(s["exe"]).parent))
        # kod 1638 (nowsza wersja już jest) / 3010 (wymaga restartu) _shell_runas traktuje jako błąd — log
        res[s["id"]] = bool(ok)
        log.info("GOG: biblioteka %s: %s", s["id"], "OK" if ok else "nie zainstalowano")
    return res


# ── uruchamianie ────────────────────────────────────────────────────────────────

def pick_play_task(tasks: list):
    """Zadanie gry przed launcherem zewnętrznym (np. REDprelauncher Wiedźmina 3 wymaga instalatora GOG):
    główne zadanie gry → dowolne zadanie gry → główne zadanie → pierwsze z plikiem."""
    fts = [t for t in tasks or [] if t.get("path") and (t.get("type") or "FileTask") == "FileTask"]
    game = [t for t in fts if (t.get("category") or "").lower() == "game"]
    return (next((t for t in game if t.get("isPrimary")), None) or (game[0] if game else None)
            or next((t for t in fts if t.get("isPrimary")), None) or (fts[0] if fts else None))


def _info(app: Path, pid: str) -> dict:
    try:
        return json.loads((app / f"goggame-{pid}.info").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as ex:
        raise FinalizeError(f"brak opisu uruchamiania (goggame-{pid}.info): {ex}")


def play_tasks(app: Path, pid: str) -> list:
    """Zadania uruchamiania gry (np. launcher i obie części Wing Commandera) do wyboru w interfejsie:
    [{"index", "name", "category", "default"}] — tylko pliki .exe wewnątrz katalogu gry."""
    data = _info(app, pid)
    tasks = [t for t in data.get("playTasks") or [] if t.get("path") and (t.get("type") or "FileTask") == "FileTask"]
    best = pick_play_task(tasks)
    out = []
    for i, t in enumerate(tasks):
        try:
            exe = safe_join(app, t["path"])
        except UnsafePath:
            continue
        if exe.suffix.lower() == ".exe":
            out.append({"index": i, "name": str(t.get("name") or exe.stem), "category": str(t.get("category") or ""),
                        "default": t is best})
    return out


def launch_spec(app: Path, pid: str, task: int | None = None) -> dict:
    """{"exe", "args", "cwd", "name"} z goggame-<pid>.info — ścieżki tylko wewnątrz katalogu gry, tylko .exe.
    `task` — numer zadania z play_tasks (domyślnie zadanie gry przed launcherem)."""
    data = _info(app, pid)
    tasks = [t for t in data.get("playTasks") or [] if t.get("path") and (t.get("type") or "FileTask") == "FileTask"]
    if task is not None and task >= 0:
        if task >= len(tasks):
            raise FinalizeError("nie ma takiego zadania uruchamiania")
        t = tasks[task]
    else:
        t = pick_play_task(tasks)
    if not t:
        raise FinalizeError("goggame-*.info nie ma zadania uruchamiania gry")
    try:
        exe = safe_join(app, t["path"])
        cwd = safe_join(app, t["workingDir"]) if t.get("workingDir") else exe.parent
    except UnsafePath as ex:
        raise FinalizeError(f"ścieżka uruchamiania poza katalogiem gry: {ex}")
    if exe.suffix.lower() != ".exe":
        raise FinalizeError(f"plik uruchamiania nie jest programem .exe: {t['path']}")
    if not exe.is_file():
        raise FinalizeError(f"brak pliku gry: {t['path']}")
    args = str(t.get("arguments") or "")
    if any(ord(c) < 32 for c in args):
        raise FinalizeError("argumenty uruchamiania zawierają znaki sterujące")
    return {"exe": str(exe), "args": args, "cwd": str(cwd if cwd.is_dir() else exe.parent),
            "name": str(t.get("name") or data.get("name") or "")}


def start(spec: dict) -> subprocess.Popen:
    """Gra jako osobny proces (bez powłoki): linia poleceń = "exe" + argumenty z playTask, jak GOG Galaxy."""
    cmd = f'"{spec["exe"]}" {spec["args"]}'.strip()
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(cmd if os.name == "nt" else [spec["exe"], *spec["args"].split()], cwd=spec["cwd"],
                            close_fds=True, creationflags=flags)


def processes_under(root: Path) -> list:
    """PID-y procesów, których plik programu leży w `root` (Windows; poza nim — pusta lista)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    if snap in (0, wintypes.HANDLE(-1).value):
        return []
    want = os.path.normcase(os.path.abspath(root)) + os.sep
    out = []
    try:
        pe = PE(dwSize=ctypes.sizeof(PE))
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            h = k32.OpenProcess(0x1000, False, pe.th32ProcessID)     # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                buf = ctypes.create_unicode_buffer(1024)
                n = wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    if os.path.normcase(buf.value).startswith(want):
                        out.append(int(pe.th32ProcessID))
                k32.CloseHandle(h)
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return out
