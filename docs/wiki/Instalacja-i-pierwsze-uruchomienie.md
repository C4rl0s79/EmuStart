# Instalacja i pierwsze uruchomienie

EmuStart to dwa programy:

- **EmuStart Server** — na komputerze z grami: biblioteka, grafiki, opisy, konta i zapisy
  graczy. Instalacja i panel: [EmuStart Server](EmuStart-Server).
- **EmuStart** — na komputerze, na którym się gra (sterowany padem). Bierze wszystko od
  serwera; gry pobrane na dysk działają też bez serwera. Na telefonie — [aplikacja na
  Androida](Aplikacja-na-Androida).

Na komputerze z grami, na którym też się gra, instaluje się oba programy (w osobnych
folderach) — EmuStart loguje się do serwera jak każde inne urządzenie.

## Wymagania (EmuStart)

- Windows 10 lub 11 (64-bit).
- Microsoft Edge WebView2 Runtime — jest w Windows 10/11; jeśli okno jest puste,
  doinstaluj go ze strony Microsoftu.
- Pad zgodny z XInput (Xbox). Pady PlayStation przez Steam Input albo DS4Windows.
- Działający [EmuStart Server](EmuStart-Server) i konto na nim.

## Instalacja

1. Z [Releases](https://github.com/C4rl0s79/EmuStart/releases) pobierz
   `EmuStart-<wersja>-win64.zip` (nie `EmuStart-Server-…` — to serwer).
2. Rozpakuj w wybrane miejsce, np. `D:\emustart`. To wersja przenośna — dane
   (`config.json`, `data\`, `cache\`, `profiles\`, `logs\`) powstają obok programu.
3. Uruchom `EmuStart.exe`.

Aktualizacja: menu Start → **Aktualizuj EmuStart** (pobranie, sprawdzenie podpisu,
podmiana plików i ponowne uruchomienie). Dane zostają.

## Pierwsze uruchomienie

Otwierają się **Ustawienia**:

1. **Zaloguj** — link z panelu serwera (Dodaj urządzenie → *Kopiuj link*) albo adres
   tunelu, potem konto i hasło ([logowanie urządzeń](EmuStart-Server#logowanie-urządzeń)).
   Biblioteka pobiera się z serwera.
2. **Emulatory** — folder, w którym są (albo mają się zainstalować) emulatory,
   np. `D:\emu\emulatory`.
3. **Pamięć podręczna (pobrane gry)** — gdzie trzymać gry pobrane z serwera (domyślnie
   `cache\` obok programu).
4. **Wykryj emulatory** — przypisuje emulatory do systemów (zalecane przez serwer, gdy
   są zainstalowane).
5. **Pobierz brakujące emulatory** — dla systemów bez emulatora (patrz
   [Emulatory](Emulatory)).
6. **Gotowe, przejdź do gier**.

Potem EmuStart pyta **„Kto gra na tym komputerze?”** — wybierz konto (konta z serwera są
już na liście) albo utwórz nowe. Do niego trafią zapisy i konto RetroAchievements zastane
w emulatorach na tym komputerze. Więcej: [Profile graczy](Profile-graczy).

BIOS-y, grafiki i opisy ustawia się na serwerze — EmuStart pobiera je sam.

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

EmuStart i EmuStart Server są dystrybuowane jako katalogi z exe i `_internal\`, a nie
jeden plik — jednoplikowa wersja (PyInstaller onefile) była błędnie oznaczana heurystyką
jako `Trojan:Win32/Bearfoos.A!ml`. Jeśli mimo to pojawi się alert, zgłoś fałszywy alarm
do Microsoftu albo zbuduj program samodzielnie ([Budowa ze źródeł](Budowa-ze-źródeł)).
