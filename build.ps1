# build.ps1 — buduje przenośny EmuStart (PyInstaller, wersja katalogowa) + ZIP.
# Użycie:  powershell -ExecutionPolicy Bypass -File build.ps1
# Wynik:   dist\EmuStart\EmuStart.exe i dist\EmuStart-<wersja>-win64.zip
#          (portable — config/data/cache/logs powstają obok exe)

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
