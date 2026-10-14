# Budowa ze źródeł

## Wymagania

- Windows 10/11, Python 3.12+ (rozwijany na 3.14), Git.
- Zależności: `pip install -r requirements.txt` (pywebview, Pillow, PyInstaller…).

## Uruchomienie

```bash
python main.py              # pełny ekran (wg ustawień)
python main.py --window     # w oknie
python main.py --debug      # w oknie + DevTools
python main.py --browser    # sam interfejs: http://127.0.0.1:8765/index.html?dev
```

Dane (`config.json`, `data\`…) powstają obok `main.py`.

## Testy

```bash
python -m pytest -q tests
```

Testy działają na plikach tymczasowych (sztuczne instalacje emulatorów, NAS
w katalogu tymczasowym) — nie dotykają prawdziwych ustawień.

## Paczka

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1           # dist\EmuStart-<wersja>-win64.zip i dist\EmuStart-Server-<wersja>-win64.zip
powershell -ExecutionPolicy Bypass -File build.ps1 -Deploy   # + kopia do katalogu programu
powershell -ExecutionPolicy Bypass -File build.ps1 -Deploy -ServerTarget E:\EmuStart   # + EmuStart Server
```

Repozytorium i zainstalowany program są w **osobnych katalogach**: `-Deploy` kopiuje program
do `-Target` (domyślnie zmienna `EMUSTART_HOME`, bez niej `D:\emustart`), obok jego
`config.json`, `data\` i `profiles\`. Katalog programu nie może być katalogiem repozytorium.

PyInstaller w trybie katalogu (onedir) z informacją o wersji w exe — wersja
jednoplikowa była błędnie oznaczana przez Windows Defender.

## Struktura

| Moduł | Rola |
|---|---|
| `main.py` | start, okno pywebview, logi |
| `emustart/api.py` | funkcje wywoływane z interfejsu |
| `emustart/launcher.py` | sesja gry: pamięć podręczna, rozpakowanie, profil, uruchomienie |
| `emustart/ingame.py` | adaptery emulatorów: stany, skróty, pady, ustawienia, RetroAchievements |
| `emustart/profiles.py` | profile, junction, synchronizacja z NAS, kopie, wznawianie |
| `emustart/scanner.py`, `systems.py` | skan folderów, rozpoznawanie systemów |
| `emustart/art.py`, `art_sources.py`, `launchbox.py`, `metadata.py` | grafiki i metadane |
| `emustart/installer.py` | pobieranie emulatorów i rdzeni |
| `emustart/server.py` | lokalny serwer interfejsu |
| `web/` | interfejs (HTML/CSS/JS) |

## Podpis wydań (0.42)

Zip wydania podpisuje CI (`tools/release_sign.py`, klucz Ed25519 z sekretu repozytorium
`EMUSTART_SIGNING_KEY`); aktualizacja w programie sprawdza podpis kluczem publicznym z
`emustart/signing.py`. Bez sekretu budowa z tagu się przerywa — wydanie bez podpisu nie
wychodzi.

- Nowy klucz: `python tools/release_sign.py keygen <plik>` — wypisuje klucz publiczny do
  `RELEASE_PUBKEY`; plik z kluczem prywatnym: do sekretu i kopii zapasowej (poza repozytorium).
- Sprawdzenie pliku: `python tools/release_sign.py verify EmuStart-<wersja>-win64.zip`
  (plik `.sig` obok).
- Zmiana klucza: nowy `RELEASE_PUBKEY` w programie działa dopiero w wersji, która go zawiera —
  wydanie z nowym kluczem podpisuj jeszcze starym, następne już nowym.

Zmiany opisuje [CHANGELOG.md](https://github.com/C4rl0s79/EmuStart/blob/main/CHANGELOG.md)
(format Keep a Changelog, wersje SemVer).
