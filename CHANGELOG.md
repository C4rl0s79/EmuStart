# Changelog

Wszystkie istotne zmiany w EmuStart. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/), wersje według
[SemVer](https://semver.org/lang/pl/).

## [0.8.0] — 2026-10-07

### Dodane
- **Filtry listy gier (X)**: szukanie w tytule (klawiatura ekranowa), gatunek,
  dekada, liczba graczy, region (z tagów nazwy: USA, Europa, Japonia…),
  producent, „pokaż: lokalne / przypięte / grane / niegrane” i sortowanie
  (tytuł, rok, ostatnio grane, czas gry, rozmiar). Przy każdej wartości liczba
  gier, które zostaną przy pozostałych filtrach. Nagłówek listy pokazuje
  „9 z 3883 · Role playing games, 1990s, Europa”. Filtry są zapamiętywane
  osobno dla każdego systemu.
- **Metadane całego systemu przy wejściu do niego** (baza RetroArcha, offline,
  jedna transakcja; SNES 3883 gry ≈ 0,1 s) — dotąd liczone tylko dla
  oglądanej gry. Szybsze dopasowanie nazw (indeks po kluczu tytułu).
- **Arcade**: rok, producent i liczba graczy z `mame -listxml`, gatunek
  z `catver.ini` (`D:\emu\dat\Support Files`; ustawienie `mame_support_dir`).
  Uzupełniane raz, w tle, po aktualizacji programu. FBNeo: gatunek dla
  6564 z 6595 setów.

### Zmienione
- Wybór okładki/zrzutu: miniatury, które się nie wczytały, znikają z listy
  (jak w wyborze logo) — zamiast pustych kafelków.

## [0.7.3] — 2026-10-07

### Naprawione
- **Wybór logo padem nie działał**: kafelek „bez logo” (bez obrazka) był
  ukrywany jak uszkodzony obrazek, przez co liczba kolumn siatki wychodziła
  nieskończona — strzałki skakały na początek/koniec listy, a zaznaczenie
  trafiało na niewidoczne kafelki. „Bez logo” jest teraz kafelkiem z nazwą,
  logo, które się nie wczytają, znikają z listy, a kolumny liczone są
  z widocznych kafelków (to samo w wyborze okładek gry).

## [0.7.2] — 2026-10-07

### Naprawione
- Pad w wyborze logo (i potencjalnie w innych miejscach) przenosił fokus na
  **pasek zadań Windows** (`Shell_TrayWnd` — ustalone z diagnostyki 0.7.1;
  to nawigacja padem w samym Windows, a nie w WebView2). Strażnik fokusu:
  gdy w ciągu 2 s od naciśnięcia pada fokus trafi na pasek zadań, EmuStart
  go odzyskuje. Przełączanie do innych programów (Alt+Tab) nie jest blokowane.
- Po odzyskaniu fokusu przytrzymany przycisk nie jest wysyłany drugi raz jako
  nowe naciśnięcie.

## [0.7.1] — 2026-10-07

### Naprawione
- Krzyżak pada w menu systemu mógł „wyrzucić” fokus z okna (interfejs przestawał
  reagować). Windows przekazuje do okna klawisze pada Xbox (`VK_GAMEPAD_*`),
  a WebView2 robi z nich nawigację fokusem po stronie. Te klawisze są teraz
  blokowane — pad obsługuje wyłącznie EmuStart.

### Dodane
- Diagnostyka fokusu w logu: gdy okno EmuStart straci fokus bez uruchomionej
  gry, w `logs/emustart.log` zapisuje się, które okno go przejęło; zapisują
  się też zablokowane klawisze pada.

## [0.7.0] — 2026-10-07

### Dodane
- **Opcje systemu pod przytrzymanym A na logo w karuzeli** (krótkie A nadal
  wchodzi do systemu):
  - **Logo** — wybór padem z siatki propozycji: wbudowane, pobrane wcześniej,
    motywy Art Book Next (ES-DE) i Carbon, paczka logo PyLinks
    (`platform_logos\_variants`: białe / kolorowe / czarne, dopasowanie po
    nazwach w stylu LaunchBox) oraz ikony z 9 motywów RetroArcha; także „bez
    logo” (sama nazwa). Wybrane logo jest kopiowane do `data/media/_systems`.
  - **Poświata logo** (włącz/wyłącz), **Nazwa** systemu (klawiatura ekranowa,
    „Przywróć nazwę”), **Emulator** systemu, **Pobierz emulator** (gdy brak),
    **Grafiki: pobierz brakujące** dla tego systemu, **Skanuj ponownie** tylko
    ten system, **Ukryj system**.
- Ustawienie `logo_pack_dir` — własny folder z paczką logo (domyślnie
  `D:\py\PyLinks\platform_logos`).

### Naprawione
- Zamknięcie okna nie kończyło programu, gdy w tle trwało pobieranie grafik —
  proces zostawał w pamięci (bez okna) aż do końca pracy. Teraz zamknięcie okna
  przerywa zadania w tle i kończy program od razu.

## [0.6.1] — 2026-10-07

### Zmienione
- **Wybór folderu padem** zamiast wpisywania ścieżki: lista dysków (z etykietą
  i rodzajem — dysk, sieć), wchodzenie w podfoldery (A / →), w górę (B / ←),
  X wybiera bieżący folder. Przy folderze widać, ile systemów EmuStart w nim
  rozpoznaje, a przy podfolderach — jaki to system. Dotyczy folderów z grami,
  folderu emulatorów i pamięci podręcznej. Okno Windows (mysz) zostaje pod X
  w ustawieniach, wpisanie ścieżki klawiaturą — pod Y.

## [0.6.0] — 2026-10-07

### Dodane
- **Pobieranie emulatorów i rdzeni RetroArcha.**
  - Uruchomienie gry z systemu bez emulatora pyta: „Brak emulatora: Atari Lynx.
    Pobrać i zainstalować?” z listą plików, wersjami i rozmiarem. A = pobierz
    i od razu graj, B = anuluj. Postęp w bajtach, B przerywa.
  - Ustawienia → **Pobierz brakujące emulatory**: lista wszystkich systemów
    z biblioteki bez emulatora i jednorazowe pobranie wszystkiego.
  - Wybór jak dotychczas: samodzielny emulator tam, gdzie EmuStart go preferuje
    (DuckStation, PCSX2, RPCS3, PPSSPP, Dolphin, Cemu, Eden, Azahar, melonDS,
    ares, Snes9x, mGBA, Flycast, Xenia, xemu, shadPS4, Vita3K, MAME), w reszcie
    rdzeń RetroArcha (Stella, Handy, VICE, Gambatte, Genesis Plus GX, FBNeo…);
    brak RetroArcha → najpierw sam RetroArch (buildbot, wersja stabilna).
  - Źródła z aktualizatora ROM Helpera: GitHub Releases, dolphin-emu.org,
    strona wydań Edena, buildbot.libretro.com. Instalacja do folderu
    emulatorów, DuckStation/PCSX2/Dolphin w trybie przenośnym (jak Twoje).
    Wersje zapisywane w `data/emu_versions.json` (format ROM Helpera).
  - Po instalacji system od razu dostaje emulator w ustawieniach.
  - Archiwa .7z: 7-Zip z systemu, a gdy go brak — `7zr.exe` z 7-zip.org.

## [0.5.0] — 2026-10-07

### Dodane
- **Kilka folderów z grami** (Ustawienia → Foldery z grami): dodawanie (ścieżka
  z klawiatury ekranowej albo wybór folderu), usuwanie (Y), zmiana kolejności
  (◀ ▶). Ta sama gra w kilku folderach jest pokazywana raz — wygrywa folder
  wyżej na liście; foldery tego samego systemu z różnych miejsc łączą się
  w jeden system. Niedostępny folder (np. wyłączony NAS) nie usuwa gier
  z biblioteki.
- **Nazwy folderów No-Intro / Redump**: „Sony - PlayStation”, „Atari - Atari
  7800 (BIN)”, „Nintendo - Wii - NKit RVZ [zstd-19-128k]”, „… (PSN) (Decrypted)”
  itd. są rozpoznawane jako systemy (obok nazw EmulationStation). Foldery
  archiwalne (Flux, KryoFlux, WOZ, Waveform, Updates, Encrypted…) są pomijane;
  nierozpoznany folder można włączyć ręcznie w ustawieniach.
- Nowe systemy: Atari 8-bit, Atari ST, ColecoVision, Intellivision, Vectrex,
  Virtual Boy, Pokémon Mini, Channel F, Supervision, Mega Duck, Sega Pico,
  Arcadia 2001, Super Cassette Vision, Game.com, VIC-20, Plus/4, Game Pocket
  Computer.
- **Logo systemów**: brakujące (poza 40 wbudowanymi) pobierane z motywu Carbon
  dla EmulationStation (SVG), a gdy go nie ma — ikona systemu z RetroArcha.
  Pobrane logo dostają jasną poświatę, żeby ciemne elementy były widoczne.

### Naprawione
- Pad wyłączony i włączony ponownie w trakcie działania programu nie sterował
  interfejsem (Gamepad API w WebView2 nie zauważa ponownie podłączonego pada).
  Pady XInput są teraz czytane w Pythonie (jak w menu w grze) i działają od razu
  po ponownym podłączeniu; Gamepad API zostaje dla pozostałych padów.

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
