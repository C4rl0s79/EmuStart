# Specyfikacja: plakaty gier w EmuStart (do coverflow w Lumenie)

Status: **szkic do akceptacji** · 2026-11-02 · dotyczy: EmuStart Server, klient EmuStart (Windows), APK, Lumen (`engine/` + coverflow TV)

## Problem

W coverflow trybu TV Lumena (`TvCoverflow.tsx`, motywy z `games.layout = "coverflow"`) gry Steam mają
plakaty 2:3, a gry EmuStart pudełka w kształcie z ich systemu: poziome pudełka SNES/N64, kwadratowe
pudełka CD, longboxy 3DO. Wstęga jest przez to nierówna, a gry z emulatorów wyglądają gorzej niż gry Steam.
EmuStart zna dziś tylko trzy rodzaje grafik (`box`, `snap`, `logo`), więc Lumen nie ma skąd wziąć
plakatu, który byłby taki sam na każdym urządzeniu. Jedyna droga to własne wyszukiwanie Lumena w
SteamGridDB: osobne na każdym komputerze, wymaga klucza Lumena, a admin nie może poprawić wyniku na serwerze.

Dodatkowa komplikacja: rodzaj `box` jest już mieszanką. Źródło nr 3 łańcucha okładek
(`art_sources.Sources.find`) to siatki SteamGridDB 600x900, więc część „okładek” to w praktyce plakaty.
Nie da się odróżnić jednych od drugich.

### Miara: odchylenie od 2:3

Obrazu **nie przycinamy**. Grafika o proporcjach bliskich 2:3 jest przy wyświetlaniu lekko rozciągana albo
ściskana do dokładnie 2:3. Odchylenie to `1 − min(r, ⅔) / max(r, ⅔)`, gdzie `r` = szerokość / wysokość,
czyli o ile procent zmienia się jeden bok. **Granica: 18%**, co odpowiada proporcjom ≈ 0,55–0,81. Przykłady:

| Grafika | Proporcje | Odchylenie |
|---|---|---|
| SteamGridDB 600x900 | 0,667 | 0% |
| SteamGridDB 660x930 / 342x482 | 0,71 | 6% |
| PS2, GameCube, Wii, Mega Drive (pudełko DVD/Mega Drive) | 0,70–0,71 | 5–6% |
| N64 Japonia | 0,72 | 7% |
| PSP (UMD) | 0,58 | 13% |
| longbox 3DO (`Fanart - Box - Front`) | 0,56 | 16% |
| Lynx | 0,80 | 17% |
| Super Famicom | 0,55 | 17,5% |
| PSX (każdy region) | 1,00 | 33%: **nie jest plakatem** |
| SNES USA/EU | 1,37 | 51%: **nie jest plakatem** |

### Pomiar (2026-11-02)

**Kształt obecnych okładek:** 4398 okładek `box` z kopii mediów w `LumenData` (62 systemy, głównie libretro i
LaunchBox „Box - Front”), z granicą odchylenia 18%.

| Grupa | Systemy (odsetek okładek, które mogą być plakatem) |
|---|---|
| **Okładka wystarczy** | PS2, GameCube, Wii, Xbox 360, Jaguar, Atari 2600/800, Intellivision, Vectrex (100%), Supervision (98%), Master System (98%), Mega Drive (97%), PSP (96%), Saturn (95%), C64 (95%), ColecoVision (94%), FBNeo (93%), 32X (90%), ZX Spectrum (86%), NES (76%) |
| **Okładka nie wystarczy** | PSX (3%, mediana 1,00), SNES (26%, 1,41), N64 (29%, 1,37), GBA (0%), GB/GBC (≤17%), Dreamcast (0%), PC Engine (2%), Lynx (3%), 3DO (2%), Neo Geo Pocket (0%) |
| Razem | **2657 z 4398 (60%)** okładek może być plakatem |

**Baza LaunchBoksa na serwerze:** 1,34 mln grafik. Typ „Poster” ma tylko **1571**, „Advertisement Flyer -
Front” (ulotki, zwykle pionowe) **28 164**. libretro ma jedną okładkę na grę (tę, którą już mamy), więc nowego
plakatu nie doda. IGDB tylko kadruje te same kwadratowe i poziome pudełka do 3:4 i szuka po tytule, czyli najmniej pewnie.

**LaunchBox według systemów:** 30 losowych gier na platformę, pobrane nagłówki **wszystkich** ich grafik „Box - Front”,
„Box - Front - Reconstructed”, „Fanart - Box - Front”, „Poster” i „Advertisement Flyer - Front” ze wszystkich regionów
(`images.launchbox-app.com`). Tabela pokazuje odsetek gier z co najmniej jedną grafiką o odchyleniu ≤ 18%.

| Platforma LaunchBox | Bez ulotek | Z ulotkami | Skąd plakat (odchylenie) |
|---|---|---|---|
| Sony PSP | **100%** | 100% | pudełko UMD 0,58 (13%), każdy region |
| Atari Lynx | **77%** | 77% | pudełko 0,80 (17%) |
| 3DO | **67%** | 67% | longbox `Fanart - Box - Front` 0,56 (16%) |
| SNES | **47%** | 57% | **japońskie** pudełko Super Famicom 0,55 (17,5%). USA/EU poziome (1,37) |
| Nintendo 64 | **47%** | 53% | **japońskie** pudełko 0,70–0,75 (5–11%). USA/EU poziome (1,43) |
| Sega Saturn | 37% | 53% | pudełka USA/EU (długie). Japońskie kwadratowe |
| GB, GBC | 27–30% | 37% | część pudełek 0,8 |
| Dreamcast | 23% | 30% | głównie `Fanart - Box - Front` i ulotki |
| GBA, PC Engine/TG-16, NGPC | 10–20% | 10–20% | pojedyncze. Pudełka 0,9–1,0 |
| **Sony PlayStation** | **0%** | 30% | **brak.** Okładki są prawie dla każdej gry, ale kwadratowe we wszystkich regionach (48 z 52 w 0,9–1,1, mediana 1,00). Pionowe są tylko ulotki |

Wnioski: LaunchBox daje plakaty dla PSP, Lynxa i 3DO. Dla SNES i N64 daje je tylko z wydań japońskich, a
potrzebne są głównie wydania EU/US, więc tam (i dla PSX, GBA, PC Engine) główne źródło to SteamGridDB.
Japońskie pudełko jest zapasem.

## Decyzje (ustalone z użytkownikiem)

| Pytanie | Decyzja |
|---|---|
| Kiedy coverflow pokazuje plakat | **Coverflow zawsze bierze plakat**, a gdy go nie ma, pudełko. Lista TV zostaje przy pudełkach |
| Skąd serwer bierze plakaty | **Automatycznie w tle** (jak okładki) **+ ręczny wybór** admina w panelu serwera |
| Kolejność źródeł | Dla gier retro **LaunchBox i libretro mają pierwszeństwo przed IGDB** (pewne dopasowanie po nazwie No-Intro i id LaunchBoksa zamiast szukania po tytule). Po pomiarze IGDB całkiem wypada z łańcucha plakatów |
| **Format przed źródłem** | **Wygrywa grafika najbliższa 2:3, niezależnie od źródła.** Źródło i region rozstrzygają dopiero remis (S3) |
| Granica odchylenia | **18%** (zostaje). Grafiki z wydań EU/US mają pierwszeństwo przy remisie, japońskie są na końcu (S3) |
| **Bez przycinania** | **Obrazu nie przycinamy.** Coverflow lekko zmienia proporcje wyświetlanej grafiki do dokładnie 2:3, gdy odchylenie ≤ 18%. Przy jednym formacie odchylenia są małe, więc po przeskalowaniu wygląda to dobrze (L3) |
| Styl siatek SteamGridDB, wyjątki | Bez dodatkowego filtra stylu (domyślne filtry API). **Nietrafione plakaty admin poprawia ręcznie** w panelu (S7) |
| Pobieranie plakatów w tle (O1) | **Włączone domyślnie**, bez nowego ustawienia |
| Aktualizacja silnika Lumena (O5) | **Wydajemy wszystko razem:** silnik Lumena (dziś 0.54.0) dostaje przez `subtree pull` wszystko z EmuStart do wersji z plakatami (także `pady-fantomy` i 0.54.1–0.54.2), a wydanie Lumena zawiera to razem z plakatami i motywami TV |
| Kolejność pracy | **Najpierw `pady-fantomy`** (kończy użytkownik, razem z PR #103), potem plakaty w EmuStart od nowego `main`. Gałąź Lumena `lightbox` **scalona do `main`** 2026-11-02 (PR #1, fast-forward) |
| Czy UI EmuStart pokazuje plakaty | **Nie.** EmuStart przechowuje, synchronizuje i udostępnia plakaty, a wygląd listy gier EmuStart (Windows, APK) się nie zmienia |

## Cele

1. **Spójna wstęga:** gra EmuStart w coverflow stoi jako 2:3, tej samej wielkości co gry Steam. Cel: systemy z
   pionowymi pudełkami (PS2, GC, Wii, MD, SMS, PSP, Saturn…) ≥ 95% od razu, z samych okładek. SNES i N64
   ≥ 70% (głównie SteamGridDB, japońskie pudełko jako zapas). PSX i GBA zależą w całości od SteamGridDB
   (LaunchBox ma dla nich tylko kwadratowe okładki): cel ≥ 60%.
2. **Jedno źródło prawdy:** plakat ustawia serwer, a każde urządzenie z Lumenem pokazuje ten sam.
   Zmiana zrobiona przez admina dociera po odświeżeniu listy, tak jak dziś okładki.
3. **Zero regresji:** okładki (`box`), zrzuty, logo i lista gier EmuStart wyglądają i działają jak przed zmianą.
   Starsze urządzenia współpracują z nowym serwerem bez zmian.
4. **Bez ręcznej pracy:** plakaty pobierają się same w tle. Ręczny wybór służy do poprawek wyjątków, nie jest warunkiem działania.

## Poza zakresem (v1)

- **Plakaty w UI EmuStart** (podgląd gry, lista na Windows/APK, `web/options.js`). Decyzja użytkownika, a do tego zasada „bez przy okazji” z `CLAUDE.md`.
- **Plakaty na liście TV i w widoku desktopowym Lumena.** Lista TV zostaje przy pudełkach. Desktop ma już swój łańcuch plakatów (`emuArt`) i go nie ruszamy.
- **Porządkowanie rodzaju `box`** (wyłączenie z niego siatek SteamGridDB). To zmieniłoby okładki, które użytkownicy już widzą. Ewentualnie osobna zmiana później (P2).
- **Przycinanie i plakaty z pudełek kwadratowych i poziomych** (np. PSX 1:1, SNES USA 1,37), także przez rozmyte tło. Grafika z odchyleniem > 18% nie jest plakatem, a coverflow pokazuje wtedy pudełko w jego własnym kształcie.
- **Wybór plakatu per gra w samym Lumenie** dla coverflow, ponad wybór serwera. Istniejący wybór TV „Grafika” zostaje, patrz pytanie O3.

## Historie użytkownika

**Gracz na kanapie (Lumen, tryb TV, motyw z coverflow)**
- Chcę, żeby gry z emulatorów stały we wstędze jako plakaty tej samej wielkości co gry Steam, bo wstęga ma wyglądać spójnie.
- Chcę widzieć całą grafikę, bez uciętego tytułu czy logo platformy, bo to po nich rozpoznaję grę.
- Gdy gra nie ma plakatu, chcę zobaczyć jej pudełko, a nie pustą ramkę, żeby zawsze było wiadomo, co wybieram.
- Gdy grę mam w kilku wersjach (grupa `emugrp:`), chcę jeden plakat dla całej grupy, żeby wstęga nie zmieniała się przy przełączaniu wersji.

**Admin serwera EmuStart**
- Chcę, żeby serwer sam pobrał plakaty dla całej biblioteki, bo nie będę wybierał ich ręcznie dla tysięcy gier.
- Chcę podmienić albo usunąć zły plakat w panelu gry (tak jak okładkę), żeby poprawka trafiła do wszystkich urządzeń.
- Chcę widzieć w „Grafikach” liczbę plakatów na system, żeby wiedzieć, gdzie brakuje.
- Bez klucza SteamGridDB nie chcę, żeby gry dostały trwałe „brak plakatu”. Po dodaniu klucza mają się pobrać.

**Gracz na starszym urządzeniu (EmuStart PC/APK sprzed zmiany)**
- Chcę, żeby wszystko działało jak wcześniej, gdy serwer ma już plakaty, bo nie każde urządzenie aktualizuję od razu.

## Wymagania

### P0 — bez tego nie ma funkcji

**EmuStart Server: nowy rodzaj grafiki `poster`**

| # | Wymaganie | Gdzie |
|---|---|---|
| S1 | Rodzaj `poster` obok `box`/`snap`/`logo`: plik `<nazwa>.poster.png` w `mediastore` (adres `/media/<es>/<nazwa>.poster.png`), WebP. **Zapisany w swoich proporcjach, bez przycinania i bez zmiany proporcji**, zmniejszony do wysokości 900 px (`LIMITS["poster"] = (800, 900)`). Proporcje do 2:3 dopasowuje dopiero wyświetlanie (L3), więc oryginał zostaje, a granicę albo sposób wyświetlania można później zmienić bez ponownego pobierania | `art.py` (`LIMITS`, `rename`, `reset_system`, `retry_missing`) |
| S2 | Kolumna `art_poster INTEGER NOT NULL DEFAULT 0`: 0 nie sprawdzano, 1 jest własny plik (LaunchBox/SteamGridDB/wybór admina), 2 brak, **3 plakatem jest okładka gry**. Stan 3 nie tworzy kopii (bez przycinania to ten sam obraz), a adres `poster` wskazuje plik okładki. Po zmianie okładki (wybór admina, synchronizacja) stan 3 jest liczony od nowa. Dodana przez `add_columns`, `set_art` zna `poster` | `library.py`, `art.py`, `srvapi._art_urls`, `catalog.py` |
| S3 | **Wybór według formatu, nie źródła.** Dla gry zbierani są kandydaci ze wszystkich źródeł (S5), a każdy dostaje ocenę: **(a) odchylenie od 2:3** (mniej = lepiej) → **(b) wysokość** (≥ 900 px najlepiej, < 600 px oznacza powiększenie i spada niżej) → **(c) region:** region gry, potem **EU/US** (Europe, United Kingdom, North America, United States, kraje EU), World/bez regionu, **Japonia na końcu** → **(d) źródło:** okładka gry → LaunchBox → SteamGridDB. Remis w (a) to różnica ≤ 5 punktów procentowych, więc pudełko EU/US z 0,70 (5%) wygrywa z japońskim 0,667 (0%). Przykład: SNES ma japońskie pudełko 0,55 (17,5%) i siatkę SteamGridDB 600x900 (0%), więc wygrywa SteamGridDB, a japońskie pudełko zostaje zapasem | `art_sources.py` (`Sources.find_poster`, `poster_score`) |
| S4 | **Granica odchylenia 18%** (stała `POSTER_MAX_SKEW`, ≈ 0,55–0,81). Grafika z większym odchyleniem nie jest kandydatem. Kształt grafiki z LaunchBoksa jest sprawdzany przez pobranie nagłówka (Range, około 64 KB), bo baza nie ma wymiarów. SteamGridDB podaje `width`/`height` w odpowiedzi API, bez pobierania | `art_sources.py` |
| S5 | **Pula kandydatów**, sprawdzana od najtańszych: **(1) okładka gry** już zapisana na serwerze (bez sieci) → **(2) LaunchBox** „Poster”, „Box - Front”, „Box - Front - Reconstructed”, „Fanart - Box - Front” ze wszystkich regionów (do 8 nagłówków na grę) → **(3) SteamGridDB** siatki `600x900,342x482,660x930`, szukane **po nazwie z LaunchBoksa** (id gry z LaunchBoksa), a dopiero potem po tytule, z progiem `MATCH_MIN` 0,8. **Wczesne zakończenie:** kandydat z odchyleniem ≤ 2%, wysokością ≥ 900 px i z regionu EU/US albo bez regionu kończy szukanie. Pobierany jest tylko zwycięzca. **IGDB, TheGamesDB i libretro nie są źródłem plakatów:** libretro ma tylko okładkę z (1), a IGDB/TGDB kadrują te same pudełka i dopasowują po tytule | `art_sources.py` (`launchbox.images(..., "poster")`, `Sgdb.poster_candidates`) |
| S6 | Pobieranie w tle: zbiorcze narzędzie „Grafiki” (`art.Job`) bierze `poster`, a kolejka `Fetcher` pobiera plakat gry, o którą pyta urządzenie. Bez bazy LaunchBoksa i klucza SteamGridDB stan zostaje `0` (nie `2`), więc pobierze się później. `2` tylko wtedy, gdy źródła działały i nic nie znalazły (jak dziś `art_sources.online()`) | `art.py` |
| S7 | Ręczny wybór (poprawianie wyjątków): `art_candidates` / `art_choose` / `art_clear` przyjmują `poster`. Propozycje: LaunchBox (Poster, pudełka wszystkich regionów, **ulotki „Advertisement Flyer - Front”**) + siatki SteamGridDB (do 12), **posortowane według oceny z S3**, z widocznym odchyleniem i regionem („6% · Europe”), bez kandydatów z odchyleniem > 18%. Podgląd pokazuje grafikę dopasowaną do 2:3, czyli tak, jak będzie wyglądać w coverflow. „Usuń” daje stan 2, więc plakat nie wróci sam z okładki | `srvapi.py`, `art_sources.candidates`, `srvadmin.py` |
| S8 | Panel serwera: w edycji gry czwarte pole `poster: "Plakat (2:3)"`, z przyciskami „Zmień…” i „Usuń” (ten sam mechanizm) i podglądem w 2:3 | `web/server.js` (`KINDS`) |
| S9 | `art_overview`: liczba plakatów na system (kolumna obok okładek/zrzutów/logo) | `srvapi.py`, `web/server.js` |

**Synchronizacja z urządzeniami**

| # | Wymaganie | Gdzie |
|---|---|---|
| Y1 | `art_poster` w `COLS`, `art_m["poster"]` (czas pliku) w danych systemu; `_write` zapisuje `art_poster`, `_drop_changed_art` usuwa lokalną kopię po zmianie na serwerze | `libsync.py` |
| Y2 | Lista gier dla urządzeń zawiera `poster` (adres albo `""`) | `catalog.list_games`, `remote._game_row`, `srvapi._art_urls` |
| Y3 | Klient z nowym serwerem: `poster` pobierany do lokalnej kopii mediów tak jak `box` (offline działa). Stan 3 nie pobiera niczego drugi raz (to plik okładki) | `api.py` (`_drop_local_art`, dostęp do mediów) |
| Y4 | **Zgodność wstecz:** starszy klient ignoruje nowe pole i kolumnę. Nowy klient ze starszym serwerem dostaje `art_poster = 0` i pusty `poster`, bez błędów i bez pytania serwera o plik | testy `test_srvapi.py`, `test_thin_client.py` |

**Lumen (repo `D:\GIT\Launcher`)**

| # | Wymaganie | Gdzie |
|---|---|---|
| L1 | `engine/` zaktualizowany z EmuStart (`git subtree pull`, dziś 0.54.0) do wersji z plakatami | `engine/` (uwaga: `engine/UPSTREAM.md` podaje nieaktualne 0.50.2) |
| L2 | `lumen_library` zwraca `poster`; `EmuLibraryRow.poster`, `emu.hasPoster` | `engine/emustart/lumen.py`, `src/main/emustart/mapping.ts` |
| L3 | **Dopasowanie proporcji przy wyświetlaniu:** w coverflow każda okładka (gra EmuStart, gra Steam, plakat z serwera, pudełko zapasowe) o odchyleniu ≤ 18% od 2:3 jest wyświetlana jako dokładnie 2:3 (`object-fit: fill`, bez przycinania i bez rozmytego tła `art--fit`), a do układu wstęgi trafia proporcja ⅔ (`onRatio`, `coverBox`). Okładka z większym odchyleniem zostaje w swoim kształcie, jak dziś. Ta sama stała 18% co w S4 | `TvCoverflow.tsx`, `Art.tsx` (nowa właściwość, np. `fitShape="poster"`) |
| L4 | Coverflow prosi o grafikę plakatu (nowy `ArtLayout` `'flow'` albo parametr adresu `lumen-art://…?flow=1`), a lista TV dalej o `tv`. Kolejność dla gry EmuStart w coverflow: wybór TV gracza → **plakat z serwera** → pudełko jak dziś (LaunchBox box → okładka z serwera). Gry Steam bez zmian (już mają 2:3) | `index.ts` (`artResponse`, `tvArt` / nowa ścieżka) |
| L5 | Grupa wersji (`emugrp:`): plakat pokazywanej wersji, a gdy go nie ma, pierwszy plakat innej wersji (jak `emuLbId`) | `index.ts` |

**Kryteria akceptacji (P0)**

- [ ] Żaden plakat nie jest przycinany: plik `*.poster.png` ma proporcje źródła (± zaokrąglenie przy zmniejszaniu).
- [ ] Gra PS2 z okładką 0,70: bez żadnego pobrania `art_poster = 3`, a adres `poster` to plik okładki. W coverflow stoi jako 2:3 tej samej wielkości co gra Steam obok.
- [ ] Gra SNES z siatką 600x900 w SteamGridDB i japońskim pudełkiem 0,55 w LaunchBoksie: wygrywa SteamGridDB. Bez siatki SteamGridDB plakatem jest japońskie pudełko, w coverflow rozciągnięte do 2:3 (cały obraz widoczny).
- [ ] Gra z pudełkiem EU (0,70) i japońskim (0,667): wygrywa EU (remis w granicy 5 punktów, rozstrzyga region).
- [ ] Gra z kandydatem 600x900 EU/US w kroku (1) albo (2) S5: SteamGridDB nie jest pytany (wczesne zakończenie). IGDB nie jest pytany nigdy.
- [ ] Okładka PSX 1:1 i grafika 920x430: nie zostają plakatem (odchylenie > 18%). W coverflow pudełko PSX zostaje kwadratowe, jak dziś.
- [ ] Ta sama gra w Lumenie: lista TV i desktop pokazują to samo co przed zmianą.
- [ ] Gra bez plakatu (np. demo Amigi): coverflow pokazuje pudełko/zrzut jak dziś, nie pustą ramkę ani placeholder z nazwą.
- [ ] Admin zmienia plakat w panelu: Lumen po odświeżeniu listy pokazuje nowy, a stara kopia lokalna znika.
- [ ] Serwer bez klucza SteamGridDB: gry z pionową okładką mają plakat (stan 3), gra PSX ma `art_poster = 0`. Po dodaniu klucza i ponownym pobraniu plakat się pojawia.
- [ ] EmuStart PC/APK w wersji sprzed zmiany z nowym serwerem: lista, okładki i uruchamianie działają (test ręczny albo `test_three_devices.py`).
- [ ] Lista gier EmuStart (Windows, APK) bez zmian: zrzut ekranu przed i po jest identyczny.
- [ ] `python -m pytest -q` w EmuStart oraz `npm run test:engine` i `npm test` w Lumenie nie mają nowych niepowodzeń względem `main`.

### P1 — szybko po wydaniu

- **Plakat z SteamGridDB Lumena jako zapas w coverflow.** Gdy serwer nie ma plakatu, użyć plakatu, który Lumen i tak pobiera dla widoku desktopowego (`artwork.resolveTitled`), zanim spadnie na pudełko.
- **Przycisk „Plakaty” w narzędziu „Grafiki”** dla jednego systemu (ponowne pobranie tylko plakatów, także tych z `2`).
- **Lista „do poprawy” w panelu:** plakaty z odchyleniem > 15% albo z japońskiego wydania, żeby admin szybko przejrzał wyjątki.
- **Ulotki w automatycznym łańcuchu:** dziś „Advertisement Flyer - Front” jest tylko w ręcznym wyborze (O2).

### P2 — na później (projektować tak, żeby to umożliwić)

- Plakaty w UI EmuStart jako opcja edytora wyglądu. Dane już będą, zmienia się tylko `web/`.
- Wyłączenie siatek SteamGridDB z łańcucha `box`, żeby `box` oznaczał zawsze prawdziwe pudełko.
- Plakaty na liście TV i w siatce desktopowej Lumena z serwera zamiast własnego SteamGridDB Lumena.

## Moduły i emulatory (zasada „jeden ekosystem”)

| Moduł | Co się zmienia | Uwagi |
|---|---|---|
| EmuStart Server | S1–S9: rodzaj, źródła, ocena formatu, panel, statystyki | baza LaunchBoksa i klucz SGDB tylko tu |
| Klient EmuStart (Windows) | Y1–Y4: synchronizacja i lokalna kopia `poster` | UI bez zmian |
| APK | brak zmian w UI. `Bridge.kt` (listy `box/snap/logo`, l. 539/647/665) dopisuje `poster` tylko wtedy, gdy przepuszcza pola dalej | sprawdzić przy implementacji |
| Lumen | L1–L5, w tym dopasowanie proporcji w coverflow (L3) | jedyny konsument plakatów w v1 |
| Emulatory | nie dotyczy | grafiki nie zależą od emulatora |

## Metryki sukcesu

**Szybko (tydzień po wydaniu)**
- Pokrycie: odsetek gier z `art_poster` 1 lub 3 na system (`art_overview`). Punkt wyjścia z pomiaru to 60% (same okładki). Sukces jak w celu 1 (pionowe pudełka ≥ 95%, SNES/N64 ≥ 70%, PSX/GBA ≥ 60%). Cel ambitny ≥ 85% dla wszystkich.
- **Spójność:** mediana odchylenia plakatów ≤ 6%. Odsetek plakatów z odchyleniem > 15% (Super Famicom, longbox, Lynx) i z wydań japońskich podawany osobno dla każdego systemu: to kandydaci do ręcznej poprawki.
- Udział źródeł: ile plakatów dała okładka, LaunchBox, a ile SteamGridDB (log `emustart.art`, z wynikiem oceny). Gdy SteamGridDB daje większość na PSX/SNES, to jest sygnał do O2 (ulotki).
- Trafność: na próbce 50 losowych plakatów najwyżej 2 należą do innej gry niż ta na liście (próg nazw 0,8, sprawdzane ręcznie w panelu).
- Gry bez kandydata z odchyleniem ≤ 18% (`art_poster = 2`) na system: tu przydadzą się ulotki (O2) albo ręczny wybór.

**Po czasie (miesiąc)**
- Ręczne poprawki plakatów w panelu: mniej niż 1 na 100 gier.
- Miejsce na dysku serwera: wzrost `media/*.media` zgodny z szacunkiem, ok. 60–90 KB na pobrany plakat (WebP, wysokość 900, q85; **szacunek do zmierzenia** na pierwszym systemie). Stan 3 nie zajmuje miejsca.

## Pytania otwarte

| # | Pytanie | Kto | Blokuje? |
|---|---|---|---|
| O2 | Czy ulotki LaunchBoksa („Advertisement Flyer - Front”, 28 tys.) mogą być plakatem automatycznie (przed SteamGridDB), czy tylko w ręcznym wyborze? To pionowe reklamy z epoki, często z tekstem, więc wyglądają inaczej niż plakat ze SteamGridDB | użytkownik | nie |
| O3 | Gracz wybrał w Lumenie grafikę TV „Grafika” dla gry (`artTv.grid`). Ma to wygrać z plakatem w coverflow (L4 dziś: tak), czy wybór TV dotyczy tylko listy? Dopasowanie proporcji (L3) obejmuje też ten wybór | użytkownik | nie |
| O4 | Coverflow mówi Lumenowi o „plakacie” nowym `ArtLayout 'flow'` czy parametrem `?flow=1`? `'flow'` ładnie wpisuje się w `prefs.artTv`, ale rozszerza typ współdzielony z desktopem | inżynieria (Lumen) | nie |

## Plan i zależności

1. **Faza 1, EmuStart (kolejna wersja po `pady-fantomy`, prawdopodobnie 0.55.0):** S1–S9 i Y1–Y4, testy, wpis w CHANGELOG. Przed kodem plan w `CHANGELOG.md → [Nieopublikowane]` (zasada z `CLAUDE.md`), po skończeniu obecnej gałęzi `pady-fantomy`.
2. **Napełnienie biblioteki:** po wdrożeniu serwera uruchomić „Grafiki” i sprawdzić pokrycie i spójność (metryki 1–2), a wyjątki poprawić ręcznie.
3. **Faza 2, Lumen:** L1 (`subtree pull`, decyzja O5) → L2–L5 → wydanie Lumena (`lumen-release`). L3 (dopasowanie proporcji) nie zależy od serwera i może wejść wcześniej, bo poprawia też obecne pionowe pudełka.

Zależności: Faza 1 zaczyna się po scaleniu `pady-fantomy` (i PR #103) do `main` EmuStart, a numer wersji
plakatów będzie kolejny po nich. Coverflow (`TvCoverflow.tsx`) jest na `main` Lumena od scalenia `lightbox`
(PR #1). L2, L4 i L5 wymagają serwera z Fazy 1. Lumen ze starszym serwerem ma działać dokładnie jak dziś, z wyjątkiem L3 (brak plakatu, czyli pudełko, dopasowane do 2:3, jeśli jest blisko).
