# EmuStart

Frontend do gier na emulatorach, sterowany padem, w stylu EmulationStation.
Biblioteka i okładki leżą lokalnie, gry na NAS-ie, a ostatnio grane trafiają do
lokalnej pamięci podręcznej. Działa tak samo w domu (LAN) i zdalnie (Tailscale).

Pełny opis zamysłu: [CONCEPT.md](CONCEPT.md).

## Pobranie

Z [Releases](../../releases) pobierz `EmuStart-<wersja>-win64.zip`, rozpakuj
gdziekolwiek (np. `D:\emustart`) i uruchom `EmuStart.exe`. Wersja przenośna:
`config.json`, `data\`, `cache\` i `logs\` powstają obok exe. Potrzebny jest
Microsoft Edge WebView2 Runtime (jest w Windows 10/11).

Program jest dystrybuowany jako katalog, a nie pojedynczy exe: jednoplikowa
wersja PyInstallera była przez Windows Defender błędnie oznaczana heurystyką
jako `Trojan:Win32/Bearfoos.A!ml`.

## Uruchomienie ze źródeł

```bash
pip install -r requirements.txt
python main.py            # pełny ekran (wg ustawień)
python main.py --window   # w oknie
python main.py --debug    # w oknie + DevTools
```

Przy pierwszym starcie otwierają się ustawienia: sprawdź foldery i wybierz
**Wykryj emulatory i skanuj**.

## Sterowanie

| Pad | Klawiatura | Działanie |
|---|---|---|
| D-pad / lewa gałka | strzałki | nawigacja |
| A | Enter | wybierz / graj |
| B | Esc | wstecz / anuluj pobieranie |
| X | X | „Graj teraz (z sieci)” na ekranie pobierania; wybór folderu w ustawieniach |
| Y | Y / P | przypnij / odepnij grę (zostaje w cache na stałe) |
| X (lista gier) | X | filtry: szukaj, gatunek, dekada, gracze, region, producent, sortowanie |
| LB / RB | PgUp / PgDn | strona w górę / w dół |
| LT / RT | Home / End | poprzednia / następna litera |
| Start | F2 / Tab | menu |

## Foldery z grami

W ustawieniach można podać kilka folderów (np. `Z:\ROMS\REDUMP`,
`Z:\ROMS\No-Intro`, `Z:\ROMS\ROMS`). Podfoldery są rozpoznawane po nazwach
EmulationStation (`psx`), No-Intro/Redump (`Sony - PlayStation`) i libretro.
Duplikaty pokazują się raz — z folderu wyżej na liście.

## Emulatory

Uruchomienie gry z systemu bez emulatora proponuje jego pobranie (samodzielny
emulator albo RetroArch + rdzeń). Wszystkie brakujące naraz: Ustawienia →
**Pobierz brakujące emulatory**. Instalacja trafia do folderu emulatorów
z ustawień (np. `D:\emu\emulatory\<Emulator>`).

## Opcje systemu (przytrzymaj A na logo)

Na karuzeli systemów **przytrzymane A** otwiera opcje systemu: wybór logo
(wbudowane, Art Book Next, Carbon, paczka logo PyLinks, ikony RetroArcha albo
sama nazwa), poświata logo, nazwa, emulator, pobranie emulatora, grafiki,
ponowny skan i ukrycie systemu.

## Opcje gry (przytrzymaj A)

Krótkie **A** uruchamia grę, **przytrzymane A** (0,6 s) otwiera opcje:
emulator tylko dla tej gry, wczytanie wybranego zapisu, metadane i opis
(edycja klawiaturą ekranową), wybór okładki i zrzutu z propozycji,
pobranie opisu z sieci, przypinanie.

## Profile i pady

- **Profile** (Start → Zmień profil; przy starcie „Kto gra?”): każdy profil ma
  własne save'y i stany emulatorów (RetroArch, DuckStation, PCSX2, Dolphin,
  PPSSPP, RPCS3), historię i quicksave. Save'y synchronizują się z
  `Z:\emustart\Profiles\<profil>\save`; ten sam profil nie gra na dwóch
  komputerach naraz.
- **Kolejność padów** (Start → Kolejność padów): Gracz 1/2… niezależnie od tego,
  w jakiej kolejności Windows wykrył pady (np. Bluetooth przed USB). Działa
  w RetroArchu, DuckStation, PCSX2 i Dolphinie. Pady PS muszą być widoczne jako
  XInput (Steam Input / DS4Windows).

## Menu w grze

W trakcie gry przytrzymaj **A + Y przez 2 sekundy** (pad PS: X + trójkąt):
Wróć do gry · Zapisz stan · Wczytaj stan · Quicksave i wyjdź · Wyjdź z gry.
Po „Quicksave i wyjdź” następne uruchomienie tej gry wczyta zapisany stan.
Pełne zapisywanie stanów obsługują RetroArch, DuckStation, PCSX2, Dolphin i PPSSPP.

## Metadane i LaunchBox

Start → **Grafiki i metadane**: pobranie bazy LaunchBox (108 MB, potem offline),
„Pobierz brakujące metadane i opisy” (opcjonalnie z Wikipedią) i grafiki.
LaunchBox daje opisy, daty, producentów, gatunki, liczbę graczy oraz grafiki
w kategoriach, w tym Clear Logo — z nich korzysta opcja „Tytuły gier jako logo”.

## Grafiki

Start → **Grafiki: pobierz brakujące**. Narzędzie pobiera okładki i zrzuty dla
gier, które ich nie mają (całość albo wybrany system), z postępem i możliwością
zatrzymania (X). Źródła, w kolejności prób:

1. libretro, dokładna nazwa (No-Intro/Redump),
2. libretro, dopasowanie po liście plików (inna wersja nazwy, „, The”, „(Disc 1)”),
3. SteamGridDB (okładki), 4. IGDB, 5. TheGamesDB (okładki i zrzuty).

Klucze SGDB/IGDB/TGDB importuje się jednym przyciskiem z `config.json` PyLinksWeb.
Dopasowanie po nazwie w SGDB/IGDB/TGDB wymaga podobieństwa ≥ 0,8, żeby
automat nie wstawiał okładek innych gier.

## Jak uruchamiana jest gra

1. Gra jest w cache → start od razu z dysku lokalnego.
2. Mała gra (ZIP kartridża, arcade) → kopia do cache (sekunda), start.
3. Duża płyta (CHD/RVZ/ISO) → pomiar prędkości na pierwszych sekundach kopiowania:
   - LAN: start wprost z NAS-a, kopia do cache leci w tle;
   - zdalnie: ekran pobierania (pobrano / zostało / prędkość / pozostały czas),
     z możliwością „Graj teraz (z sieci)”.
4. Katalogi PS3 → zawsze najpierw pobranie.

ZIP-y dla emulatorów, które nie czytają archiwów, są rozpakowywane do `%TEMP%`
jako pliki tymczasowe (przy wolnym RAM-ie Windows trzyma je w pamięci).
Cache trzyma ostatnie gry (domyślnie 10) plus przypięte.

## Pliki

| Ścieżka | Zawartość |
|---|---|
| `config.json` | ustawienia, emulator per system |
| `data/library.sqlite` | biblioteka, historia gry, stan cache, nazwy setów arcade |
| `data/media/<system>/` | okładki i zrzuty (libretro thumbnails) |
| `cache/<system>/` | lokalne kopie gier |
| `profiles/<id>/` | save'y i stany emulatorów każdego profilu |
| `logs/emustart.log` | log |

## Testy i budowa

```bash
python -m pytest -q tests
powershell -ExecutionPolicy Bypass -File build.ps1   # → dist\EmuStart-<wersja>-win64.zip
```

Zmiany: [CHANGELOG.md](CHANGELOG.md).

`python main.py --browser` uruchamia sam interfejs pod
`http://127.0.0.1:8765/index.html?dev` (do podglądu w przeglądarce, bez okna).
