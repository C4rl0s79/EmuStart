# build.ps1 — buduje przenośne EmuStart (PC) i EmuStart Server (PyInstaller) + ZIP-y.
# Użycie:  powershell -ExecutionPolicy Bypass -File build.ps1 [-Deploy] [-Target <katalog EmuStart>]
#          [-ServerTarget <katalog EmuStart Server>]
# Wynik:   dist\EmuStart\EmuStart.exe             i dist\EmuStart-<wersja>-win64.zip
#          dist\EmuStartServer\EmuStartServer.exe i dist\EmuStart-Server-<wersja>-win64.zip
#          (portable — config/data/cache/logs powstają obok exe)

param(
    [switch]$Deploy,               # -Deploy: po budowie kopiuje program do katalogu programu
    # katalog zainstalowanego programu (osobno od repozytorium): -Target albo EMUSTART_HOME
    [string]$Target = $(if ($env:EMUSTART_HOME) { $env:EMUSTART_HOME } else { "D:\emustart" }),
    # -ServerTarget: katalog EmuStart Server (z -Deploy) — bez niego serwer nie jest kopiowany
    [string]$ServerTarget = ""
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== EmuStart: testy ==" -ForegroundColor Cyan
python -m pytest -q tests | Out-Host
if ($LASTEXITCODE -ne 0) {
    # testy z lokalnym serwerem testowym bywają niestabilne na maszynach GitHuba (zerwane połączenia,
    # przekroczony czas) — za każdym razem inne, osobno przechodzą; powtórka tylko tych, które padły,
    # w świeżym procesie. Padną drugi raz — budowa przerwana jak dotąd.
    Write-Host "Część testów nie przeszła — powtarzam tylko je." -ForegroundColor Yellow
    python -m pytest -q --lf tests | Out-Host
}
if ($LASTEXITCODE -ne 0) { Write-Host "Testy nie przeszły — przerywam." -ForegroundColor Red; exit 1 }

Write-Host "== EmuStart: build exe ==" -ForegroundColor Cyan
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist)  { Remove-Item dist  -Recurse -Force }
python -m PyInstaller --noconfirm --clean emustart.spec | Out-Host

$ver = (Select-String -Path "emustart\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
foreach ($app in @(@{ Dir = "EmuStart"; Zip = "EmuStart-$ver-win64.zip" },
                   @{ Dir = "EmuStartServer"; Zip = "EmuStart-Server-$ver-win64.zip" })) {
    $exe = Join-Path $PSScriptRoot "dist\$($app.Dir)\$($app.Dir).exe"
    if (-not (Test-Path $exe)) {
        Write-Host "BŁĄD: nie powstał dist\$($app.Dir)\$($app.Dir).exe" -ForegroundColor Red
        exit 1
    }
    $zip = Join-Path $PSScriptRoot "dist\$($app.Zip)"
    Compress-Archive -Path (Join-Path $PSScriptRoot "dist\$($app.Dir)") -DestinationPath $zip -Force
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "OK: $zip ($mb MB)" -ForegroundColor Green
}

function Deploy-App([string]$Dir, [string]$Dest) {
    # kopia do katalogu programu (obok config.json, data\, profiles\) — repozytorium i program osobno
    $full = [System.IO.Path]::GetFullPath($Dest).TrimEnd('\')
    if ($full -eq $PSScriptRoot.TrimEnd('\')) {
        Write-Host "Katalog programu nie może być katalogiem repozytorium ($full)." -ForegroundColor Red
        exit 1
    }
    $running = Get-Process -Name $Dir -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$full\*" }
    if ($running) {
        Write-Host "$Dir działa z $full — zamknij go i uruchom ponownie z -Deploy." -ForegroundColor Yellow
        exit 2
    }
    New-Item -ItemType Directory -Force -Path $full | Out-Null
    robocopy (Join-Path $PSScriptRoot "dist\$Dir") $full /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Host "BŁĄD kopiowania (robocopy $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
    Write-Host "Skopiowano do $full\$Dir.exe" -ForegroundColor Green
}

if ($Deploy) {
    Deploy-App "EmuStart" $Target
    if ($ServerTarget) { Deploy-App "EmuStartServer" $ServerTarget }
}
