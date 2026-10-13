"""updater — aktualizacja EmuStart i EmuStart Server z wydań na GitHubie.

Dwa programy, każdy z własnym plikiem wydania (i podpisem):
EmuStart (PC) — `EmuStart-<wersja>-win64.zip` (EmuStart/EmuStart.exe),
EmuStart Server — `EmuStart-Server-<wersja>-win64.zip` (EmuStartServer/EmuStartServer.exe).

1. `latest()` — najnowsze wydanie C4rl0s79/EmuStart i plik tego programu.
2. `Job` — pobranie ZIP-a do `update\\` obok exe (z postępem), sprawdzenie skrótu SHA-256
   i **podpisu wydania** (`<zip>.sig`, Ed25519 — emustart/signing.py; bez poprawnego podpisu
   nic nie jest rozpakowywane), sprawdzenie archiwum (`EmuStart\\EmuStart.exe` i
   `_internal`), rozpakowanie do `update\\new`.
3. `Job.apply()` — skrypt PowerShell `update\\apply.ps1` uruchomiony poza procesem
   serwera: czeka na jego zakończenie (i na zwolnienie plików przez okno panelu),
   robi kopię `EmuStart.exe` + `_internal` do `update\\backup`, podmienia je, przy błędzie
   przywraca kopię, a na końcu uruchamia serwer (zadanie Harmonogramu „EmuStart
   Server”, bez niego — exe z tymi samymi parametrami). Przebieg: `logs\\update.log`.

`config.json`, `data`, `cache`, `logs` nie są w ZIP-ie i zostają nietknięte.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from emustart import __version__, paths

log = logging.getLogger("emustart.updater")

REPO = "C4rl0s79/EmuStart"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
UA = f"EmuStart/{__version__}"
TASK = "EmuStart Server"

# program → (nazwa exe i folderu w ZIP-ie, plik wydania)
APPS = {"client": ("EmuStart", re.compile(r"EmuStart-[\d.]+-win64\.zip")),
        "server": ("EmuStartServer", re.compile(r"EmuStart-Server-[\d.]+-win64\.zip"))}


def kind() -> str:
    """Który program się aktualizuje: serwer (EmuStartServer.exe, także dawny EmuStart.exe
    --server — przejście na osobny program) albo EmuStart na PC."""
    if Path(sys.executable).stem.lower() == "emustartserver" or \
            any(a in sys.argv for a in ("--service", "--server", "--panel", "--server-panel")):
        return "server"
    return "client"


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def latest(timeout: float = 15, app: str = "") -> dict:
    """{version, tag, url, size, notes, published, newer} najnowszego wydania (plik programu `app`)."""
    app = app or kind()
    req = urllib.request.Request(API_URL, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.loads(r.read())
    tag = rel.get("tag_name") or ""
    ver = tag.lstrip("v")
    root = APPS[app][0]
    asset = next((a for a in rel.get("assets") or []
                  if APPS[app][1].fullmatch(a.get("name") or "")), None)
    if asset is None and app == "server":
        # wydanie sprzed rozdziału (jeden program z trybem serwera) — dotychczasowy plik
        asset = next((a for a in rel.get("assets") or []
                      if APPS["client"][1].fullmatch(a.get("name") or "")), None)
        root = APPS["client"][0]
    sig = next((a for a in rel.get("assets") or []
                if asset and a.get("name") == asset["name"] + ".sig"), None)
    return {"version": ver, "tag": tag, "current": __version__, "app": app, "root": root,
            "newer": vtuple(ver) > vtuple(__version__),
            "url": asset["browser_download_url"] if asset else "", "size": asset["size"] if asset else 0,
            "sha256": ((asset or {}).get("digest") or "").removeprefix("sha256:").lower(),
            "sig_url": sig["browser_download_url"] if sig else "",
            "notes": (rel.get("body") or "")[:4000], "published": rel.get("published_at") or "",
            "page": rel.get("html_url") or ""}


def update_dir() -> Path:
    return paths.APP / "update"


class Job:
    """Pobranie i przygotowanie aktualizacji w tle; UI odpytuje `status()`."""

    def __init__(self, info: dict):
        self.info = info
        self.phase = "downloading"            # downloading → ready → applying | error
        self.done = 0
        self.total = info.get("size") or 0
        self.error = ""
        self.cancel = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="update")

    def start(self) -> None:
        self.thread.start()

    def status(self) -> dict:
        return {"phase": self.phase, "done": self.done, "total": self.total, "error": self.error,
                "version": self.info.get("version", "")}

    def _run(self) -> None:
        d = update_dir()
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            zp = d / (self.info["url"].rsplit("/", 1)[-1] or "update.zip")
            self._download(self.info["url"], zp)
            self.phase = "extracting"
            new = d / "new"
            _extract(zp, new, self.info.get("root") or APPS[self.info.get("app") or kind()][0])
            zp.unlink(missing_ok=True)
            self.phase = "ready"
            log.info("aktualizacja %s gotowa do podmiany", self.info["version"])
        except Exception as ex:
            log.exception("aktualizacja")
            self.phase, self.error = "error", str(ex) or ex.__class__.__name__

    def _download(self, url: str, dest: Path) -> None:
        if not url:
            raise RuntimeError("W wydaniu nie ma pliku tego programu (…-win64.zip).")
        if not url.startswith(DOWNLOAD_PREFIX):
            raise RuntimeError(f"Nieoczekiwany adres aktualizacji: {url}")
        import hashlib
        h = hashlib.sha256()
        part = dest.with_suffix(".part")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            self.total = int(r.headers.get("Content-Length") or self.total or 0)
            while True:
                if self.cancel.is_set():
                    raise InterruptedError("przerwano")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                self.done += len(chunk)
        if self.total and part.stat().st_size != self.total:
            raise RuntimeError(f"Pobrano {part.stat().st_size} z {self.total} B — spróbuj ponownie.")
        # skrót SHA-256 podany przez GitHub dla pliku wydania — plik dokładnie ten z wydania
        want = self.info.get("sha256") or ""
        if want and h.hexdigest() != want:
            part.unlink(missing_ok=True)
            raise RuntimeError("Pobrany plik ma inny skrót SHA-256 niż plik wydania — aktualizacja przerwana.")
        # podpis wydania (klucz prywatny tylko w CI) — chroni także przed przejęciem konta GitHub
        name = url.rsplit("/", 1)[-1]
        if not _signature_ok(self.info.get("sig_url") or "", name, h.hexdigest()):
            part.unlink(missing_ok=True)
            raise RuntimeError("Wydanie nie ma poprawnego podpisu EmuStart — aktualizacja wstrzymana "
                               "(plik mógł zostać podmieniony).")
        part.replace(dest)

    def apply(self, argv: list, server: bool = True) -> dict:
        """Uruchamia skrypt podmiany (poza tym procesem). Wołający kończy proces.
        server=True — potem start zadania Harmonogramu serwera (gdy jest), False —
        EmuStart z tymi samymi parametrami (okno do grania)."""
        if self.phase != "ready":
            return {"ok": False, "reason": "Aktualizacja nie jest gotowa."}
        if os.name != "nt" or not getattr(sys, "frozen", False):
            return {"ok": False, "reason": "Podmiana działa tylko w EmuStart.exe na Windows."}
        script = write_script(update_dir(), paths.APP, os.getpid(), argv, server=server,
                              exe_name=self.info.get("root") or APPS[self.info.get("app") or kind()][0])
        ulog = paths.APP / "logs" / "update.log"
        before = ulog.stat().st_size if ulog.is_file() else 0
        how = _launch(script, server)
        # proces kończy się dopiero, gdy skrypt potwierdzi start — inaczej zostaje włączony
        # (0.32–0.34: skrypt ginął razem z serwerem, a serwer już się nie podnosił)
        if not _confirmed(ulog, before, f"PID {os.getpid()}"):
            log.error("aktualizacja %s: skrypt podmiany nie wystartował (%s) — serwer działa dalej",
                      self.info["version"], how)
            return {"ok": False, "reason": "Skrypt podmiany nie wystartował — EmuStart działa dalej na obecnej "
                                           "wersji. Szczegóły w logach."}
        self.phase = "applying"
        log.info("aktualizacja %s: podmiana plików po zamknięciu (%s)", self.info["version"], how)
        return {"ok": True}


UPD_TASK = "EmuStart Update"
PANEL_TASK = "EmuStart Panel"


def _confirmed(ulog: Path, before: int, mark: str, seconds: float = 30) -> bool:
    """Skrypt podmiany zapisał w update.log, że czeka na ten proces."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if ulog.is_file() and ulog.stat().st_size > before and \
                    mark in ulog.read_bytes()[before:].decode("utf-8", "replace"):
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def _task_exists(name: str) -> bool:
    r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode == 0


def _launch(script: Path, server: bool) -> str:
    """Start skryptu podmiany niezależnie od tego procesu. Serwer z Harmonogramu: jednorazowe
    zadanie „EmuStart Update” (SYSTEM) — proces potomny zadania serwera ginął razem z nim.
    Zwraca sposób uruchomienia (do logu)."""
    nowin = subprocess.CREATE_NO_WINDOW
    if server and _task_exists(TASK):
        tr = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}"'
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", UPD_TASK, "/SC", "ONCE", "/ST", "00:00",
                            "/RU", "SYSTEM", "/RL", "HIGHEST", "/TR", tr],
                           capture_output=True, text=True, creationflags=nowin)
        if r.returncode == 0:
            r = subprocess.run(["schtasks", "/Run", "/TN", UPD_TASK], capture_output=True, text=True,
                               creationflags=nowin)
            if r.returncode == 0:
                return "zadanie Harmonogramu"
        log.warning("zadanie aktualizacji: %s — uruchamiam skrypt bezpośrednio",
                    (r.stdout + r.stderr).strip()[:200])
    return spawn_powershell(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], script.parent)


def spawn_powershell(args: list, cwd=None) -> str:
    """PowerShell w tle, niezależny od tego procesu (przeżyje jego zamknięcie).

    Bez DETACHED_PROCESS: Windows PowerShell uruchomiony bez konsoli kończy się od razu
    (kod 0), nie wykonując skryptu — przez to nie działała aktualizacja z aplikacji na
    komputerze i nie wracał panel serwera. CREATE_NO_WINDOW daje mu własną, ukrytą konsolę.
    Zwraca sposób uruchomienia (do logu)."""
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    io = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = ["powershell"] + list(args)
    cwd = str(cwd) if cwd else None
    try:
        subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                         close_fds=True, cwd=cwd, **io)
        return "proces poza zadaniem"
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=cwd, **io)
        return "proces"


def _signature_ok(sig_url: str, name: str, sha256_hex: str) -> bool:
    """Podpis `<zip>.sig` z tego samego wydania, zgodny z kluczem wbudowanym w program."""
    from emustart import signing
    if not sig_url.startswith(DOWNLOAD_PREFIX) or sig_url.rsplit("/", 1)[-1] != name + ".sig":
        return False
    try:
        req = urllib.request.Request(sig_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            sig = r.read(4096).decode("ascii", "replace")
    except OSError as ex:
        log.warning("podpis wydania: %s", ex)
        return False
    return signing.verify(name, sha256_hex, sig)


def _extract(zp: Path, new: Path, name: str = "EmuStart") -> None:
    """Rozpakowanie z kontrolą: archiwum całe, exe i _internal są, żadnych ścieżek poza folder."""
    with zipfile.ZipFile(zp) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"Uszkodzony plik w archiwum: {bad}")
        names = z.namelist()
        root = f"{name}/"
        if f"{root}{name}.exe" not in names or not any(n.startswith(f"{root}_internal/") for n in names):
            raise RuntimeError(f"Archiwum nie wygląda jak {name} (brak {name}.exe / _internal).")
        new.mkdir(parents=True, exist_ok=True)
        base = new.resolve()
        for info in z.infolist():
            if not info.filename.startswith(root) or info.is_dir():
                continue
            rel = info.filename[len(root):]
            dst = (new / rel).resolve()
            if base not in dst.parents:
                raise RuntimeError(f"Niedozwolona ścieżka w archiwum: {info.filename}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


def _ps(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def write_script(udir: Path, app: Path, pid: int, argv: list, server: bool = True,
                 exe_name: str = "") -> Path:
    """Skrypt PowerShell podmiany plików (patrz opis modułu)."""
    exe_name = exe_name or ("EmuStartServer" if server else "EmuStart")
    exe = app / f"{exe_name}.exe"
    # dawny EmuStart.exe --server → EmuStartServer.exe --service (przejście na osobny program)
    if exe_name == "EmuStartServer":
        argv = ["--service" if a == "--server" else "--panel" if a == "--server-panel" else a for a in argv]
    args = " ".join(a if re.fullmatch(r"[\w\-=.:]+", a) else '"' + a.replace('"', '') + '"'
                    for a in argv if a != "--restarted")
    if server:
        args = (args + " --restarted").strip()
    use_task = "$true" if server else "$false"
    script = udir / "apply.ps1"
    script.write_text(f"""$ErrorActionPreference = 'Continue'
$app = {_ps(app)}; $new = {_ps(udir / 'new')}; $bak = {_ps(udir / 'backup')}
$log = Join-Path $app 'logs\\update.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function L($m) {{ Add-Content -LiteralPath $log -Value ("{{0:yyyy-MM-dd HH:mm:ss}} {{1}}" -f (Get-Date), $m) }}
L 'aktualizacja: czekam na zamknięcie serwera (PID {pid})'
Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue
# okno panelu (ten sam exe) też musi zwolnić pliki — czekamy, aż EmuStart.exe da się otworzyć do zapisu
$exe = {_ps(exe)}
for ($i = 0; $i -lt 60; $i++) {{
  try {{ $f = [IO.File]::Open($exe, 'Open', 'ReadWrite', 'None'); $f.Close(); break }} catch {{ Start-Sleep 1 }}
}}
L 'kopia zapasowa'
robocopy (Join-Path $app '_internal') (Join-Path $bak '_internal') /MIR /R:5 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
if (Test-Path -LiteralPath $exe) {{ Copy-Item -LiteralPath $exe -Destination (Join-Path $bak {_ps(exe.name)}) -Force }}
L 'podmiana plików'
robocopy (Join-Path $new '_internal') (Join-Path $app '_internal') /MIR /R:30 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
$rc1 = $LASTEXITCODE
robocopy $new $app /E /XD _internal /R:30 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
$rc2 = $LASTEXITCODE
if ($rc1 -ge 8 -or $rc2 -ge 8) {{
  L "BŁĄD podmiany (robocopy $rc1/$rc2) — przywracam poprzednią wersję"
  robocopy (Join-Path $bak '_internal') (Join-Path $app '_internal') /MIR /R:30 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
  if (Test-Path -LiteralPath (Join-Path $bak {_ps(exe.name)})) {{ Copy-Item -LiteralPath (Join-Path $bak {_ps(exe.name)}) -Destination $exe -Force }}
}} else {{
  L "podmiana zakończona (robocopy $rc1/$rc2)"
  Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
  if ({use_task}) {{
    # serwer to osobny program: dawny EmuStart.exe w folderze serwera (bez swojego _internal)
    # nie uruchomiłby się — usuwamy go; zadanie Harmonogramu → EmuStartServer.exe --service
    $old = Join-Path $app 'EmuStart.exe'
    if ($old -ne $exe -and (Test-Path -LiteralPath $old)) {{ Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue; L 'usunięto dawny EmuStart.exe z folderu serwera' }}
    schtasks /Query /TN {_ps(TASK)} 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0 -and $exe -like '*EmuStartServer.exe') {{
      schtasks /Change /TN {_ps(TASK)} /TR ('"' + $exe + '" --service') 2>&1 | Out-Null
      L "zadanie Harmonogramu → $exe --service (kod $LASTEXITCODE)"
      $lnk = Join-Path $env:ProgramData 'Microsoft\\Windows\\Start Menu\\Programs\\EmuStart Server.lnk'
      if (Test-Path -LiteralPath $lnk) {{
        $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $s.TargetPath = $exe; $s.Arguments = '--panel'; $s.IconLocation = $exe + ',0'; $s.Save()
        L 'skrót EmuStart Server → panel nowego programu'
      }}
    }}
  }}
}}
$task = $false
if ({use_task}) {{ schtasks /Query /TN {_ps(TASK)} 2>$null | Out-Null; $task = ($LASTEXITCODE -eq 0) }}
$started = $false
if ($task) {{
  # zadanie bywa jeszcze „uruchomione” (ten skrypt startował z jego procesu) — wtedy /Run
  # nic nie robi (a /End zabiłby ten skrypt); sprawdzamy, czy nowy EmuStart.exe naprawdę ruszył
  schtasks /Run /TN {_ps(TASK)} 2>$null | Out-Null
  L "zadanie Harmonogramu: /Run (kod $LASTEXITCODE)"
  for ($i = 0; $i -lt 30; $i++) {{
    Start-Sleep 1
    if (Get-Process -Name {_ps(exe.stem)} -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $exe }}) {{ $started = $true; break }}
  }}
  if ($started) {{ L 'serwer uruchomiony (zadanie Harmonogramu)' }}
  else {{ L 'zadanie Harmonogramu nie uruchomiło serwera — start bezpośredni' }}
}}
if (-not $started -and {use_task}) {{
  $a = {_ps(args)}
  if ($a) {{ Start-Process -FilePath $exe -ArgumentList $a -WorkingDirectory $app -WindowStyle Hidden }}
  else {{ Start-Process -FilePath $exe -ArgumentList '--service' -WorkingDirectory $app -WindowStyle Hidden }}
  L 'serwer uruchomiony bezpośrednio'
}} elseif (-not $started) {{
  $a = {_ps(args)}
  if ($a) {{ Start-Process -FilePath $exe -ArgumentList $a -WorkingDirectory $app }}
  else {{ Start-Process -FilePath $exe -WorkingDirectory $app }}
  L 'EmuStart uruchomiony ponownie'
}}
# panel serwera wraca w sesji użytkownika, który go zamknął (zadanie tylko interaktywne)
$pu = Join-Path {_ps(udir)} 'panel-user.txt'
if (Test-Path -LiteralPath $pu) {{
  $u = (Get-Content -LiteralPath $pu -Raw).Trim()
  Remove-Item -LiteralPath $pu -Force -ErrorAction SilentlyContinue
  if ($u) {{
    Start-Sleep 3
    $tr = '"' + $exe + '" ' + $(if ($exe -like '*EmuStartServer.exe') {{ '--panel' }} else {{ '--server-panel' }})
    schtasks /Create /F /TN {_ps(PANEL_TASK)} /SC ONCE /ST 00:00 /RU $u /IT /TR $tr 2>&1 | Out-Null
    $c1 = $LASTEXITCODE
    schtasks /Run /TN {_ps(PANEL_TASK)} 2>&1 | Out-Null
    L "panel serwera: zadanie dla $u (utworzenie $c1, start $LASTEXITCODE)"
  }}
}}
schtasks /Delete /TN {_ps(UPD_TASK)} /F 2>$null | Out-Null
""", encoding="utf-8-sig")
    return script
