# build.ps1 — buduje przenośny EmuStart (PyInstaller, wersja katalogowa) + ZIP.
# Użycie:  powershell -ExecutionPolicy Bypass -File build.ps1 [-Deploy]
# Wynik:   dist\EmuStart\EmuStart.exe i dist\EmuStart-<wersja>-win64.zip
#          (portable — config/data/cache/logs powstają obok exe)

param([switch]$Deploy)   # -Deploy: po budowie kopiuje binarkę do katalogu programu
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== EmuStart: testy ==" -ForegroundColor Cyan
python -m pytest -q tests | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Host "Testy nie przeszły — przerywam." -ForegroundColor Red; exit 1 }

Write-Host "== EmuStart: build exe ==" -ForegroundColor Cyan
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist)  { Remove-Item dist  -Recurse -Force }
python -m PyInstaller --noconfirm --clean emustart.spec | Out-Host

$exe = Join-Path $PSScriptRoot "dist\EmuStart\EmuStart.exe"
if (-not (Test-Path $exe)) {
    Write-Host "BŁĄD: nie powstał dist\EmuStart\EmuStart.exe" -ForegroundColor Red
    exit 1
}
$ver = (Select-String -Path "emustart\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$zip = Join-Path $PSScriptRoot "dist\EmuStart-$ver-win64.zip"
Compress-Archive -Path (Join-Path $PSScriptRoot "dist\EmuStart") -DestinationPath $zip -Force
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "OK: $zip ($mb MB)" -ForegroundColor Green

if ($Deploy) {
    # Kopia do katalogu programu (obok config.json, data\, profiles\) — tam
    # binarka korzysta z ustawień i biblioteki, a kolejne budowanie jej nie kasuje.
    $running = Get-Process -Name EmuStart -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$PSScriptRoot\*" }
    if ($running) {
        Write-Host "EmuStart działa z $PSScriptRoot — zamknij go i uruchom ponownie z -Deploy." -ForegroundColor Yellow
        exit 2
    }
    robocopy (Join-Path $PSScriptRoot "dist\EmuStart") $PSScriptRoot /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Host "BŁĄD kopiowania (robocopy $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
    Write-Host "Skopiowano do $PSScriptRoot\EmuStart.exe" -ForegroundColor Green
}
