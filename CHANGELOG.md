# Changelog

Wszystkie istotne zmiany w EmuStart. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/), wersje według
[SemVer](https://semver.org/lang/pl/).

## [Nieopublikowane]

Plan najbliższych zmian: [ROADMAP.md](ROADMAP.md).

## [0.25.11] — 2026-10-09

### Zmienione
- Android: **układ dla ekranu telefonu** (poziomo, ok. 900 × 410 px): niskie paski górny
  i dolny, większy tekst i wiersze listy; podgląd gry — okładka i zrzut obok siebie
  (zrzut ok. 2× większy), obok tytuł i **wszystkie szczegóły**, pod obrazkami opis;
  w nagłówku listy logo platformy i logo gry; większe logo w karuzeli systemów;
  okna dopasowane do szerokości. Rozmiary obrazków liczone tak, żeby szczegóły miały
  zawsze co najmniej 38 % szerokości (także przy kwadratowych i szerokich okładkach).
  Podgląd w przeglądarce na komputerze: `index.html?dev&phone`.

## [0.25.10] — 2026-10-09

### Zmienione
- **Kopie zapasowe zapisów spakowane zstd (poziom 19)**, gdy plik się kompresuje — kopia
  karty pamięci PS2 (8 MB, w większości pusta) zajmuje kilka KB (`<plik>.zst`; na NAS-ie
  istniejące kopie spakowane: 87 MB → 11 MB). Stanów gry nie pakujemy — emulatory
  (PCSX2, RetroArch, DuckStation) kompresują je same, zstd 19 dawało 0–1 %.
- Serwer: zapis pliku o **tej samej treści** (np. emulator tylko otworzył kartę) nie tworzy
  kopii zapasowej — tylko aktualizuje datę.
- Kopie stanów robione przez PCSX2 (`.p2s.backup`) nie są już wysyłane na NAS.

### Poprawione
- Android: **folder kart innego emulatora nie jest przypisywany** — DuckStation i AetherSX2
  pokazywały (i mogły używać) folderu ArmSX2, gdy własnego nie było. Teraz tylko folder
  emulatora; DuckStation — wyjaśnienie o Android/data.

## [0.25.9] — 2026-10-09

### Dodane
- **Foldery z grami z komputera na serwerze** — EmuStart na Windows wysyła serwerowi swoją
  listę folderów (względem udziału na NAS-ie, np. `WHDLoad`), serwer dopisuje brakujące
  i je skanuje (przy starcie i po każdej zmianie folderów). Dzięki temu na telefonie są
  platformy Amiga WHDLoad (gry i dema). **Serwer trzeba zaktualizować.**
- Android: rdzenie RetroArcha, na których gra już działała na tym telefonie, są w Ustawieniach
  oznaczone **✓** (RetroArch nie pokazuje innym aplikacjom, które rdzenie są pobrane).

### Poprawione
- Android: **przygotowanie gry jak na Windows** — ZIP z kilkoma plikami (**MSU-1**: ROM + `.msu`
  + ścieżki `.pcm`; Amiga na kilku dyskietkach) albo dla emulatora, który ZIP-ów nie czyta,
  jest rozpakowany w całości (raz, obok gry; usuwany razem z grą); gry na kilku płytach
  dostają playlistę `.m3u` (zmiana płyty w emulatorze); playlista dla emulatora bez jej
  obsługi — pierwsza płyta. Wcześniej gry MSU-1 startowały bez muzyki, a wielopłytowe
  tylko z pierwszą płytą.

## [0.25.8] — 2026-10-09

### Poprawione
- Android: **wybór rdzenia RetroArcha** — Ustawienia → Emulatory pokazują osobno każdy rdzeń
  pasujący do platformy (np. SNES: Snes9x, bsnes; NES: Nestopia, FCEUmm, Mesen; PS1:
  SwanStation, Beetle PSX HW, PCSX ReARMed…), nie tylko domyślny. Serwer podaje pełną
  listę rdzeni platformy. **Serwer trzeba zaktualizować.** Rdzeń trzeba mieć pobrany
  w RetroArchu (Online Updater → Core Downloader).

## [0.25.7] — 2026-10-09

### Poprawione
- **Blokada profilu nie „wisi” po zamknięciu aplikacji w trakcie gry** — zamiast stałej
  12-godzinnej blokady dzierżawa odnawiana przez urządzenie w grze (komputer co minutę,
  telefon co 2 minuty); bez odnowienia wygasa po 5 min (komputer) / 15 min (telefon).
  Telefon przy uruchomieniu aplikacji zdejmuje własną blokadę z niedokończonej gry.
- **Przejęcie profilu**: ekran „Profil gra teraz na urządzeniu …” ma opcję **Y — Przejmij
  profil i graj** (Windows i Android).
- Windows: blokada zdejmowana także wtedy, gdy start gry nie powiedzie się po jej założeniu
  (wcześniej zostawała).

## [0.25.6] — 2026-10-09

### Poprawione
- Android: pierwsza synchronizacja pliku różnego od serwera (np. świeża karta pamięci
  emulatora) **już przed grą** bierze wersję z serwera — wcześniej telefon zapamiętywał
  tylko stan serwera i po grze mógł nadpisać kartę z komputera swoją. Wersja z telefonu
  trafia do kopii zapasowej (na serwerze `_backup/…/konflikt`), z komunikatem.
- Android: Ustawienia → Zapisy gier pokazują przy folderze liczbę plików i godzinę
  ostatniej zmiany (i wszystkie znalezione foldery, gdy jest ich kilka) — widać, czy to
  folder, do którego emulator naprawdę zapisuje.

## [0.25.5] — 2026-10-09

### Dodane
- **Wygląd systemów z komputera na serwerze** — logo wybrane albo pobrane na PC, poświata
  i własna nazwa systemu idą na serwer EmuStart (przy starcie i po każdej zmianie), więc
  aplikacja na Androida pokazuje te same loga co Windows. **Serwer trzeba zaktualizować.**
- Android: **przeglądarka folderów padem** dla folderów zapisów i stanów (wystarczy wskazać
  folder danych emulatora, np. `ArmSX2`); X — wykrywanie automatyczne.
- Test spójności interfejsu: każda funkcja wołana przez interfejs musi istnieć na Windows
  i być obsłużona na Androidzie albo świadomie oznaczona jako tylko dla komputera.

### Poprawione
- Android: foldery `memcards` / `sstates` wykrywane w całej pamięci telefonu (do 3 poziomów),
  nie tylko w folderach pierwszego poziomu o nazwie emulatora — zapisy ArmSX2 nie szły na serwer.
- Android: **pierwsza synchronizacja nie nadpisuje pliku z serwera** (np. karty pamięci
  z komputera) — zostaje wersja z serwera, wersja z telefonu w kopii zapasowej.
- Android: menu w grze odpytywane w trakcie gry zwracało błąd co 80 ms; opisy i grafiki
  „na żądanie” oraz nieobsłużone funkcje odpowiadają teraz celowo (komunikat), nie pustką.

## [0.25.4] — 2026-10-09

### Poprawione
- Android: **loga platform** — serwer podaje też, czy logo potrzebuje jasnej poświaty
  (pobrane loga bywają czarne i na ciemnym tle były niewidoczne). **Serwer trzeba
  zaktualizować do 0.25.4.**
- Logo systemu, które się nie wczytało, zastępuje nazwa systemu (zamiast pustej karty).

## [0.25.3] — 2026-10-09

### Poprawione
- Android: na liście profili nie pojawia się folder pozostawiony po zmianie nazwy profilu
  na komputerze (z plikiem `moved.json`).

## [0.25.2] — 2026-10-09

### Dodane
- Android: **stany gry (savestate) z telefonu na serwerze** — `<profil>/states/android/<emulator>/`,
  osobno od stanów z PC. Przed grą stany tej gry z serwera, po grze zmienione na serwer
  (z kopią zapasową poprzedniej wersji); przetrwają reinstalację i zmianę telefonu.
  RetroArch (`<gra>.state*`), ArmSX2 i AetherSX2 (`.p2s` — nazwane numerem płyty, więc
  przypisanie do gry EmuStart poznaje po pierwszym zapisie stanu i trzyma też na serwerze).

## [0.25.1] — 2026-10-09

### Dodane
- Android: **PS1 w RetroArchu (SwanStation, Beetle PSX) z kartami DuckStation z komputera** —
  zapis `<gra>.srm` (surowa karta 128 KB) ↔ `<gra>_1.mcd` DuckStation na PC. DuckStation na
  Androidzie trzyma dane w niedostępnym `Android/data` i nie pozwala zmienić folderu —
  Ustawienia → Zapisy gier podpowiadają RetroArch dla PS1.

## [0.25.0] — 2026-10-09

### Dodane
- Android: **profile i zapisy gier wspólne z EmuStart na komputerze** (foldery profili na
  serwerze): wybór profilu przy pierwszym uruchomieniu i w menu Start, zapisy uruchamianej
  gry z serwera przed grą i na serwer po grze — RetroArch (`.srm` itd.), DuckStation (karty
  gry), ArmSX2 i AetherSX2 / NetherSX2 (karty PCSX2 `Mcd00x.ps2`). Konflikty: nowsza wersja,
  druga w kopii zapasowej. Blokada: ten sam profil nie gra naraz na telefonie i komputerze.
  Bez połączenia — kolejka wysyłek. Ustawienia → **Zapisy gier**: profil, foldery
  emulatorów (wykrywane, także po pierwszej grze), zaległe wysyłki.
- Android: **stały klucz podpisu** — kolejne wersje instalują się na poprzedniej.
  Jednorazowo trzeba odinstalować wersję sprzed 0.25.0.

## [0.24.0] — 2026-10-09

### Dodane
- **Zapisy, ustawienia emulatorów, blokada profilu i stany wznowienia przez serwer
  EmuStart** zamiast przez SMB (Ustawienia → Serwer EmuStart → „Zapisy i profile przez
  serwer”, domyślnie włączone):
  - pliki skompresowane zstd (pusta karta pamięci PS2 8 MB → kilka KB),
  - kopię zapasową nadpisywanej wersji robi serwer u siebie — przez SMB szła najpierw
    do komputera i z powrotem,
  - kilka plików naraz, jedno zapytanie zamiast wielu wymian SMB.
  Ten sam folder `Profiles` na NAS-ie: serwer znajduje go sam i oznacza znacznikiem;
  klient wybiera serwer tylko przy zgodnym znaczniku. Serwer niedostępny — SMB.
- **Przy starcie EmuStart wysyła wszystkie lokalne zapisy i ustawienia profili**, których
  nie ma na NAS-ie / serwerze (gra uruchomiona w tym czasie czeka na koniec wysyłki).
- Serwer: `/v1/nas/…` (lista, odczyt, zapis z kopią zapasową, usuwanie w folderze profili).
  **Serwer na komputerze z grami trzeba zaktualizować do 0.24.0.**

### Zmienione
- Pasek stanu: „⇅ wysyłam zapisy: …” (przez serwer albo NAS).

## [0.23.0] — 2026-10-09

### Dodane
- **Gry na Windows z serwera EmuStart** zamiast przez SMB — przez Tailscale na dużą
  odległość ok. 3× szybciej (pomiar: SMB ok. 25 MB/s, serwer z 8 strumieniami ok. 78 MB/s;
  SMB idzie jednym połączeniem TCP niezależnie od liczby strumieni).
  - Ustawienia → **Serwer EmuStart**: adres (podpowiedź z udziału sieciowego z grami),
    klucz (**Wklej klucz ze schowka**), **Sprawdź połączenie**, włącznik **Gry z serwera**.
  - 8 strumieni, kolejne brakujące bloki łączone w zapytania do 4 MB; granie w trakcie
    pobierania (WinFsp) czyta brakujące fragmenty z serwera.
  - Plik z serwera tylko przy zgodnym rozmiarze; serwer wyłączony, starszy, bez gry —
    SMB jak dotąd. NAS przez SMB niedostępny, a serwer działa — gra i tak się pobierze.
  - Ekran pobierania pokazuje źródło.
- Serwer: `GET /v1/find?es=&rel=` (gra po systemie i ścieżce), `features` w `/v1/info`.
  **Serwer na komputerze z grami trzeba zaktualizować do 0.23.0.**
- `ROADMAP.md` — plan przejścia na serwer EmuStart (gry, zapisy, profile, biblioteka)
  z pomiarami prędkości SMB i HTTP.
- `tools/httpbench.py` — pomiar odczytu z serwera EmuStart.

## [0.22.6] — 2026-10-09

### Zmienione
- Android: domyślnie **8 strumieni pobierania** zamiast 4. Pomiar przez Tailscale
  (ok. 1000 km, 33 ms) z serwerem 0.22.5: 8 strumieni ≈ 78 MB/s, 4 ≈ 21 MB/s.
  Kto zmienił liczbę w Ustawieniach, zachowuje swoją wartość.

## [0.22.5] — 2026-10-09

### Poprawione
- Serwer: **pobieranie gier z dalekiego serwera kilkukrotnie szybsze** — serwer
  odpowiadał w HTTP/1.0 i zamykał połączenie po każdym bloku, więc każdy blok
  zaczynał TCP od nowa. Teraz HTTP/1.1 z utrzymaniem połączenia (bezczynne
  zamykane po 2 minutach).

## [0.22.4] — 2026-10-09

### Zmienione
- Android: **kilka wersji tego samego emulatora** (np. RetroArch ze Sklepu Play
  i z pliku APK ze strony — różne pakiety) widać w Ustawieniach → Emulatory osobno,
  z numerem wersji i źródłem („Sklep Play” / „spoza Sklepu Play”). Domyślnie
  wybierana jest najnowsza.
- AetherSX2 opisany jako „AetherSX2 / NetherSX2” (ten sam pakiet).

## [0.22.3] — 2026-10-09

### Zmienione
- Android: **zrozumiałe komunikaty przy błędzie połączenia z serwerem** — zły klucz
  (serwer odpowiada, klucz nie pasuje), telefon nie łączy się (Tailscale wyłączony,
  serwer nie działa), nieznany albo niepoprawny adres; zawsze z adresem, którego
  aplikacja użyła.
- `EmuStart.exe --server-key` kopiuje klucz do schowka (do wklejenia i przesłania na
  telefon zamiast przepisywania 32 znaków).

## [0.22.2] — 2026-10-09

### Dodane
- **Obsługa samą myszą** (np. na komputerze z grami bez pada): podpowiedzi przycisków
  na dole ekranu i w oknach są klikalne — działają jak przyciski pada (Wybierz ten
  folder, W górę, Anuluj…).
- Wybór folderu: **Y — wpisz ścieżkę** z klawiatury (np. `D:\ROMS\No-Intro`).
- „Okno Windows (mysz)” do wyboru folderu działa także, gdy interfejs jest otwarty
  w przeglądarce.

## [0.22.1] — 2026-10-09

### Naprawione
- **Okno EmuStart nie startowało na innym komputerze** („Failed to resolve
  Python.Runtime.Loader.Initialize”) — Windows blokuje pliki rozpakowane z pobranego
  zipa, a .NET odmawia wczytania `Python.Runtime.dll`. EmuStart nie kończy się już
  błędem: otwiera interfejs w przeglądarce (z padem przez Gamepad API) i pokazuje,
  jak odblokować pliki (`Get-ChildItem -Recurse <folder> | Unblock-File`).
  „Wyjdź z EmuStart” w tym trybie kończy program.

## [0.22.0] — 2026-10-09

### Dodane
- **EmuStart na Androida** (`EmuStart-<wersja>-android.apk` w wydaniach, budowany
  automatycznie na GitHubie): ten sam interfejs co na Windows w WebView, sterowanie
  padem (GameSir itp., gałka, spusty), lista systemów i gier z grafikami i opisami
  z serwera EmuStart, pobieranie gry do telefonu w kilku strumieniach ze wznawianiem,
  „ostatnie gry + przypięte” w telefonie, praca bez serwera na ostatniej liście.
  Uruchamianie w emulatorach jak w ES-DE: ArmSX2/NetherSX2 (PS2), DuckStation, PPSSPP,
  Dolphin, Azahar, melonDS/DraStic, Flycast/Redream, M64Plus FZ, Eden, RetroArch
  z rdzeniem dla pozostałych systemów.
- Serwer: lista systemów podaje kod platformy i rdzeń RetroArcha.
- Wiki: „Aplikacja na Androida”.

## [0.21.1] — 2026-10-09

### Naprawione
- **Kwadratowa (albo szeroka) okładka zasłaniała szczegóły gry** w podglądzie:
  okładka i zrzut mają wspólną wysokość liczoną z proporcji obu obrazków tak, by
  szczegóły zawsze miały co najmniej 30% szerokości. Górne i dolne krawędzie obrazków
  dalej są równe, a ich góra — na wysokości logo platformy.

## [0.21.0] — 2026-10-09

### Dodane
- **Tryb serwera dla aplikacji na Androida** (`EmuStart.exe --server`, bez okna):
  na komputerze z grami udostępnia przez Tailscale listę systemów i gier
  z metadanymi i grafikami oraz pliki gier z obsługą `Range` (pobieranie kilkoma
  strumieniami, wznawianie). Dostęp z losowym kluczem serwera; interfejs
  administracyjny (`http://127.0.0.1:8740/index.html?dev`) tylko lokalnie; serwer
  wydaje wyłącznie pliki gier z biblioteki.
- `--install-server` — autostart razem z Windows (Harmonogram zadań) i reguła
  zapory; `--server-key` — adres i klucz serwera.
- Wiki: strona „Serwer dla Androida” z instrukcją instalacji.

## [0.20.0] — 2026-10-09

### Naprawione
- **Ręcznie zmieniona grafika (np. okładka) wracała do starej** po wyjściu z systemu
  i powrocie: przeglądarka trzymała grafikę w pamięci podręcznej pod tym samym
  adresem. Adres grafiki zawiera teraz wersję pliku — każda zmiana to nowy adres.

### Zmienione
- **Logo zaznaczonej gry zawsze w nagłówku listy**, obok logo platformy — niezależnie
  od ustawienia „Tytuły gier jako logo” (brakujące logo jest dociągane dla zaznaczonej
  gry).
- **Podgląd gry: okładka | szczegóły | zrzut**, a pod spodem opis na całą szerokość.
  Okładka i zrzut mają tę samą wysokość (wyrównane górą i dołem), a ich górna
  krawędź jest na wysokości górnej krawędzi logo platformy.
- Edytor wyglądu: „Logo systemu i gry nad listą” (wspólna wysokość); usunięte
  suwaki, które w nowym układzie nic nie zmieniały (logo w podglądzie, szerokość
  kolumny okładki).

## [0.19.2] — 2026-10-09

### Zmienione
- **Gra startuje od razu** — domyślnie zawsze granie w trakcie pobierania (próg
  „…gdy pobieranie potrwa dłużej niż” = 0; można ustawić inaczej).
- **Szybsze ładowanie w trakcie pobierania** (pomiar na grach PS2 z NAS-a > 1000 km,
  typowe ładowanie: nagłówek + 30 odczytów w różnych miejscach): **ok. 8 s zamiast
  ok. 18 s**:
  - bloki 1 MB zamiast 4 MB — gra czyta rozrzucone kawałki, więc większe bloki
    oznaczały pobieranie niepotrzebnych danych (ponad 2× mniej danych przy ładowaniu),
  - brakujący blok pobierany kilkoma kawałkami naraz,
  - gdy gra czyta, pobieranie w tle bierze tylko bloki, których gra potrzebuje
    (i 8 następnych); resztę pliku dokańcza, gdy gra przestanie czytać,
  - połączenia z plikiem na NAS-ie i początek pliku przygotowywane od razu, zanim
    emulator o nie poprosi.
- Pomiar sieci zapamiętywany na 30 min — bez 1,5 s czekania przy każdej grze.
- Niedokończone pobierania z poprzedniej wersji (mapa bloków 4 MB) są przeliczane,
  nie pobierane od nowa.

## [0.19.1] — 2026-10-09

### Zmienione
- **Granie w trakcie pobierania tylko przy długim pobieraniu**: dla mniejszych gier
  (do ok. 1 GB) samo pobranie w 4 strumieniach było szybsze niż start z dysku
  strumieniowego. EmuStart liczy teraz czas pobrania z pomiaru prędkości i gra
  w trakcie pobierania dopiero, gdy pobranie trwałoby dłużej niż próg (domyślnie
  45 s — przy ~250 Mb/s to ok. 1,4 GB). Ustawienia → „…gdy pobieranie potrwa dłużej
  niż” (0 = zawsze w trakcie pobierania).
- Zapas pobierania przed miejscem czytanym przez grę: 8 bloków (32 MB) zamiast 4.

## [0.19.0] — 2026-10-09

### Dodane
- **Pobieranie gier w 4 strumieniach naraz** (pliki od 32 MB), blokami po 4 MB
  z mapą pobranych bloków — przerwane pobieranie wznawia się od brakujących bloków.
  Zmierzone przez Tailscale (NAS > 1000 km): 268 Mb/s zamiast 110 Mb/s. Plik `.part`
  jest rzadki (NTFS), więc zapis bloku z końca pliku nie zapisuje zer przed nim.
- **Graj w trakcie pobierania (WinFsp)**: w trybie zdalnym gra startuje od razu
  z wirtualnego dysku tylko do odczytu (`emustart/vfs.py`, natywne API WinFsp przez
  ctypes, bez dodatkowych pakietów). Pobrane fragmenty idą z dysku lokalnego, brakujące
  są pobierane natychmiast poza kolejką (ok. 0,25 s na blok 4 MB, powtórny odczyt
  1 ms), a pobieranie w tle przesuwa się w miejsce, które gra czyta. Struktura plików
  jak na NAS-ie, więc `.cue`/`.m3u` działają. Ustawienia → „Graj w trakcie pobierania”.
- **Sprawdzenie WinFsp przy starcie** (log, Ustawienia). Bez WinFsp albo przy błędzie
  montowania — dotychczasowe zachowanie (ekran pobierania, „Graj teraz” z NAS-a).

## [0.18.1] — 2026-10-08

### Naprawione
- **Nie było jak wrócić do postępu pobierania grafik**: po wyjściu z ekranu postępu
  „Grafiki: pobierz brakujące” w opcjach systemu kończyło się komunikatem
  „Pobieranie już trwa”. Teraz otwiera ekran z postępem.
- Zbędne zapytania do Demozoo o produkcję „0” (Pouet podaje 0, gdy nie ma odnośnika).

### Dodane
- Wskaźnik pobierania grafik w górnym pasku na każdym ekranie („🖼 grafiki 120/900
  (13%)”), a w menu Start „Grafiki i metadane — trwa 13%”.

## [0.18.0] — 2026-10-08

### Dodane
- **Dema WHDLoad: dane z Pouet.net i Demozoo** — grupa, rok, party z miejscem
  w konkursie, typ produkcji, opis i zrzut ekranu. Grupa z nazwy paczki rozstrzyga
  między demami o tej samej nazwie („Megademo” Dragons ≠ „Megademo” Vision).
  Na próbce z kolekcji: 12/12 trafionych, wszystkie ze zrzutem.
- **Gry WHDLoad: metadane i grafiki** — LaunchBox (platforma Commodore Amiga)
  z dopasowaniem „Kings Quest 5” = „King's Quest V”, „Speedball 2” = „Speedball 2:
  Brutal Deluxe”; w ostateczności dane i grafiki tej samej gry z kolekcji zipów
  Amigi. Na próbce 300 gier: 273 trafione (wcześniej 0 — LaunchBox nie znał
  platformy tych systemów).

### Naprawione
- Dopasowanie w LaunchBox sprawdza numer części: „Back to the Future Part 3” nie
  trafia już w „Part II”, „Might & Magic 3” w „Might and Magic II”, a dopasowanie po
  początku tytułu wymaga podtytułu („Archon” ≠ „Archon II”, „A Train” ≠ „Trains”).

### Zmienione
- Jednorazowo przy starcie: grafiki i metadane dem WHDLoad przypisane wcześniej po
  nazwie (z baz gier) są usuwane i pobierane od nowa z Pouet/Demozoo; gry WHDLoad
  oznaczone jako „bez grafiki/opisu” są wyszukiwane ponownie.

## [0.17.3] — 2026-10-08

### Naprawione
- **Długie czekanie przed startem gry** (także gry już lokalnej, w logu do 60 s):
  przed każdą grą EmuStart porównywał save'y profilu z NAS-em, sprawdzając każdy
  plik osobnym zapytaniem przez sieć — dla RetroArcha (np. dysk WHDLoad z PUAE)
  samo przejście po folderze trwało 27 s. Teraz:
  - lista plików z rozmiarami i datami pobierana jednym przejściem (27 s → ok. 3 s),
  - znacznik zmian na NAS-ie: jeśli od ostatniej synchronizacji nikt nic nie
    wysłał, porównanie jest pomijane (ok. 0,2 s); pełne porównanie raz na dobę
    i zawsze, gdy inny komputer coś wysłał,
  - po grze wysyłane są tylko zmienione pliki, bez listowania NAS-a.
- **RetroArch startował zminimalizowany**: EmuStart dawał fokus tylko pierwszemu
  oknu emulatora, a RetroArch przy ładowaniu rdzenia tworzy okno pełnoekranowe od
  nowa — bez fokusu Windows je minimalizował. Teraz okno emulatora jest pilnowane,
  aż przez 3 s utrzyma się na wierzchu (najwyżej 25 s od startu).

### Dodane
- W logu czas od wybrania gry do startu emulatora (i ile z tego zajęła
  synchronizacja profilu) — do diagnozy.

## [0.17.2] — 2026-10-08

### Naprawione
- **Klawiatura w grach na komputery (Amiga, C64, MSX, Atari ST, ZX…) przez
  RetroArch**: prawy Shift otwierał klawiaturę ekranową PUAE (RetroArch przypisuje
  go do Select pada), strzałki działały jak joystick, a F1 i Esc przechwytywał
  RetroArch (menu, wyjście) — np. w Pinball Dreams nie dało się grać. Na czas sesji
  tych systemów EmuStart włącza „game focus” (cała klawiatura dla emulowanego
  komputera, skróty RetroArcha wyłączone) i zdejmuje przypisania klawiszy do pada.
  Pad działa bez zmian; wyjście z gry: menu EmuStart (A + Y), Scroll Lock przełącza
  game focus. `retroarch.cfg` pozostaje bez zmian.

## [0.17.1] — 2026-10-08

### Naprawione
- **Gry WHDLoad: „DOS-Error #205 (object not found) on reading
  devs:kickstarts/kick34005.a500”** — rdzeń PUAE zakłada emulowany dysk WHDLoad
  w `saves\PUAE\WHDLoad` i wkłada do `Devs\Kickstarts` tylko pliki `.RTB`, bez
  samych ROM-ów. EmuStart dokłada tam teraz Kickstarty (z folderu `system`
  RetroArcha albo z folderu BIOS-ów) przed startem gry WHDLoad.

## [0.17.0] — 2026-10-08

### Dodane
- **Amiga: obrazy `.ipf`** (No-Intro). Gra na kilku dyskietkach w jednym zipie
  dostaje playlistę `.m3u` (zmiana dyskietki w RetroArchu: Sterowanie dyskami).
  Przy pierwszej grze EmuStart proponuje pobranie **capsimg** (biblioteka `.ipf`
  dla rdzenia PUAE) do folderu `system` RetroArcha; brak ROM-ów Kickstart
  sygnalizuje ostrzeżeniem (rdzeń użyje wtedy AROS).
- **Amiga WHDLoad — dwa nowe systemy: gry i dema** (`.lha`/`.lzx`). Rozpoznawane
  podfoldery `Games`/`Demos` folderu `WHDLoad` oraz typowe nazwy zestawów
  („WHDLoad Games”, „Commodore Amiga - WHDLoad - Demos”…). Nazwy paczek zamieniane
  na tytuły z oznaczeniami: „Burntime (v1.2) (AGA)”, w demach z grupą —
  „Vector Balls (v1.0) (Hypnosis)”.

- Ustawienia → **BIOS-y**: folder z BIOS-ami (np. `bios` z RetroBat). Przed startem
  gry na RetroArchu brakujące BIOS-y rdzenia (lista z jego pliku `.info`, np.
  Kickstarty dla PUAE) kopiowane są do folderu `system` RetroArcha; dla Amigi także
  `capsimg.dll`. Istniejące pliki nie są nadpisywane.

### Zmienione
- Doinstalowanie dodatku (np. capsimg) nie zmienia już emulatora przypisanego
  do systemu.

## [0.16.5] — 2026-10-08

Przegląd bezpieczeństwa i wycieku danych.

### Bezpieczeństwo
- **Hasła i tokeny nie trafiają już do kopii ustawień profilu** (lokalnie, na
  NAS i w `_backup`): token RA z DuckStation (w trybie przenośnym możliwy do
  odszyfrowania), hasło/token RA i hasła netplay z `retroarch.cfg`, token
  z `PCSX2.ini`. Przy wgrywaniu ustawień zostają wartości z danego komputera.
  Kopie zapisane wcześniej czyszczone są jednorazowo przy starcie.
- Lokalny serwer UI sprawdza nagłówek `Host` (ochrona przed DNS rebinding —
  strona z internetu nie odczyta okładek ani UI przez 127.0.0.1); API trybu
  deweloperskiego wymaga własnego nagłówka (bez niego zwykła strona mogłaby
  wywoływać funkcje programu, gdy działa `--browser`).
- Interfejs z polityką CSP (tylko własne skrypty) — dodatkowa ochrona, gdyby
  opis gry z internetu zawierał HTML.
- Nazwy plików z indeksu wznowień na NAS są sprawdzane (wcześniej spreparowany
  indeks mógł wskazać plik poza folderem `resume`).
- Sekret IGDB wysyłany w treści zapytania zamiast w adresie URL.
- Log nie zapisuje tytułów cudzych okien (bywa w nich nazwa dokumentu czy
  temat maila) — tylko klasę okna i nazwę programu.
- Foldery sesji po awarii (wypakowane gry, konfiguracja sesji RetroArcha
  z tokenem RA) usuwane przy starcie.

### Naprawione
- Konto RA (`retroachievements.json`) nie trafiało na NAS, gdy NAS był w tym
  momencie niedostępny — drugi komputer nie dostawał konta. Teraz dosyłane przy
  następnym odczycie.
- „Permission denied” przy zapisie ustawień profilu (dwa wątki naraz) — zapis
  atomowy, pod blokadą, z ponowieniem.

## [0.16.4] — 2026-10-08

### Naprawione
- **„Gra uruchomiona” wisiało kilkanaście–kilkadziesiąt sekund po wyjściu
  z gry**: EmuStart czekał, aż ustawienia i save'y dojdą na NAS (w logu do 58 s).
  Teraz ekran wraca od razu, a synchronizacja idzie w tle — w górnym pasku
  widać „⇅ zapisuję save'y na NAS: <gra>”. Następna gra poczeka na jej koniec
  („Kończę zapis poprzedniej gry na NAS…”), zamknięcie EmuStart też (do 2 min;
  czego nie zdąży wysłać, wyśle przy następnym uruchomieniu).

## [0.16.3] — 2026-10-08

### Naprawione
- **DuckStation prosił o zalogowanie do RetroAchievements**: DuckStation trzyma
  token zaszyfrowany (AES-128-CBC, klucz z nazwy użytkownika i — poza trybem
  przenośnym — identyfikatora komputera), a EmuStart wpisywał mu zwykły token
  (taki jak w PCSX2 i RetroArch), którego DuckStation nie umiał odczytać. Token
  jest teraz szyfrowany w formacie DuckStation przy zapisie i odszyfrowywany przy
  przejmowaniu konta z DuckStation (szyfrowanie przez Windows CNG, bez
  dodatkowych bibliotek).

## [0.16.2] — 2026-10-08

### Naprawione
- **Gry MSU-1 (SNES z muzyką CD) bez muzyki**: RetroArch dostawał zip i sam
  wypakowywał z niego tylko ROM, więc pliki `.msu` i ścieżki `.pcm` zostawały
  w archiwum. Zip z więcej niż jednym plikiem jest teraz wypakowywany w całości
  do pamięci (folder sesji, sprzątany po grze), także dla RetroArcha.
- Archiwum bez pliku gry (np. sama muzyka MSU-1 bez ROM-u) daje czytelny
  komunikat zamiast nieudanego startu emulatora.

## [0.16.1] — 2026-10-08

### Naprawione
- **Nieczytelne ustawienia po powiększeniu listy gier**: wysokość wiersza listy
  gier zmieniała też odstępy w Ustawieniach, „Grafikach i metadanych” i „Kolejności
  padów”. Teraz te ekrany mają własną wysokość wiersza (edytor wyglądu →
  „Wysokość wiersza w ustawieniach”, domyślnie jak dawniej).
- Nagłówki sekcji w ustawieniach zajmują całą szerokość zamiast łamać się
  w wąskiej kolumnie.

### Zmienione
- Suwak „Menu i okna” → „Menu, okna i ustawienia”: skaluje też czcionkę ekranów
  ustawień, grafik, padów i profili (niezależnie od listy gier).

## [0.16.0] — 2026-10-08

### Dodane
- Opcje profilu (Start na karcie) → **„Folder na NAS: … → zmień na …”**, gdy
  folder profilu na NAS ma inną nazwę niż profil (np. profil przemianowany
  z „Gracz”). Folder jest przenoszony, a w starym miejscu zostaje `moved.json`
  ze wskazaniem nowego — inne komputery przepinają się same przy następnej
  synchronizacji, bez duplikatów i rozjechanych save'ów. Odmawia, gdy profil
  właśnie gra na innym komputerze, NAS jest niedostępny albo folder o nowej
  nazwie już istnieje.

## [0.15.1] — 2026-10-08

### Zmienione
- **Ukrywanie gier osobno dla każdego profilu** (Beta/Proto, dema, pirackie,
  nielicencjonowane, programy/BIOS, klony arcade) — zapisywane w `emustart.json`
  profilu (lokalnie i na NAS), więc działa tak samo na każdym komputerze.
  Profil, który jeszcze tych ustawień nie ma, widzi wszystko (klony arcade ukryte).

## [0.15.0] — 2026-10-08

### Dodane
- Ustawienia → **Ukrywanie gier** (wg oznaczeń w nazwach No-Intro/Redump/MAME),
  każda grupa osobno:
  - wersje rozwojowe — Beta, Proto/Prototype, Possible Proto, Alpha, Debug,
    location test,
  - dema i wersje promocyjne — Demo, Kiosk, Promo, Sample, Trial, Preview,
  - pirackie i przeróbki — Pirate, hack, bootleg,
  - nielicencjonowane — Unl, Aftermarket, Homebrew,
  - programy i BIOS-y.
  Liczba gier w karuzeli uwzględnia ukryte (system bez widocznych gier znika).
  Rewizje (Rev, Alt) zostają — to pełne wersje gier.

### Zmienione
- „Ukryj klony arcade” przeniesione do sekcji Ukrywanie gier („Klony arcade”).

## [0.14.1] — 2026-10-08

### Naprawione
- **Te same gatunki pod różnymi nazwami** w filtrach i podglądzie (źródła
  metadanych nazywają je różnie): „RPG”, „Role-Playing” i „Role playing games”
  to teraz jeden gatunek RPG; podobnie Fighting (Fight, Fighter), Racing (Race,
  Driving), Beat 'em Up (trzy pisownie), Puzzle (Puzzle-Game, Thinking), Board
  Game, Cards, Casino, Music, Sports (dyscypliny), Breakout, Horror i inne.
  Wpisy złożone („Racing / Driving”) dzielone na części, „N/A” pomijane.
  Metadane w bazie zostają bez zmian — ujednolicenie przy wyświetlaniu.

## [0.14.0] — 2026-10-08

Kilka komputerów, jeden NAS.

### Dodane
- **„Kto gra na tym komputerze?”** przy pierwszym uruchomieniu (nowa instalacja):
  wybór profilu z NAS albo nowego. Do niego — a nie do pierwszego z listy —
  trafiają save'y i konto RetroAchievements zastane w emulatorach na tym
  komputerze. Aktualizacja ze starszej wersji nie pyta (właścicielem zostaje
  ostatnio grający profil). Zmiana: Ustawienia → Profile albo Start na profilu →
  „Ustaw jako profil tego komputera”.
- **Pytanie „Kto gra?” przy starcie** do wyłączenia (Ustawienia → Profile) — wtedy
  od razu startuje profil tego komputera.
- **Kopie zapasowe na NAS**: plik save'a albo ustawień nadpisywany na NAS trafia
  najpierw do `Profiles\<profil>\_backup\<czas>-<komputer>\` (10 ostatnich).
- **Wykrywanie konfliktów**: gdy ten sam save zmienił się na dwóch komputerach
  (gra bez dostępu do NAS), zostaje nowsza wersja, druga ląduje w kopii
  zapasowej (`konflikt`), a EmuStart pokazuje komunikat.
- **Wznawianie na innym komputerze**: „quicksave i wyjdź” zapisuje stan także
  w profilu na NAS (`resume\`); drugi komputer go wczyta, a stan zużyty na jednym
  komputerze znika też na pozostałych.
- Edytor wyglądu → „Wygląd zapisywany”: **dla profilu** (ten sam na każdym
  komputerze) albo **dla tego komputera**.

### Zmienione
- Zmiana nazwy profilu, który nic jeszcze nie wysłał na NAS, zmienia też jego
  folder na NAS (o ile nazwa nie jest zajęta). Profilom z danymi folder zostaje.
- Konto RA zastane w emulatorach jest przypisywane dopiero, gdy wiadomo, czyj
  jest komputer.

## [0.13.0] — 2026-10-08

### Dodane
- **Ustawienia emulatorów osobno dla każdego profilu** (RetroArch, DuckStation,
  PCSX2): przed grą wgrywane są ustawienia profilu, po grze zapisywane z powrotem
  do profilu i na NAS (`Profiles\<profil>\settings\<emulator>`). Wartości
  zależne od komputera (ścieżki, BIOS, karta grafiki, urządzenie audio) zostają
  z danego komputera. Nowy profil zaczyna od bieżących ustawień. Można wyłączyć:
  Ustawienia → Profile.
- **Ustawienia EmuStart osobno dla profilu**: wygląd, tytuły jako logo, ukrywanie
  klonów arcade (`emustart.json` w profilu i na NAS, nowszy wygrywa).
- **RetroAchievements dla każdego profilu**: Start na karcie profilu → logowanie
  (hasło idzie tylko do retroachievements.org, zapisywany jest token), wylogowanie,
  tryb hardcore, „użyj konta z emulatora”. Przed każdą grą konto profilu trafia do
  RetroArch (konfiguracja sesji), DuckStation i PCSX2 (`secrets.ini`); profil bez
  konta wyłącza osiągnięcia. Konto zalogowane już w emulatorach przejmuje
  pierwszy profil.
- **Czysta instalacja EmuStart**: profile z `Z:\emustart\Profiles` same wracają na
  listę; save'y, ustawienia i konto RA ściągają się przy pierwszej grze.
- Grafiki i metadane → **„Zmniejsz zapisane grafiki”**: WebP w rozmiarze ekranowym,
  zwykle ok. 8× mniej miejsca.
- Klawiatura ekranowa: wiersz symboli i ukrywanie hasła.

### Zmienione
- Nowe grafiki zapisywane od razu zmniejszone (okładki do 900 px, logo do
  1000×400, zrzuty do 960×720, WebP).
- Foldery save'ów DuckStation i PCSX2 czytane z ich ustawień (było na sztywno).
- RetroArch dostaje w każdej sesji folder save'ów i stanów wprost — save nie
  trafi obok gry w RAM-ie / pamięci podręcznej, nawet przy „save obok gry”.
- Profil wgrywany jest przed wyliczeniem parametrów startu gry.

### Naprawione
- **Pobieranie grafik zapełniało dysk do zera**: zatrzymuje się z komunikatem,
  gdy zostaje mniej niż 2 GB; puste pliki `.tmp` po nieudanym zapisie są usuwane.
- Emulator zainstalowany od nowa: jego świeża (pusta) karta pamięci nie trafia już
  obok save'a profilu z dopiskiem w nazwie, tylko do kopii zapasowej profilu.

## [0.12.0] — 2026-10-07

### Dodane
- **Edytor wyglądu** (menu Start → „Wygląd”, także w Ustawieniach): panel
  z boku ekranu, zmiany widoczne od razu na karuzeli systemów albo liście gier
  (A przełącza ekran pod spodem, Y przesuwa panel na drugą stronę).
  ←/→ zmienia wartość, LB/RB po 5 kroków, X przywraca domyślną, Start dwa razy
  przywraca wszystko, B zapisuje (config.json → `look`).
  - **Logo:** wysokość i szerokość logo tytułu na liście, logo w podglądzie,
    wielkość logo systemów w karuzeli i odstęp między nimi, logo nad listą gier.
  - **Lista i podgląd:** wysokość wiersza, szerokość listy, wysokość okładki
    i zrzutu, szerokość kolumny okładki, liczba linii opisu.
  - **Czcionki osobno:** cały interfejs, lista gier, tytuł gry, metadane, opis
    gry, nazwa systemu, informacje o systemie, menu i okna, paski górny i dolny.
  - **Kolory:** kolor akcentu (8 do wyboru) i tło (6 wariantów).
  - Przełącznik „Tytuły gier jako logo”.

### Zmienione
- Logo tytułów gier domyślnie większe: na liście 92% wysokości wiersza (było
  ok. 75%) i do 75% szerokości, w podglądzie do 16% wysokości ekranu (było 10%).

## [0.11.0] — 2026-10-07

### Naprawione
- **Pad psuł się po zamknięciu programu** (wyłączał się i nie dało się go
  włączyć do restartu Windows; przy działającym programie nie działał w innych
  aplikacjach). Ograniczony do minimum kontakt programu z padami:
  - interfejs nie używa już Gamepad API w WebView2 (otwierało pady przez
    Windows.Gaming.Input) — pady obsługuje wyłącznie EmuStart przez XInput,
  - XInput jest odpytywany tylko, gdy okno EmuStart jest na wierzchu; w tle
    (gra, inny program) program nie dotyka padów wcale,
  - zapytanie o baterię (jedyne idące radiowo do pada) tylko raz przy wejściu
    na ekran „Kolejność padów” i przy starcie gry w trybie „bezprzewodowe
    pierwsze” — dotąd 5 razy na sekundę na tym ekranie i przy każdej grze.

### Dodane
- Ustawienie **„Obsługa padów w menu”**: EmuStart/XInput (domyślnie),
  przeglądarka (Gamepad API) albo wyłączona (tylko klawiatura). Flaga startowa
  `--pady=python|przegladarka|brak` — do szybkiego sprawdzenia, czy problem
  z padem wraca w danym trybie. Wybrany tryb zapisuje się w logu przy starcie.

### Zmienione
- **Podgląd gry 2×2**: okładka po lewej i zrzut ekranu dosunięty do prawej
  krawędzi (u góry), metadane pod okładką, opis pod zrzutem (więcej miejsca
  na opis).

## [0.10.0] — 2026-10-07

### Dodane
- **Opis platformy w karuzeli systemów** — wyśrodkowany blok (~55% szerokości)
  pod nazwą systemu: producent, lata produkcji (np. 1994–2006) albo rok
  premiery, nośnik, liczba padów i opis.
  - Dane platform z LaunchBoksa (`Platforms.xml`: producent, premiera, nośnik,
    procesor, pamięć, pady, opis) — pobierane fragmentem archiwum (~75 KB
    zamiast 108 MB), także do istniejących baz.
  - Lata produkcji i producent z Wikidaty, opis po polsku z Wikipedii (gdy brak
    — angielski, a na końcu opis z LaunchBoksa). Wybierany jest najbardziej
    pasujący artykuł (dokładna zgodność nazwy wygrywa — SNES ≠ NES,
    Atari 2600 ≠ Atari 2600+). Systemy arcade opisane jako automaty.
  - Pobierane raz, w tle; oglądany system ma pierwszeństwo przed kolejką.

### Zmienione
- Zapytania do Wikipedii z opisowym nagłówkiem User-Agent (adres projektu) —
  ogólny nagłówek Wikipedia odrzucała przy serii zapytań (HTTP 429).

## [0.9.0] — 2026-10-07

### Dodane
- **Baza LaunchBox** (Start → Grafiki i metadane → „Pobierz/Aktualizuj bazę
  LaunchBox”, 108 MB): 189 tys. gier z opisem, rokiem, producentem, wydawcą,
  gatunkami i liczbą graczy oraz 1,3 mln grafik w kategoriach. Zamieniana na
  lokalną bazę `data/launchbox.db` — potem wszystko działa offline. Sety arcade
  dopasowywane po nazwie setu (Mame.xml). Dopasowanie na Twojej kolekcji:
  94–99% gier (próbki po 300), 0,4 ms na grę.
- **„Pobierz brakujące metadane i opisy”** (wszystkie systemy) oraz wariant
  „… oraz Wikipedia dla gier bez opisu”; dla pojedynczego systemu A pobiera
  grafiki i metadane naraz. Liczniki opisów przy każdym systemie.
- Metadane z LaunchBoksa wchodzą też przy wejściu do systemu (razem z bazą
  RetroArcha) — filtry gatunku/graczy działają także tam, gdzie RetroArch nie
  ma danych. Dane z RetroArcha (No-Intro/Redump) mają pierwszeństwo.
- **LaunchBox jako źródło grafik** — w automacie (zaraz po libretro) i w ręcznym
  wyborze (kategorie: Box - Front, Screenshot - Gameplay, Clear Logo…).
- **Tytuły gier jako logo** (Ustawienia → Wygląd → „Tytuły gier jako logo”):
  lista gier i podgląd pokazują Clear Logo zamiast tekstu, gdy jest. Logo
  dociągają się w tle dla widocznych gier i w narzędziu „Grafiki i metadane”;
  ręczny wybór w opcjach gry („Logo gry”) — LaunchBox i SteamGridDB.

### Zmienione
- Narzędzie „Grafiki” nazywa się teraz „Grafiki i metadane”.

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
