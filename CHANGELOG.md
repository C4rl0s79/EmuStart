# Changelog

Wszystkie istotne zmiany w EmuStart. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/), wersje według
[SemVer](https://semver.org/lang/pl/).

## [0.4.0] — 2026-10-07

### Dodane
- **Opcje gry pod przytrzymanym A** (≥ 0,6 s; krótkie A nadal uruchamia grę):
  - **Emulator dla tej gry** — nadpisuje emulator systemu tylko dla jednej gry,
  - **Wczytaj zapis** — lista stanów tej gry (Quicksave EmuStart, sloty
    i „resume” DuckStation/PCSX2 rozpoznawane po numerze seryjnym, stany
    RetroArcha po nazwie gry, Dolphin/PPSSPP po nazwach poznanych w grze);
    gra startuje od razu z wybranego stanu,
  - **Metadane i opis** — edycja tytułu, producenta, wydawcy, roku, gatunku,
    liczby graczy i opisu; ręczne zmiany mają pierwszeństwo przed pobranymi
    i nie są nadpisywane, „Przywróć dane pobrane” je usuwa,
  - **Okładka / Zrzut ekranu** — ręczny wybór z propozycji (libretro, SteamGridDB,
    TheGamesDB, IGDB), wyszukiwanie pod inną nazwą, usunięcie grafiki,
  - pobranie opisu z sieci, przypinanie.
- **Metadane gier**: producent, wydawca, rok, gatunek, gracze z baz RetroArcha
  (`database/rdb`, offline, dopasowanie po nazwie No-Intro/Redump), opis
  z IGDB/TheGamesDB i streszczenie z Wikipedii (pl, potem en). W podglądzie gry
  opis dociąga się sam po chwili zatrzymania na grze (bez TheGamesDB — limit).
- **Klawiatura ekranowa** obsługiwana padem (polskie znaki) — nazwy profili,
  edycja metadanych, wyszukiwanie grafik. Fizyczna klawiatura też działa.
- **Kolejność padów** (Start → Kolejność padów): jak w Windows, bezprzewodowe
  przed przewodowymi, albo ręcznie („Gracz 1: naciśnij A na swoim padzie”).
  Przed startem gry EmuStart przepina numery urządzeń w RetroArchu
  (`input_playerN_joypad_index`), DuckStation i PCSX2 (`SDL-n` w [PadN])
  oraz Dolphinie (`XInput/n` w [GCPadN]), a po grze przywraca tylko te wpisy
  (także po awarii programu, przy następnym starcie).
- **Profile graczy**: ekran „Kto gra?” przy starcie (gdy profili jest więcej
  niż jeden), tworzenie / zmiana nazwy / usuwanie, profil w pasku górnym.
  - Save'y i stany emulatorów osobno dla każdego profilu: folder save'ów
    emulatora staje się dowiązaniem (junction) do folderu profilu. Dotychczasowe
    save'y trafiają do pierwszego profilu. Obsługiwane: RetroArch, DuckStation,
    PCSX2, Dolphin, PPSSPP, RPCS3.
  - Synchronizacja z `Z:\emustart\Profiles\<profil>\save` przed i po grze
    (nowszy plik wygrywa, nadpisywany lokalny trafia do kopii `_backup`);
    nieudane wysłanie (NAS offline) jest ponawiane przy starcie.
  - Blokada: ten sam profil nie wystartuje gry na dwóch komputerach naraz.
  - Przeniesienie EmuStart do innego folderu przenosi magazyn profili.

### Naprawione
- Klucze SteamGridDB / IGDB / TheGamesDB importowane z PyLinksWeb były
  zaszyfrowane (TPM) i nie działały — teraz są odszyfrowywane i zapisywane
  w EmuStart także zaszyfrowane (TPM/DPAPI, moduł z PyLinksWeb).
- TheGamesDB jest oszczędzany: poniżej 100 zapytań miesięcznego limitu
  przestaje być używany automatycznie.

## [0.3.0] — 2026-10-07

### Dodane
- **Narzędzie „Grafiki”** (Start → Grafiki: pobierz brakujące): zbiorcze
  pobieranie okładek i zrzutów dla całej kolekcji albo jednego systemu,
  z postępem, licznikami źródeł, pozostałym czasem i zatrzymaniem (X).
  Źródła w kolejności prób:
  1. libretro, dokładna nazwa (No-Intro/Redump),
  2. libretro, dopasowanie po liście plików serwera — inna wersja nazwy,
     „, The”, okładka „(Disc 1)” dla gier wielopłytowych, inne zapisy nazw arcade,
  3. SteamGridDB (okładki), IGDB i TheGamesDB (okładki i zrzuty) — klienci jak
     w PyLinksWeb, filtr platformy, próg podobieństwa nazw 0,8.
- Import kluczy SteamGridDB / IGDB / TheGamesDB z `config.json` PyLinksWeb.
- Podgląd gry na liście też korzysta z dopasowania po liście plików libretro.

### Zmienione
- Dystrybucja jako katalog w ZIP (`EmuStart.exe` + `_internal\`) zamiast
  pojedynczego exe — jednoplikowa wersja PyInstallera była przez Windows
  Defender błędnie oznaczana jako `Trojan:Win32/Bearfoos.A!ml`.
- Exe ma metadane wersji (nazwa produktu, wersja, opis) — mniej podejrzany
  dla heurystyk antywirusów.

### Naprawione
- Menu w grze nie wybiera już samo „Wróć do gry” przy puszczaniu A+Y:
  reaguje dopiero, gdy pad jest puszczony przez chwilę i minęło pół sekundy
  od otwarcia.
- Brak sieci nie oznacza już gry jako „bez grafiki”.
- Dwa wątki pobierające tę samą grafikę nie kończą się błędem zapisu pliku.

## [0.2.0] — 2026-10-07

### Dodane
- **Menu w grze**: A+Y przytrzymane 2 s (pad PS: X+trójkąt) — Wróć do gry,
  Zapisz stan, Wczytaj stan, Quicksave i wyjdź, Wyjdź z gry. Pad czytany
  przez XInput niezależnie od fokusu; emulator pauzowany na czas menu.
- Adaptery emulatorów: RetroArch (komendy UDP włączane na czas sesji),
  DuckStation, PCSX2, Dolphin, PPSSPP (skróty czytane z ich ustawień).
- **Quicksave i wyjdź**: stan kopiowany do `data/resume/`, przy następnym
  starcie gra wczytuje go jednorazowo (`-statefile`, `-s`, `--state=`,
  autowczytanie RetroArcha).

### Naprawione
- Menu w grze nie reagowało na A/B: wywołania okna (`evaluate_js`) przy grze
  na pełnym ekranie czekały ~20 s i blokowały UI. Teraz UI odpytuje stan menu.
- Czarny ekran i niewidoczne menu w trybie pełnoekranowym: okno EmuStart nie
  jest już minimalizowane w trakcie gry.
- Po wyjściu z gry ekran zostawał na „Gra uruchomiona”.

## [0.1.0] — 2026-10-07

### Dodane
- Interfejs w stylu EmulationStation sterowany padem: karuzela systemów, lista
  gier z okładką i zrzutem, ustawienia, menu Start.
- Wykrywanie emulatorów (samodzielne + rdzenie RetroArch wg baz w `info/*.info`),
  wybór emulatora per system.
- Skan kolekcji na NAS do SQLite: gry wielopłytowe, `.m3u`/`.cue`/`.gdi`,
  katalogi PS3; nazwy arcade z `mame -listxml` (ukryte klony i BIOS-y).
- Uruchamianie z pamięci podręcznej: 10 ostatnich gier + przypięte, kopiowanie
  z wznawianiem; pomiar sieci — w LAN płyty startują wprost z NAS z kopią w tle,
  zdalnie ekran pobierania (pobrano / zostało / prędkość / czas, „Graj teraz”).
- ZIP-y rozpakowywane do pamięci (`%TEMP%` z atrybutem pliku tymczasowego) dla
  emulatorów, które nie czytają archiwów; playlisty `.m3u` tworzone w locie.
- Okładki i zrzuty z serwera miniatur libretro.
