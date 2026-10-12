"""updater — aktualizacja EmuStart z wydań na GitHubie (panel serwera).

1. `latest()` — najnowsze wydanie C4rl0s79/EmuStart i plik `EmuStart-<wersja>-win64.zip`.
2. `Job` — pobranie ZIP-a do `update\\` obok exe (z postępem), sprawdzenie archiwum
   (`EmuStart\\EmuStart.exe` i `_internal`), rozpakowanie do `update\\new`.
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
import urllib.request
import zipfile
from pathlib import Path

from emustart import __version__, paths

log = logging.getLogger("emustart.updater")

REPO = "C4rl0s79/EmuStart"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = f"EmuStart/{__version__}"
TASK = "EmuStart Server"


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def latest(timeout: float = 15) -> dict:
    """{version, tag, url, size, notes, published, newer} najnowszego wydania."""
    req = urllib.request.Request(API_URL, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.loads(r.read())
    tag = rel.get("tag_name") or ""
    ver = tag.lstrip("v")
    asset = next((a for a in rel.get("assets") or []
                  if re.fullmatch(r"EmuStart-[\d.]+-win64\.zip", a.get("name") or "")), None)
    return {"version": ver, "tag": tag, "current": __version__,
            "newer": vtuple(ver) > vtuple(__version__),
            "url": asset["browser_download_url"] if asset else "", "size": asset["size"] if asset else 0,
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
            zp = d / f"EmuStart-{self.info['version']}-win64.zip"
            self._download(self.info["url"], zp)
            self.phase = "extracting"
            new = d / "new"
            _extract(zp, new)
            zp.unlink(missing_ok=True)
            self.phase = "ready"
            log.info("aktualizacja %s gotowa do podmiany", self.info["version"])
        except Exception as ex:
            log.exception("aktualizacja")
            self.phase, self.error = "error", str(ex) or ex.__class__.__name__

    def _download(self, url: str, dest: Path) -> None:
        if not url:
            raise RuntimeError("W wydaniu nie ma pliku EmuStart-…-win64.zip.")
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
                self.done += len(chunk)
        if self.total and part.stat().st_size != self.total:
            raise RuntimeError(f"Pobrano {part.stat().st_size} z {self.total} B — spróbuj ponownie.")
        part.replace(dest)

    def apply(self, argv: list, server: bool = True) -> dict:
        """Uruchamia skrypt podmiany (poza tym procesem). Wołający kończy proces.
        server=True — potem start zadania Harmonogramu serwera (gdy jest), False —
        EmuStart z tymi samymi parametrami (okno do grania)."""
        if self.phase != "ready":
            return {"ok": False, "reason": "Aktualizacja nie jest gotowa."}
        if os.name != "nt" or not getattr(sys, "frozen", False):
            return {"ok": False, "reason": "Podmiana działa tylko w EmuStart.exe na Windows."}
        script = write_script(update_dir(), paths.APP, os.getpid(), argv, server=server)
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        try:          # poza zadaniem Harmonogramu (job object) — inaczej zginąłby razem z serwerem
            subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                             close_fds=True, cwd=str(update_dir()))
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=str(update_dir()))
        self.phase = "applying"
        log.info("aktualizacja %s: podmiana plików po zamknięciu serwera", self.info["version"])
        return {"ok": True}


def _extract(zp: Path, new: Path) -> None:
    """Rozpakowanie z kontrolą: archiwum całe, exe i _internal są, żadnych ścieżek poza folder."""
    with zipfile.ZipFile(zp) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"Uszkodzony plik w archiwum: {bad}")
        names = z.namelist()
        root = "EmuStart/"
        if f"{root}EmuStart.exe" not in names or not any(n.startswith(f"{root}_internal/") for n in names):
            raise RuntimeError("Archiwum nie wygląda jak EmuStart (brak EmuStart.exe / _internal).")
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


def write_script(udir: Path, app: Path, pid: int, argv: list, server: bool = True) -> Path:
    """Skrypt PowerShell podmiany plików (patrz opis modułu)."""
    exe = app / "EmuStart.exe"
    args = " ".join(a if re.fullmatch(r"[\w\-=.:]+", a) else '"' + a.replace('"', '') + '"'
                    for a in argv if a != "--restarted")
    if server:
        args = (args + " --restarted").strip()
    use_task = "$true" if server else "$false"
    script = udir / "apply.ps1"
    script.write_text(f"""$ErrorActionPreference = 'Continue'
$app = {_ps(app)}; $new = {_ps(udir / 'new')}; $bak = {_ps(udir / 'backup')}
$log = Join-Path $app 'logs\\update.log'
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
Copy-Item -LiteralPath $exe -Destination (Join-Path $bak 'EmuStart.exe') -Force
L 'podmiana plików'
robocopy (Join-Path $new '_internal') (Join-Path $app '_internal') /MIR /R:30 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
$rc1 = $LASTEXITCODE
robocopy $new $app /E /XD _internal /R:30 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
$rc2 = $LASTEXITCODE
if ($rc1 -ge 8 -or $rc2 -ge 8) {{
  L "BŁĄD podmiany (robocopy $rc1/$rc2) — przywracam poprzednią wersję"
  robocopy (Join-Path $bak '_internal') (Join-Path $app '_internal') /MIR /R:30 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
  Copy-Item -LiteralPath (Join-Path $bak 'EmuStart.exe') -Destination $exe -Force
}} else {{
  L "podmiana zakończona (robocopy $rc1/$rc2)"
  Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
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
    if (Get-Process -Name EmuStart -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $exe }}) {{ $started = $true; break }}
  }}
  if ($started) {{ L 'serwer uruchomiony (zadanie Harmonogramu)' }}
  else {{ L 'zadanie Harmonogramu nie uruchomiło serwera — start bezpośredni' }}
}}
if (-not $started -and {use_task}) {{
  $a = {_ps(args)}
  if ($a) {{ Start-Process -FilePath $exe -ArgumentList $a -WorkingDirectory $app -WindowStyle Hidden }}
  else {{ Start-Process -FilePath $exe -ArgumentList '--server' -WorkingDirectory $app -WindowStyle Hidden }}
  L 'serwer uruchomiony bezpośrednio'
}} elseif (-not $started) {{
  $a = {_ps(args)}
  if ($a) {{ Start-Process -FilePath $exe -ArgumentList $a -WorkingDirectory $app }}
  else {{ Start-Process -FilePath $exe -WorkingDirectory $app }}
  L 'EmuStart uruchomiony ponownie'
}}
""", encoding="utf-8-sig")
    return script
