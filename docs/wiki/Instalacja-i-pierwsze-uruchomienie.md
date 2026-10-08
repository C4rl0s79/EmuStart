# Instalacja i pierwsze uruchomienie

## Wymagania

- Windows 10 lub 11 (64-bit).
- Microsoft Edge WebView2 Runtime — jest w Windows 10/11; jeśli okno jest puste,
  doinstaluj go ze strony Microsoftu.
- Pad zgodny z XInput (Xbox). Pady PlayStation przez Steam Input albo DS4Windows.
- Gry w folderach na dysku lub NAS-ie (dysk sieciowy zmapowany pod literą, np. `Z:`).

## Instalacja

1. Z [Releases](https://github.com/C4rl0s79/EmuStart/releases) pobierz
   `EmuStart-<wersja>-win64.zip`.
2. Rozpakuj w wybrane miejsce, np. `D:\emustart`. To wersja przenośna — wszystkie
   dane (`config.json`, `data\`, `cache\`, `profiles\`, `logs\`) powstają obok programu.
3. Uruchom `EmuStart.exe`.

Aktualizacja: zamknij EmuStart i rozpakuj nową wersję w to samo miejsce
(nadpisując `EmuStart.exe` i `_internal\`). Dane zostają.

## Pierwsze uruchomienie

Otwierają się **Ustawienia**:

1. **Foldery z grami** — dodaj jeden lub kilka (A → wybór folderu padem). Kolejność
   = pierwszeństwo, gdy ta sama gra jest w kilku folderach. Zobacz
   [Foldery z grami i systemy](Foldery-z-grami-i-systemy).
2. **Emulatory** — folder, w którym są (albo mają się zainstalować) emulatory,
   np. `D:\emu\emulatory`.
3. **Pamięć podręczna** — gdzie trzymać lokalne kopie gier (domyślnie `cache\`
   obok programu).
   **BIOS-y** (opcjonalnie) — folder z BIOS-ami, np. `bios` z RetroBat; EmuStart
   kopiuje z niego brakujące pliki do RetroArcha (patrz [Emulatory](Emulatory#bios-y)).
4. **Wykryj emulatory i skanuj** — przypisuje znalezione emulatory do systemów
   i skanuje kolekcję.
5. **Pobierz brakujące emulatory** — dla systemów bez emulatora (patrz
   [Emulatory](Emulatory)).
6. **Gotowe, przejdź do gier**.

Potem EmuStart pyta **„Kto gra na tym komputerze?”** — wybierz profil (profile
z NAS-a są już na liście) albo utwórz nowy. Do tego profilu trafią save'y i konto
RetroAchievements zastane w emulatorach na tym komputerze. Więcej:
[Profile graczy](Profile-graczy).

## Parametry startu

| Parametr | Działanie |
|---|---|
| `--window` | w oknie zamiast pełnego ekranu |
| `--debug` | w oknie z narzędziami deweloperskimi |
| `--pady=python` | pady w menu przez XInput (domyślnie) |
| `--pady=przegladarka` | pady przez Gamepad API przeglądarki |
| `--pady=brak` | bez obsługi padów w menu (tylko klawiatura) |
| `--browser` | tylko serwer interfejsu pod `http://127.0.0.1:8765/index.html?dev` (dla deweloperów) |

## Windows Defender

EmuStart jest dystrybuowany jako katalog z `EmuStart.exe` i `_internal\`, a nie
jeden plik — jednoplikowa wersja (PyInstaller onefile) była błędnie oznaczana
heurystyką jako `Trojan:Win32/Bearfoos.A!ml`. Jeśli mimo to pojawi się alert,
zgłoś fałszywy alarm do Microsoftu albo zbuduj program samodzielnie
([Budowa ze źródeł](Budowa-ze-źródeł)).
