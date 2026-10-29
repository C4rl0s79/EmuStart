# Changelog

Wszystkie istotne zmiany w EmuStart. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/), wersje według
[SemVer](https://semver.org/lang/pl/).

## [Nieopublikowane]

Plan najbliższych zmian: [ROADMAP.md](ROADMAP.md).

## [0.50.4] — 2026-10-29

Zakres: klient (`api.py` — lista „Wczytaj zapis”). Serwer bez zmian.

### Poprawione
- **„Kontynuuj” = naprawdę najnowszy stan.** Autozapis RetroArcha (`.state.auto`) bywa starszy
  niż ręczne stany (np. zapis przy wyjściu wyłączony), a lista zawsze podpisywała go „Kontynuuj —
  najnowszy stan” i stawiała na górze. Teraz na górze jest stan z najpóźniejszą datą: „Kontynuuj —
  najnowszy stan (Stan #5)”; starszy autozapis to „Autozapis”. Wczytanie jak dotąd („Wczytaj
  zapis” — kopia na miejsce autozapisu).

## [0.50.3] — 2026-10-28

Zakres: klient (`ingame.py`, `saveslots.py`, `syncclient.py`, `api.py`). Serwer bez zmian (już
przechowuje usunięte stany paczki — `removed_add` / `removed_drop`). Interfejs `web/` bez zmian.

### Poprawione
- **Log RetroArcha z sesji naprawdę powstaje.** W ustawieniach sesji (`emustart_ra.cfg`) dochodzą
  `log_verbosity = "true"` i `frontend_log_level = "1"` (INFO — w RetroArch 1.22.2 `RARCH_LOG`
  pisze tylko przy włączonym trybie szczegółowym i poziomie ≤ 1). Wcześniej RetroArch nie tworzył
  `retroarch.log` i po grze w logu EmuStart zawsze było „brak logu sesji”. Teraz widać, czy stan
  z „Wczytaj zapis” wczytał się przy starcie. Oba klucze są tylko na czas sesji — nie wracają do
  `retroarch.cfg`.

### Dodane
- **Usuwanie i przywracanie stanów innych rdzeni (API).** `state_edit(gra, "delete"|"restore", arc,
  core=<rdzeń>)` i `state_trash(gra, core=<rdzeń>)` — stan innego rdzenia RetroArcha z listy
  „Wczytaj zapis” do kosza tego rdzenia (aktywny slot) i z powrotem; dotychczasowe wywołania bez
  zmian. Najpierw aktualna paczka stanów i kosza tego rdzenia z serwera, potem zmiana i od razu
  nowa wersja na serwer. Usunięty stan jest zapisany na serwerze jako usunięty — urządzenie
  grające tym rdzeniem go nie przywróci. Wymaga połączenia (bez niego nic się nie zmienia);
  przerwana wysyłka idzie przy następnym otwarciu listy zapisów tej gry. Pamięć podręczna
  `_inne_rdzenie` po zmianie = nowa wersja z serwera (lista bez usuniętego stanu). Interfejs —
  w Lumen.

## [0.50.2] — 2026-10-28

Zakres: klient (`saveslots.py`, `api.py`). Serwer bez zmian.

### Poprawione
- **Zmiany stanów (zmień numer, kopiuj, przenieś, „Kontynuuj”, usuń, przywróć) czekają na
  synchronizację tej gry w tle.** Pliki zmieniają się pod tą samą blokadą co pobieranie z serwera
  (przy starcie / logowaniu, przed grą), wysyłka na serwer — po niej. Wcześniej zmiana mogła trafić
  w środek pobierania tej samej gry (wynik poprawiała dopiero następna synchronizacja). Dokończenie
  przerwanej zamiany slotów — też pod blokadą.

## [0.50.1] — 2026-10-28

Zakres: klient (`api.py`). Serwer bez zmian.

### Poprawione
- **Sloty zapisów: nic nie zmienia plików zapisów pod działającą grą.** W trakcie gry (od
  przygotowania do zamknięcia emulatora) zmiana slotu i zmiany stanów odmawiają („Najpierw zamknij
  grę”), a otwarcie listy zapisów nie synchronizuje gry z serwerem (lista z plików na dysku;
  synchronizacja jak dotąd przed grą i po niej). Inaczej zmiana aktywnego slotu — ręczna albo
  z innego urządzenia — podmieniłaby pliki pod emulatorem, a jego autozapis przy wyjściu trafiłby
  do innego slotu. Dotyczy zwłaszcza Lumen, w którym bibliotekę i zapisy da się przeglądać w trakcie
  gry.

## [0.50.0] — 2026-10-28

Zakres: synchronizacja zapisów — serwer (`savesync.py`, `remote.py`, `srvadmin.py`) i klient
Windows (`saveslots.py` — nowy, `syncclient.py`, `savebundles.py`, `launcher.py`, `api.py`,
`ingame.py`, `web/options.js`, `web/app.js`). **Najpierw zaktualizuj serwer**, potem PC. Telefon
(APK) i Lumen bez zmian — widzą slot 1 (dostosowanie później).

Nie zmienia się: lista gier, sterowanie poza listą „Wczytaj zapis”, menu (Tab), ekran
uruchamiania, menedżer „Zapisy i stany” (PS2), synchronizacja folderów profilu, okno konfliktu
zapisów w grze, panel serwera.

### Dodane
- **Sloty zapisów gry** — 4 osobne „przejścia” każdej gry w profilu gracza. Slot to komplet
  zapisów gry na platformie: stany, zapisy w grze (`.srm` itd.), karty pamięci. Gra startuje na
  aktywnym slocie. Opcje gry → **Slot zapisów** (np. „Slot 2 · 3 stany · 27.10 20:31”) → A —
  ustaw aktywny.
  - Aktywny slot leży w folderach emulatora (jak dotąd), pozostałe w folderze profilu
    (`_sloty/<platforma>/<gra>/<slot>/`). Wszystkie sloty i kosze synchronizują się z serwerem;
    aktywny slot jest wspólny dla urządzeń konta (wygrywa późniejsza zmiana).
  - Zamiana slotu nic nie kasuje przed sprawdzoną kopią: (1) pliki emulatora → folder starego
    slotu, kopia porównana z oryginałem; (2) pliki nowego slotu → emulator, wynik porównany;
    (3) czyszczenie folderu nowego slotu. Krok zapisany w bazie — przerwana zamiana kończy się
    od właściwego kroku. Niezgodna kopia — zamiana wstrzymana, pliki bez zmian. Bez zamiany
    w trakcie wysyłki zapisów po grze.
- **Edycja stanów** — „Wczytaj zapis” → **przytrzymane A** (przytrzymany Enter, prawy przycisk
  myszy) na stanie: Wczytaj · Zmień numer · Kopiuj jako nowy numer · Kopiuj do slotu… ·
  Przenieś do slotu… · Ustaw jako „Kontynuuj” (dotychczasowy dostaje nowy numer) · Usuń (do
  kosza). Krótkie A — jak dotąd, gra od stanu. PCSX2: numery 0–9, bez „Kontynuuj”.
- **Kosz** na końcu „Wczytaj zapis” (gdy niepusty): 10 najnowszych usuniętych stanów na slot,
  A — przywróć (także na innym urządzeniu).
- Serwer: `slots` i `active_slot` w manifeście (stare `emulators` = slot 1), pole `slot` w
  `upload/init`, `POST …/<gra>/slot`, rodzaj paczki `trash`, cecha `saveslots` w `/v1/info`.
  Klient nie wysyła slotów 2–4 do serwera bez slotów (stary zapisałby je w slocie 1).
- Usunięte / przenumerowane / przeniesione stany zapisane przy paczce jako para (nazwa, SHA-256)
  (`removed`, ostatnie 500) — sumowanie stanów z drugiego urządzenia (`merge_states`) ich nie
  przywraca; kopia pod innym numerem zostaje.

### Poprawione
- **RetroArch: zapis po wczytaniu slotu „Auto” nadpisywał istniejące stany** (Chrono Trigger
  MSU1: wczytany ręcznie autozapis → slot −1, następny zapis → 0, kolejny → 1 = istniejący stan).
  Po każdej sesji straż numerów: stan o istniejącym numerze z inną treścią dostaje kolejny wolny
  numer, poprzedni wraca na swój (z kopii sprzed sesji).
- RetroArch: numer slotu wybrany w menu w trakcie gry (`state_slot`) nie trafia już do
  `retroarch.cfg`.
- RetroArch: log sesji do folderu sesji; po grze linie o stanach trafiają do logu EmuStart
  („RetroArch (stany): …”). Przyczyna nieudanego wczytania stanu przy starcie (Chrono Trigger)
  nie jest ustalona — log ją pokaże przy następnym wystąpieniu.

### Sprawdzone / niesprawdzone
- `python -m pytest -q`: 379 zaliczonych (`main`: 361) — m.in. dwa urządzenia i zamiana slotów,
  przerwanie zamiany w krokach 2 i 3, niezgodna kopia, usunięte i przenumerowane stany nie
  wracają, kosz, edycja offline, stary serwer bez slotów, straż numerów, `state_slot`.
- UI w Chromium (kopia programu bez serwera): opcje gry → Slot zapisów → zmiana i powrót;
  Wczytaj zapis → przytrzymane A → Zmień numer; prawy przycisk → Usuń → Kosz → przywróć; krótkie
  A uruchamia grę od stanu; lista gier bez zmian.
- Niesprawdzone: prawdziwy RetroArch z grą MSU1, okno WebView2, pad, prawdziwy serwer.

## [0.49.1] — 2026-10-27

Zakres: strona serwera (`webportal.py`). Zaktualizuj serwer.

### Poprawione
- **Strona serwera: „Wyloguj” dawało „Error code: 501, Unsupported method ('{}GET')”.** Serwer
  odpowiadał, zanim przeczytał treść zapytania; połączenie zostaje otwarte, więc nieprzeczytane
  `{}` doklejało się do następnego zapytania przeglądarki. To samo po odmowie (wygasła sesja).
  Teraz treść jest czytana zawsze przed odpowiedzią.

## [0.49.0] — 2026-10-27

Zakres: strona serwera (`webportal.py`, `web/portal/`), zaproszenia (`invites.py`), konta
(`accounts.py`), logowanie urządzeń (`enroll.py`, `remote.py`, `public.py`, `server.py`),
programy do pobrania (`clientdist.py`, `signing.py`), wstrzymanie PCSX2 (`ps2states.py`,
`emustore.py`), panel (`srvadmin.py`, `web/server.*`), klienci (`api.py`, `netsrc.py`,
`web/app.js`, `web/options.js`, Android: `Bridge.kt`, `Prefs.kt`), CI (podpis APK).
Zaktualizuj serwer, PC i telefon (stare aplikacje nie zalogują nowego urządzenia kodem ze
strony; zalogowane działają dalej).

### Dodane
- **Strona serwera pod adresem tunelu** (np. `https://emustart.emucloud.org`): animowane logo
  z pikseli, niebo synthwave z siatką, ekran CRT i **Zaloguj przez Google**. Po zalogowaniu:
  - **programy do pobrania** z opisem i zrzutem ekranu — EmuStart (Windows), EmuStart na
    Androida, Lumen;
  - **Pokaż kod logowania** — kod QR, link i 8 znaków (jednorazowy, 10 minut) do zalogowania
    urządzenia tego konta, bez hasła i bez czekania;
  - **hasło konta** (opcjonalne).
- **Zaproszenia** (panel → Urządzenia i konta → Zaproszenia): adres Google + konto (nowe albo
  istniejące) → link do wysłania. Wchodzi tylko konto Google o zaproszonym adresie; serwer
  zakłada albo łączy konto i potem rozpoznaje je po koncie Google. Ważne 30 dni, jednorazowe.
- **Logowanie Google** (panel → Konfiguracja → Strona serwera): identyfikator i sekret klienta
  OAuth z Google Cloud; panel pokazuje adres przekierowania do wklejenia w Google.
- **Programy do pobrania** (panel → Konfiguracja): serwer co 6 godzin pobiera najnowsze wydania
  EmuStart i Lumen z GitHuba, sprawdza podpis wydania (Lumen — kluczem Lumen) i trzyma kopie
  u siebie; plik bez poprawnego podpisu nie trafia na stronę. APK ma teraz podpis wydania (`.sig`).
- **Aplikacja: logowanie kodem** — Zaloguj → link / kod QR ze strony (od razu) albo adres serwera
  → **Mam kod logowania ze strony serwera** (8 znaków padem) / **Mam konto i hasło**.
- **Dodaj urządzenie w panelu wybiera konto** (istniejące albo nowe) — kod jest przypisany do
  konta.
- **PCSX2: aktualizacja wstrzymana, gdy stany PS2 są wspólne dla PC i telefonu.** Serwer czyta
  format stanów z paczek zapisów (ostatni z PC i z telefonu). Nowa wersja PCSX2 nie rozchodzi się
  sama: panel → Emulatory pokazuje „nowa wersja … wstrzymana” z powodem (formaty rozjechane — PC
  nowszy niż ArmSX2; zgodne albo telefon nowszy — aktualizacja możliwa, ale stany z obecnej
  wersji mogą przestać działać) i przycisk **Zaktualizuj mimo to** / **Zaktualizuj**
  z ostrzeżeniem. „Pobierz ponownie” wstrzymania nie omija.

### Poprawione
- Programy do pobrania: plik poprzedniej wersji pobierany właśnie ze strony (Windows nie pozwala
  go usunąć) nie psuje aktualizacji — nowa wersja trafia na stronę, stary plik znika później.
- Serwer bez folderu profili nie traktuje folderów bieżącego katalogu jako kont.

### Zmienione
- Kod logowania (strona serwera i panel) jest przypisany do konta — urządzenie nie wybiera
  konta ani nie podaje hasła.
- Hasło konta jest opcjonalne; „Mam konto” działa tylko dla konta z hasłem.

### Usunięte (bezpieczeństwo)
- **Nowe konto z aplikacji** — wszystkie drogi: prośba „Nowe konto” przy logowaniu, „Inny
  użytkownik → Utwórz konto” (`/v1/enroll/account`, `/v1/enroll/account_poll`), „Nowy profil”
  z hasłem (`PUT /v1/profiles/create`) i zapis pliku do nieistniejącego folderu konta (urządzenie
  administratora zakładało tak konto bez hasła). Nowe konto: zaproszenie albo panel. Bez serwera
  „Nowy profil” tworzy profil lokalny jak dotąd. Prośby o nowe konto zapisane przez starszy
  serwer znikają przy starcie.
- „Mam konto” dla konta bez hasła (ustawiało hasło przy zatwierdzeniu) — teraz kod ze strony.

## [0.48.3] — 2026-10-26

Zakres: ustawienia emulatorów w profilu (`profiles.py`), przypisania pada (`ingame.py`). Zaktualizuj PC.

### Poprawione
- **Ustawienia zmienione w samym emulatorze (bez EmuStart) zostają** — przy następnej grze
  EmuStart nadpisywał je ustawieniami z profilu (np. przypisania pada). Teraz wykrywa zmianę od
  ostatniej gry i przejmuje ją do profilu, który grał ostatnio (wpis w historii zmian „zmiany w
  emulatorze poza EmuStart”); wartości tego komputera (ścieżki, karta graficzna) się nie liczą.
- **DuckStation / PCSX2: przypisania pada zmienione w trakcie gry zostają** — po grze wracał
  cały wpis sprzed gry; teraz wraca tylko numer pada (kolejność kontrolerów).

## [0.48.2] — 2026-10-26

Zakres: stany PS2 paczką wspólną dla PCSX2 i ArmSX2 (`savebundles.py`, `launcher.py`, Android:
`Saves.kt`, `SaveSync.kt`), „Wczytaj zapis” w RetroArchu (`ingame.py`). Zaktualizuj PC i telefon.

### Poprawione
- **„Wczytaj zapis” w RetroArchu nie nadpisuje następnego stanu** — po wczytaniu np. stanu 4
  kolejny zapis szedł do slotu 5 (nadpisując istniejący), a nie do 11. Wybrany stan wczytuje się
  teraz jako autozapis przy starcie (bez `--entryslot`), więc RetroArch numeruje dalej od
  najwyższego stanu (jak na telefonie).
- **Stany PS2 synchronizują się jak stany RetroArcha** — jedna paczka `pcsx2/states` na grę,
  wspólna dla PCSX2 (PC) i ArmSX2 / AetherSX2 (telefon), nazwy plików jak w emulatorze (numer
  płyty i CRC). Stany z dwóch urządzeń się sumują (zajęty slot — starszy stan na pierwszy wolny
  slot 00–09). Wcześniej telefon wysyłał je do osobnego folderu, a PC synchronizował folder stanów
  — urządzenia nie widziały swoich stanów. Menedżer „Stany PS2” usunięty z telefonu.
## [0.48.1] — 2026-10-20

Zakres: synchronizacja stanów (`syncclient.py`, `savebundles.py`, `savesync.py`, `savemigrate.py`,
`api.py`, `launcher.py`, `web/options.js`; Android: `SaveSync.kt`, `Saves.kt`, `Bridge.kt`). Po
aktualizacji serwera: panel → Konfiguracja → „Zapisy sprzed 0.46” → „Przenieś zapisy” (dołącza
stany przeniesione wcześniej do historii).

### Poprawione
- **Lista „Wczytaj zapis” to zawsze stan serwera** (PC i telefon): przy otwarciu opcji gry
  urządzenie najpierw synchronizuje zapisy tej gry z serwerem (nie dopiero przy uruchomieniu
  gry) i pokazuje dokładnie paczkę stanów — bez lokalnych plików spoza niej (np. kopii pod
  nazwą ZIP-a zamiast ROM-u). Pliki paczki nazwane od innej nazwy gry dostają nazwę, której
  użyje emulator.
- **Stany z dwóch urządzeń się sumują** zamiast konfliktu: brakujące sloty dochodzą, ten sam
  slot z inną zawartością — nowszy zostaje, starszy dostaje następny wolny numer (z miniaturą);
  wynik od razu na serwerze i na wszystkich urządzeniach. Zapisy w grze (.srm) — jak dotąd jedna
  wersja, konflikt do decyzji gracza.
- **Migracja zapisów sprzed 0.46: stany dołączają się do istniejącej paczki** (bieżące zostają w
  swoich slotach, stare jako nowe sloty z miniaturami) — wcześniej szły do historii i urządzenia
  ich nie widziały. Ponowne „Przenieś zapisy” w panelu dołącza też stany przeniesione wcześniej
  do historii.
- **Stan z innego rdzenia da się wczytać**: wybór przełącza grę (PC) / system (telefon) na
  RetroArch z tym rdzeniem (brakujący rdzeń PC pobiera z serwera) i startuje z tego stanu.

## [0.48.0] — 2026-10-20

Zakres: serwer (`plock.py`, `remote.py`), PC (`profiles.py`, `syncclient.py`, `api.py`, `web/app.js`),
Android (`Saves.kt`, `SaveSync.kt`, `Bridge.kt`, `web/options.js`). Wymaga EmuStart Server 0.48.0
(blokada profilu) — zaktualizuj serwer i aplikacje.

### Dodane
- **Jedna gra naraz na profil — blokada na serwerze** (`emustart/plock.py`, `/v1/lock/take|renew|
  release|status`): atomowa (dwa urządzenia startujące w tej samej chwili — blokadę dostaje
  jedno), urządzenie rozpoznawane po tokenie sesji, nie po nazwie komputera. Dzierżawa i
  odnawianie jak dotąd, przejęcie tylko po potwierdzeniu gracza; urządzenie, któremu profil
  przejęto, przestaje go odnawiać. PC i telefon korzystają z API; plik `<profil>/lock` w folderze
  profilu nie jest już używany.
- **Zapisy przy wylogowaniu** (PC i telefon): najpierw czeka na wysyłanie po ostatniej grze i
  wysyła zaległe zapisy; gdy czegoś nie udało się wysłać — pytanie „spróbuj jeszcze raz /
  wyloguj mimo to (zapisy zostaną na urządzeniu i wyślą się po zalogowaniu na to samo konto) /
  anuluj”; w trakcie gry wylogowanie zablokowane.
- **Zapisy po zalogowaniu i przy starcie** (PC i telefon, także po zmianie profilu): nowsze wersje
  paczek gier znanych na urządzeniu pobierają się, gdy lokalnie bez zmian; konflikty zostają do
  decyzji przy uruchomieniu gry; gra, która właśnie działa, jest pomijana.

### Poprawione
- **Telefon: „Wczytaj zapis” wprost w opcjach gry** — pobiera z serwera zapisy w grze i stany tej
  gry (z innych rdzeni z oznaczeniem). Od 0.46 telefon miał tylko „Zapisy i stany” (menedżer
  zapisów), który pokazuje już tylko stany PS2 — wyglądało, jakby telefon nie widział żadnych
  zapisów. Menedżer nazywa się teraz „Stany PS2”.
- **Niewysłane zapisy poprzedniego konta nie trafiają na nowe** — paczka pamięta konto (PC: w
  bazie, telefon: osobny stan na konto); po zalogowaniu innym kontem czekają i wyślą się po
  powrocie na tamto konto.
- **Urządzenie unieważnione na serwerze** — komunikat mówi, ile gier ma zapisy czekające na
  wysłanie (zamiast samego „nieznane urządzenie”).

## [0.47.0] — 2026-10-20

Zakres: zapisy jak Steam Cloud — jeden zestaw na konto i grę. Serwer (`savesync.py`,
`savemigrate.py`, panel), PC (`syncclient.py`, `savebundles.py`), Android (`SaveSync.kt`,
`Saves.kt`, `Bridge.kt`), rejestracja urządzeń (`devices.py`, `enroll.py`). Po aktualizacji
serwera: panel → Konfiguracja → „Zapisy sprzed 0.46” → Sprawdź, potem Przenieś zapisy.

### Dodane
- **Migracja zapisów sprzed 0.46 do synchronizacji zapisów** (`emustart/savemigrate.py`, panel
  serwera → Konfiguracja → „Zapisy sprzed 0.46”). Zapisy w grze, stany i karty DuckStation z
  folderów profili (`save/retroarch/saves|states/<rdzeń>/…`, `states/shared/retroarch/<rdzeń>/…`,
  `states/android/retroarch/…`, `save/duckstation/memcards/…`) trafiają do paczek (konto +
  platforma + gra + emulator + rdzeń + rodzaj), dopasowane do gier biblioteki jak na
  urządzeniach (nazwy umowne, miniatury stanów). Paczki, które już są (np. z urządzenia z 0.46),
  **nie są nadpisywane** — stara wersja trafia do ich historii z opisem „zapisy sprzed 0.46
  (urządzenie, data)” i można ją przywrócić w panelu. Ze starych folderów nic nie znika;
  ponowne uruchomienie niczego nie dubluje (porównanie zawartości). „Sprawdź (bez zmian)” —
  sam raport; raport w panelu: ile gier i plików, czego nie dopasowano (np. wspólne karty
  PCSX2, stany bez folderu rdzenia). Próba na kopii danych: `python -m emustart.savemigrate
  --server <kopia folderu server> [--dry]`. Migracja nie uruchamia się sama.
- „Wczytaj zapis” (PC i telefon) pokazuje też **stany z innych rdzeni** tej gry (np. bsnes,
  gdy gra jest na Snes9x) z oznaczeniem „✗ rdzeń … — nie wczyta się” (stany są per rdzeń).

### Zmienione
- **Zapisy w grze wspólne dla zgodnych rdzeni** (`savebundles.SAVE_GROUPS`, jawna lista):
  SNES — Snes9x (wszystkie wersje), bsnes (w tym HD beta, Mercury, 2014), Mesen-S; NES —
  FCEUmm, Nestopia, Mesen, QuickNES (surowy SRAM / bateria, jak „root overrides” Steam Cloud).
  Stany zostają per rdzeń; rdzenie spoza listy — jak dotąd per rdzeń. Serwer przyjmuje zapisy
  rdzenia z grupy do wspólnej paczki także od urządzeń z 0.46 (widzą ją pod swoim rdzeniem), a
  paczki zapisów wysłane wcześniej osobno dla rdzeni łączy przy pierwszym dostępie do gry
  (nowsza aktualna, pozostałe w historii; nic nie znika).

### Poprawione
- **Dwa programy na jednym komputerze nie wylogowują się nawzajem** — nowa rejestracja zastępuje
  tylko wcześniejszą rejestrację tego samego programu na tym sprzęcie (wcześniej EmuStart i
  Lumen unieważniały się na zmianę: 401). Prośba o zalogowanie (panel, zatwierdzanie na
  urządzeniu) pokazuje program („EmuStart”, „EmuStart Android”, „Lumen” — pole `app` albo
  User-Agent) i to, którą rejestrację zastąpi.

## [0.46.2] — 2026-10-20

Zakres: skan biblioteki na serwerze (`emustart/scanner.py`), playlista płyt (`emustart/launcher.py`,
Android: `Prepare.kt`). Gry dopiero teraz łączone w zestaw dostają nazwę bez „(Disc …)”
(id bez zmian) — po aktualizacji serwera odśwież bibliotekę.

### Poprawione
- **Gry wielopłytowe łączą się w jedną pozycję także wtedy, gdy dotąd się rozpadały**
  (skan biblioteki na serwerze, `emustart/scanner.py`):
  - płyty oznaczone literami — „Gra (Disc A)”, „Gra (Disc B)” — w kolejności A, B, C…;
  - podtytuł płyty po numerze — „Gra (Disc 1) (Part One)”, „Gra (Disc 2) - Second Act” —
    nie rozbija zestawu, tytuł gry to część przed „(Disc …)”; ten sam dopisek na każdej
    płycie (np. „(USA)”) zostaje w nazwie jak dotąd, a ten sam numer płyty dwa razy (dwie
    wersje regionalne) — dwie osobne gry;
  - płyty `.cue` (z `.bin` / `.wav`) — dotąd każda płyta była osobną grą;
  - Xbox 360: płyty `.iso` „(Disc N)” obok gier-folderów (GOD/XEX) łączą się jak na innych
    systemach, foldery zostają osobnymi grami.
  Pozycja zestawu ma id dotychczasowej pierwszej płyty (statystyki i baza klientów bez
  zmian), pozostałe płyty są jej plikami. Nazwy gier łączonych już wcześniej się nie
  zmieniają. Bez łączenia: Amiga CD32 i CDTV, „Disk Writer” (tam „Disk N” to osobne
  programy / dyski danych) i dema Amigi (WHDLoad).
- Playlista `.m3u` gry wielopłytowej (PC i telefon) — tylko pliki płyt (`.cue`, `.chd`,
  `.iso`, `.ccd`…), bez ścieżek `.bin` / `.wav` i bez `.img` obok `.ccd`.

## [0.46.1] — 2026-10-20

Zakres: wybór emulatora i rdzenia na koncie gracza (`emustart/emuchoice.py`, `emustart/api.py`;
Android: `Prefs.kt`, `Bridge.kt`).

### Poprawione
- **Wybrane emulatory i rdzenie przechodzą z kontem** — po zalogowaniu istniejącym kontem
  na innym komputerze był domyślny emulator, a inny rdzeń to inne zapisy (Snes9x ≠ bsnes),
  więc zapisy z serwera „znikały”. Teraz:
  - **Windows ↔ Windows**: emulator systemu i emulator wybrany dla gry zapisują się na
    koncie (`settings/windows/emulators.json`, bez ścieżek komputera: „RetroArch + bsnes”,
    „DuckStation”). Przy wejściu na profil wybory konta wygrywają z ustawieniami komputera;
    brakujący rdzeń albo emulator EmuStart pobiera z serwera i ustawia sam. Uruchomienie
    gry zaraz po zalogowaniu czeka na wybory z konta. Wybory zrobione przed tą wersją (tylko
    w `config.json`) trafiają na konto przy pierwszym uruchomieniu — tylko inne niż
    domyślne, żeby domyślny emulator nowego komputera nie nadpisał wyboru ze starego.
  - **Android ↔ Android**: wybory należą do profilu, nie do telefonu (inny gracz na tym
    samym telefonie ma swoje), pobierane z konta od razu po zmianie profilu i po
    zalogowaniu (wcześniej obowiązywał wybór poprzedniego gracza).

## [0.46.0] — 2026-10-20

Zakres: synchronizacja zapisów gier z serwerem (serwer, EmuStart na PC, aplikacja na
Androida), foldery zapisów profilu bez dowiązań, karta pamięci osobna dla gry. Po
aktualizacji zaktualizuj też **EmuStart Server** i aplikację na telefonie — starszy serwer
nie ma API zapisów (gra wtedy rusza na zapisach z urządzenia).

### Dokumentacja
- Koncepcja 2.0 (`docs/koncepcja-2.0.md`, wersja robocza 3): **stany jak w Steam** — każdy
  zapis stanu to nowy, niezmienny wpis na serwerze, przed grą sloty wypełniane najnowszymi
  zgodnymi stanami (RetroArch: `savestate_auto_index` + numer nadawany przez serwer;
  PCSX2/ARMSX2, Dolphin, PPSSPP: warstwa slotów EmuStart z wersją formatu ze stanu);
  „Kontynuuj” = najnowszy stan; **nowy stan kombinacją pada** (LB + Y) na Windows i w
  RetroArchu na Androidzie; ocena „wyświetlania nad innymi aplikacjami” i usługi ułatwień
  dostępu dla pozostałych emulatorów na Androidzie.

### Dodane
- **Synchronizacja zapisów gier z serwerem — jak Steam Cloud** (`docs/SAVE_SYNC_DESIGN.md`).
  Serwer jest jedynym źródłem prawdy: zapisy w grze, stany i karty pamięci każdej gry
  synchronizują się paczkami per konto, gra, emulator i rdzeń RetroArcha (Snes9x i bsnes
  osobno — nic nie kopiuje się między rdzeniami). Przed grą pobierają się zmiany z innych
  urządzeń, po grze zmiany idą na serwer; gra bez połączenia rusza na zapisach z
  urządzenia, a zmiany wysyłają się, gdy wróci połączenie.
  - **Konflikt** (zapisy zmienione tu i na innym urządzeniu, np. gra offline): okno
    uruchamiania pokazuje obie wersje (kiedy, z którego urządzenia) i czeka na decyzję —
    **X** zostaw z tego urządzenia, **Y** weź z serwera, **B** pomiń. Druga wersja nie ginie:
    zostaje w historii na serwerze (10 ostatnich) albo w kopii zapasowej na urządzeniu.
  - Serwer (`emustart/savesync.py`): wersje paczek, odrzucanie nieaktualnych zmian (409),
    konto i urządzenie z tokenu sesji (cudze konto tylko z jego tokenem), pliki przez
    krótkotrwałe podpisane adresy bez ścieżek serwera, SHA-256 i poprawność ZIP-a, nazwa
    urządzenia w manifeście. Stały `game_id` w listach gier.
  - Windows (`emustart/syncclient.py`, `savebundles.py`): RetroArch (zapisy i stany per
    rdzeń), DuckStation i PCSX2 (karty pamięci gry). W opcjach gry: „Zapisy na serwerze” —
    zsynchronizowane / czekają na wysłanie.
  - Android (`SaveSync.kt`, `Saves.kt`): RetroArch (zapisy i stany per rdzeń), PS1 w
    RetroArchu (`<gra>.srm` = karta DuckStation `<gra>_1.mcd` z PC), ArmSX2 / AetherSX2
    (`Mcd001.ps2` = karta tej gry z PC; telefon trzyma karty innych gier w schowku aplikacji
    i podmienia je przed grą). „Wczytaj zapis” — stany tej gry z paczki (te same na każdym
    urządzeniu), z miniaturami.
  - Pliki w paczce mają nazwy umowne — urządzenia nazywające grę inaczej (ZIP z MSU-1,
    rozpakowany ROM, .m3u) widzą te same zapisy.

### Zmienione
- **Zapisy profilu bez dowiązań (junction)** w RetroArchu, DuckStation i PCSX2: emulator
  dostaje folder zapisów profilu w swoich ustawieniach tylko na czas gry (RetroArch w
  ustawieniach sesji, DuckStation i PCSX2 w ini — po grze, także po awarii EmuStart, wracają
  poprzednie wartości). Folder emulatora zostaje zwykłym folderem: emulator uruchomiony poza
  EmuStart nie widzi zapisów ostatniego gracza, aktualizacja emulatora ich nie dotyka.
  Dowiązania z wcześniejszych wersji znikają przy pierwszej grze, zapisy zostają w folderze
  profilu. Dolphin, PPSSPP i RPCS3 (bez ustawienia folderu zapisów) — na razie jak dotąd.
- **Karta pamięci osobna dla każdej gry** w PCSX2 (`<gra>.ps2`) i DuckStation
  (`<gra>_1.mcd`, nazwa od gry w bibliotece — taka sama na każdym urządzeniu, dyski jednej
  gry na jednej karcie). Pierwsza karta gry to kopia dotychczasowej (wspólnej `Mcd001.ps2`
  albo karty „per tytuł” DuckStation), wspólna karta zostaje nietknięta. Przygotowanie do
  synchronizacji zapisów z serwerem — wspólna karta wszystkich gier byłaby konfliktem przy
  każdej zmianie na dwóch urządzeniach.
- Stany RetroArcha, zapisy w grze i karty pamięci **nie synchronizują się już całymi
  folderami profilu** (ani przez wspólny folder stanów `states/shared`) — tylko paczkami
  (jedna prawda). Stany PS2 (.p2s, różne wersje formatu na PC i telefonie) i pozostałe
  emulatory — jak dotąd; menedżer zapisów kopiuje już tylko stany PS2.

### Usunięte
- `emustart/rastates.py` (wspólne stany RetroArcha z numeracją nadawaną przez serwer) —
  zastąpione paczkami stanów; menedżer zapisów bez stanów RetroArcha i podglądu zapisów w
  grze z folderów profilu (stan zapisów: opcje gry → „Zapisy na serwerze”).


## [0.45.1] — 2026-10-14

Zakres: „Wczytaj zapis” na telefonie (`web/options.js`, Android: `Bridge.kt`, `Saves.kt`),
opis wspólnych stanów RetroArcha w katalogu serwera (`emustart/savemgr.py`), `ra_states` w
`emustart/api.py`. Bez zmian: komputer (lista stanów jak w 0.45.0), lista gier, menu.

### Poprawione
- **Telefon: „Zapisy i stany” → „Stany RetroArcha (wszystkie urządzenia)” kończyło się
  błędem** „Cannot read properties of null (reading 'states')” (od 0.45.0) — strona
  „Wczytaj zapis” była tylko na komputerze. Teraz telefon też ją ma: pobiera z serwera
  stany tej gry ze wszystkich urządzeń konta (wspólny folder, rdzeń wybrany na telefonie),
  z urządzeniem, rodzajem zapisu i miniaturą; najnowszy jako „Kontynuuj”. Wybrany stan
  telefon pobiera przed startem gry i podkłada jako autozapis RetroArcha (`.state.auto`),
  który RetroArch wczytuje przy starcie — jak na komputerze.

## [0.45.0] — 2026-10-14

### Zmienione
- **EmuStart i EmuStart Server to dwa osobne programy** — każdy z własnym exe, plikiem
  wydania, podpisem i aktualizacją:
  - `EmuStart-<wersja>-win64.zip` — **EmuStart** na PC (`EmuStart.exe`: granie z padem);
  - `EmuStart-Server-<wersja>-win64.zip` — **EmuStart Server** (`EmuStartServer.exe`):
    bez parametrów otwiera panel serwera (i uruchamia serwer, gdy nie działa);
    `--service` — serwer bez okna (zadanie Harmonogramu), `--install` — autostart, zapora
    i skrót (jako administrator), `--login-link` — link logowania do schowka.
  - Aktualizacja serwera przechodzi na nowy program sama: pobiera plik serwera, usuwa
    z folderu serwera dawny `EmuStart.exe` i przestawia zadanie Harmonogramu na
    `EmuStartServer.exe --service`. Dane serwera (folder `server`) zostają.
  - `EmuStart.exe` nie ma już trybu serwera (`--server`, `--server-panel`,
    `--install-server`, `--server-key`).

- **EmuStart na PC bierze bibliotekę tylko z serwera** — bez własnych folderów z grami,
  skanowania, pobierania grafik i opisów całej biblioteki, bazy LaunchBox i plików DAT
  arcade (to robi serwer, panel → Grafiki i opisy). Z PC zniknęły: ekran „Grafiki
  i metadane”, foldery z grami i BIOS-ami w ustawieniach, „Skanuj kolekcję” (zostaje
  „Odśwież bibliotekę z serwera”), w opcjach systemu „Grafiki: pobierz brakujące”
  i „Skanuj ponownie”. Bez serwera grają gry już pobrane na komputer. Okładkę, zrzut,
  logo i opis jednej gry nadal można wybrać w opcjach gry — trafiają na serwer.
- **EmuStart na PC działa tylko przez serwer — bez SMB.** Gry pobiera z serwera (także
  przypięte — wcześniej przypinanie kopiowało wyłącznie przez SMB, więc na PC bez udziału nie
  działało), zapisy, stany, ustawienia i konta — przez serwer, BIOS-y — z serwera. Serwer
  niedostępny: grają gry już pobrane, a zapisy czekają w kolejce. Usunięte: start gry prosto
  z NAS-a i „Graj teraz (z sieci)”, pomiar sieci, ustawienia „Tryb sieci”, „Próg LAN”, „Gry
  z serwera”, „Zapisy i profile przez serwer”, przenoszenie folderu profilu na NAS-ie,
  zgłaszanie folderów z grami z PC na serwer (foldery ustawia się w panelu serwera).
- **Okładki, zrzuty, logo i opisy pojedynczych gier oraz logo, nazwy i zalecane emulatory
  systemów ustawia się tylko w panelu serwera** — wszystkie urządzenia pokazują to samo.
  Z PC zniknęły: wybór grafik i edycja opisu w opcjach gry (opis zostaje do podglądu),
  logo, poświata i nazwa w opcjach systemu, wysyłanie grafik i wyglądu systemów z PC na
  serwer (także zalecanego emulatora — teraz ustawia go panel). Serwer nie przyjmuje już
  wyglądu ani grafik od urządzeń.
- Serwer ma własny, mniejszy zestaw funkcji (emustart/srvapi.py) — nie uruchamia już
  obsługi padów, profili gracza i wysyłania zapisów, które są potrzebne tylko na PC.

### Poprawione
- **Skan: system, który zniknął (usunięty folder, scalony), zostawiał swoje gry w bazie** —
  teraz znika razem z nimi.
- **Wersje gier arcade (FBA) na PC bez gry nadrzędnej i BIOS-u** — PC szukał ich tylko
  w swoich folderach z grami, więc emulator zgłaszał brak plików. Teraz serwer podaje je
  razem z grą, a PC pobiera je obok gry (także przy przypinaniu), jak telefon.

- **Serwer jest źródłem prawdy dla grafik, opisów i logo — urządzenia trzymają lokalną
  kopię** (szybki interfejs) i odświeżają ją same: każda zmiana grafiki albo opisu na
  serwerze podbija wersję systemu; PC co 5 minut (i przy starcie) pobiera zmienione systemy,
  zapisuje opisy z serwera, a zmienione grafiki i logo systemów pobiera od nowa przy
  wyświetleniu. Lista na ekranie odświeża się sama. Telefon ma grafiki z wersją w adresie.
  Wcześniej PC pobierał system tylko po ponownym skanie na serwerze, a raz pobraną grafikę
  trzymał na zawsze.
- **Administrator zmienia grafiki, opisy i logo także z PC i telefonu** — opcje gry: okładka,
  zrzut, logo gry, poprawki opisu, „Pobierz opis z sieci”; opcje systemu (przytrzymane A na
  logo, teraz także na telefonie): logo, poświata, nazwa. Zapis idzie na serwer (źródło prawdy),
  a pozostałe urządzenia dostają zmianę przy najbliższej synchronizacji. Zwykłe konto widzi
  opis tylko do odczytu. Serwer: `/v1/admin/…` tylko dla urządzenia administratora.
- **Obrazek stanu gry przy wyborze zapisu** (opcje gry → Wczytaj zapis): zrzut, który
  zapisał emulator — RetroArch (obrazek obok stanu; EmuStart włącza go przy starcie gry),
  PCSX2 (zrzut w pliku .p2s), DuckStation (zrzut w nagłówku stanu, gdy nieskompresowany).
- **Opisy gier szuka serwer** — gdy urządzenie ogląda grę bez opisu, serwer pobiera go w tle
  (PC wcześniej szukał sam).
- **Android: RetroArch zamyka się po zamknięciu gry** („Quit on Close Content: CLI”) — wcześniej
  zostawał w tle z menu. Stany gry RetroArcha mają obrazek (podgląd stanu).

### Dodane
- **Reset gry** w menu w grze (A + Y, RetroArch) i **Graj od początku** w opcjach gry — start bez
  wczytywania stanu (autozapis / „Kontynuuj” / wznowienie), np. gdy wczytany stan nie pasuje do
  zmienionych ustawień rdzenia albo shadera.
- **Shader ekranu bez szukania w menu RetroArcha** (czarny ekran, gdy shader nie pasuje np. do
  skali rozdzielczości rdzenia): menu w grze (A + Y) → *Włącz / wyłącz shader* (do końca gry)
  albo *Bez shadera w tym systemie* (na stałe); opcje gry → *Ustawienia emulatora* → *Shader
  ekranu* przywraca. Wyłączenie działa tylko na czas gry — własne ustawienia shadera RetroArcha
  zostają nietknięte.
- **Historia ustawień emulatora — cofnięcie zepsutej zmiany**: opcje gry → *Ustawienia
  emulatora* → *Cofnij zmianę* (10 ostatnich zmian: data, gra, pliki). Ustawienia profilu nadal
  zastępują plik emulatora przed grą, ale zmiana, po której np. ekran jest czarny (za wysoka
  skala rozdzielczości), da się cofnąć bez edycji plików na serwerze; samo przywrócenie też
  trafia do historii.
- **Stany RetroArcha jak w Steam** (koncepcja 2.0, §5.5) — tylko dochodzą, nigdy się nie
  nadpisują: numer stanu zajęty na serwerze inną zawartością (dwa urządzenia, gra bez serwera)
  → następny wolny numer (komputer i telefon); autozapis przy wyjściu trafia też do historii
  jako kolejny stan, także gdy inne urządzenie zapisało w międzyczasie (wcześniej szedł do kopii
  zapasowej); przed grą najnowszy stan z dowolnego urządzenia wczytuje się sam („Kontynuuj”);
  opis każdego stanu (urządzenie, czas, autozapis / ręczny) w `.states.json` na serwerze;
  serwer trzyma 50 najnowszych stanów gry.
- **Jeden widok stanów**: opcje gry → *Wczytaj zapis* pokazuje stany ze wszystkich urządzeń
  konta (pobierane przy otwarciu) z miniaturą, numerem, urządzeniem i datą, na górze
  „Kontynuuj — najnowszy stan”; *Zapisy i stany* nie powtarza już stanów RetroArcha (zostają
  tam stany PS2 z kopiowaniem między komputerem a telefonem i zapisy w grze).
- **Panel serwera: zakładki** (Stan, Konfiguracja, Grafiki i opisy, Urządzenia i konta, Emulatory, Aktualizacja).
- **Emulatory z serwera.** EmuStart Server trzyma emulatory i rdzenie RetroArcha potrzebne
  systemom biblioteki (zalecany emulator i rdzeń z panelu): raz dziennie i przyciskiem w nowej
  zakładce **Emulatory** pobiera nowe wydania (GitHub, Dolphin, Eden, Citron, buildbot
  libretro — jak skrypt emu_updater), zapisuje je z sumami SHA-256 i zostawia też poprzednią
  wersję. EmuStart na PC bierze emulatory **tylko z serwera**: po wybraniu folderu emulatorów,
  przy starcie i co 5 minut sam doinstalowuje brakujące i aktualizuje starsze (także zastane
  w folderze, np. z RetroBata) — pobiera archiwum wersji (skompresowane, robione raz na
  serwerze) jednym wznawianym strumieniem i rozpakowuje tylko brakujące i zmienione pliki; konfiguracji i
  zapisów nie rusza, działającego emulatora nie podmienia (później). Pasek „⬇ emulatory”
  pokazuje postęp. Komputer nie pobiera już emulatorów z internetu.
- **Wszystkie rdzenie RetroArcha na serwerze** (około 245 z buildbota): komputer dostaje dla
  każdego systemu biblioteki zalecany rdzeń i wszystkie pasujące — rozpoznane po plikach `.info`
  RetroArcha (np. snes9x, bsnes, bsnes-jg dla SNES) — i wybiera je w Ustawieniach; panel ma je
  na liście zalecanego rdzenia. Brakujące pozycje (nowy system w bibliotece) serwer dociąga co
  godzinę, nie raz na dobę. W magazynie zostaje samo archiwum wersji (bez rozpakowanych plików).
- **Wymagane ustawienia emulatorów z serwera** (panel → Emulatory): dla RetroArcha, DuckStation,
  PCSX2 i Dolphina lista wartości, które każdy komputer nakłada tuż przed grą (po ustawieniach
  profilu — wygrywają). Domyślnie: wyjście z gry bez okna z pytaniem, w RetroArchu pauza
  w tle, obrazki stanów i zamknięcie gry = wyjście z RetroArcha; zmiana i powrót do domyślnych
  w panelu.
- **RetroArch domyślnie (PC i telefon)**: każdy zapis stanu z nowym numerem
  (`savestate_auto_index`), Start + Select — menu RetroArcha, Select — przycisk skrótów,
  Select + RB — przyspieszenie (wł./wył.), Select + Y — zapis stanu. Na PC sterownik pada XInput.
- **Więcej propozycji logo systemów** (np. Dreamcast 43 → 57, PS1 77): motywy EmulationStation /
  ES-DE (Art Book Next, Carbon, Alekfull NX, Canvas, Simple, Pixel, SNES Mini) i ikony z 9 motywów
  RetroArcha — prosto z internetu, sprawdzane naraz — oraz paczka logo (np. `platform_logos`
  z PyLinks) w **jednym pliku** magazynu serwera (`media\_logopack.media`, 4315 logo zamiast
  tylu plików): wczytywana z folderu ustawionego w panelu (Konfiguracja → Foldery) albo
  skopiowana gotowa. Wcześniej paczka i motywy RetroArcha były plikami na komputerze
  deweloperskim, których serwer nie ma. Wybór logo przyjmuje tylko propozycje.
- **Klucze źródeł grafik z pliku**: `art_keys.json` w folderze `server` serwera — przy starcie
  serwer zapisuje je zaszyfrowane i usuwa plik.
- **Klucze źródeł grafik z PyLinks na serwer**: EmuStart na komputerze administratora →
  Ustawienia → *Wyślij klucze źródeł grafik z PyLinks na serwer* (PyLinks szyfruje klucze
  kluczem tego komputera, więc serwer nie mógł ich odczytać sam).
- **Panel serwera → Konfiguracja → Foldery**: foldery z grami (kilka, z kolejnością), BIOS-y,
  emulatory, profile graczy, kopie kont i MAME. Wybór folderu w oknie panelu; serwer
  sprawdza każdy folder tak, jak sam go widzi (konto systemowe), i pokazuje, co tam jest
  (liczba systemów, plików BIOS-ów, czy jest RetroArch). Dysk sieciowy wybrany literą
  (np. `Z:`) zamieniany jest na ścieżkę sieciową, której serwer używa. Nowe foldery z grami
  — lista gier przeładowuje się sama.
- **Panel serwera → Grafiki i opisy**: pobieranie brakujących okładek, zrzutów, logo
  i opisów (wszystkie systemy albo jeden), z Wikipedią, zmniejszanie zapisanych grafik,
  baza LaunchBox i klucze źródeł (SteamGridDB, IGDB, TheGamesDB — zaszyfrowane na
  serwerze). Pobiera serwer; EmuStart na PC i telefony biorą grafiki i opisy od niego.
- **Panel serwera → Grafiki i opisy → Gra**: wyszukanie gry, okładka, zrzut i logo
  (propozycje z libretro, SteamGridDB, IGDB, TheGamesDB albo wyszukanie pod inną nazwą,
  usunięcie), opis i dane gry (tytuł, producent, wydawca, rok, gatunek, gracze) z ręczną
  poprawką, przywróceniem danych pobranych i pobraniem opisu z sieci.
- **Panel serwera → Konfiguracja → Systemy**: logo (propozycje, bez logo), nazwa,
  poświata, widoczność na urządzeniach, zalecany emulator i rdzeń RetroArcha.
- **Folder programu serwera tylko do odczytu dla zwykłych użytkowników** (`--install`):
  zapis — tylko konto SYSTEM (serwer) i administratorzy. Wcześniej folder dziedziczył z dysku
  „Użytkownicy uwierzytelnieni: modyfikacja” — każdy zalogowany użytkownik mógł podmienić DLL
  w `_internal`, a serwer wykonałby ją przy starcie z najwyższymi uprawnieniami; mógł też
  czytać i zmieniać dane serwera (konta, profile). Panel i link logowania (konto użytkownika)
  piszą logi w `%LOCALAPPDATA%\EmuStart Server\logs`.
- **`_internal` ma 64 pliki zamiast 316** (szybsze kopiowanie i aktualizacja): wbudowane logo
  platform w jednym pliku (`assets/systems.media` zamiast 40 PNG), bez ~96 bibliotek zgodności
  .NET (Windows ma je wbudowane — okno działa z samym `Python.Runtime.dll`), bez źródeł `.py`
  pakietów (ich kod jest już w exe), części pywebview na Androida, metadanych pakietów
  (`*.dist-info`), `setuptools` i zbędnej ikony.
- **Klucz certyfikatu serwera zaszyfrowany na dysku** — `tls-key.pem` zaszyfrowany (AES-256)
  losowym hasłem, a hasło chronione DPAPI tego komputera (`tls-key.pass`). Kopia folderu
  serwera (udział sieciowy, kopia zapasowa, dysk) na innym komputerze nic nie daje. Klucz
  serwera sprzed 0.45 szyfruje się sam przy starcie. **Przeniesienie serwera**: panel →
  Konfiguracja → *Przygotuj przeniesienie* (hasło przeniesienia; plik `przeniesienie.emustart`
  z kluczem certyfikatu i kluczami źródeł grafik), skopiowanie folderu, na nowym komputerze
  panel prosi o hasło — certyfikat ten sam, urządzenia działają bez ponownego logowania.
- **Grafiki gier w jednym pliku na system** (`media/<system>.media`, SQLite) zamiast osobnego
  pliku na każdą okładkę, zrzut i logo — na serwerze ok. 100 tys. plików mniej, serwer da się
  szybko skopiować i przenieść. Tak samo lokalna kopia na PC. Adresy grafik bez zmian. Stare
  pliki przenoszą się same przy starcie (w tle; każdy plik usuwany dopiero po odczycie
  kontrolnym z bazy, do tego czasu jest serwowany jak dotąd). Logo systemów też w jednym pliku
  (`media/_systems.media`), obrazki stanów gry na PC — `media/_stany.media`.
- **Scalanie platform** (panel → Konfiguracja → Systemy → *scal z…*): np. dwa foldery
  Xbox 360 (`x360` i `xbox360`) — gry z obu na jednej liście, ta sama gra raz (pierwszeństwo:
  kolejność folderów z grami); *Rozdziel* cofa. Przy każdym systemie widać jego folder(y).
- Panel: komunikaty w dymku w rogu okna (zapis widać bez przewijania); lista systemów
  odświeża się po zapisie i po skanie; LaunchBox — „gotowa” po pobraniu zamiast wiszącego
  „Pobieram…”.
- EmuStart: okno oczekiwania na zatwierdzenie pokazuje, na którym serwerze czeka prośba.
- **Panel serwera → konta: pliki i kopie zapasowe.** „Pliki i kopie” przy koncie pokazuje,
  co jest w profilu (zapisy, stany gry, ustawienia — liczba plików, rozmiar, ostatnia
  zmiana, ostatnio zmienione pliki). Kopia konta to jeden ZIP (profil + wpis konta z hasłem)
  w folderze kopii; **kopie automatyczne raz dziennie**, gdy konto się zmieniło, zostaje
  14 najnowszych (do zmiany). **Przywrócenie** zaczyna się od kopii obecnego stanu, a pliki,
  których w kopii nie ma, zostają.

### Dokumentacja
- Wiki: nowa strona **EmuStart Server** (instalacja, panel, foldery, grafiki i opisy, konta
  i kopie, logowanie, adresy, API); instalacja dwóch programów; grafiki i opisy z serwera,
  uruchamianie gier tylko z serwera, kilka urządzeń z jednym serwerem — bez SMB.

### Naprawione
- **PCSX2 i DuckStation zainstalowane z serwera startowały z kreatorem pierwszego uruchomienia**
  (i bez BIOS-u). Teraz przed grą: kreator i własne aktualizacje emulatora wyłączone
  (aktualizuje serwer), BIOS z serwera w folderze emulatora (PCSX2: `pcsx2ios` z folderu
  BIOS-ów serwera — serwer wypisuje BIOS-y z folderu, `GET /v1/bios?dir=`; DuckStation: BIOS-y
  PS1). Przy okazji: zmiana pustej wartości w pliku ini (`BIOS = `) trafiała do następnej linii.
- Skróty pada RetroArcha na PC: przycisk skrótów był ustawiony na Start (numer 6 w sterowniku
  XInput) zamiast Select / View (7) — Select + RB i Select + Y nie działały. Na telefonie bez zmian.
- Menu w grze → *Włącz / wyłącz shader* działało na stałe zamiast do końca gry: RetroArch zapisywał
  zmianę przy wyjściu, a EmuStart przenosił ją do `retroarch.cfg` i profilu. Teraz zostaje tylko
  w tej grze; inne zmiany z menu RetroArcha zapisują się jak dotąd.
- **Wolne łączenie z serwerem (PC i telefon)**:
  - wybór adresu czekał, aż adresy sieci lokalnej serwera — z innej sieci nigdy nieosiągalne —
    przekroczą czas (PC 2,5 s, telefon 3,5 s przy każdym sprawdzeniu); teraz po pierwszym
    działającym adresie najwyżej 0,4 s na lepszy (PC zmierzone: 2,5 → 0,6 s),
  - logowanie po nieudanej próbie szło przez **wszystkie** adresy, czekając 8–10 s na każdy martwy
    (na telefonie minuta i dłużej — stąd gra na starym zapisie i konflikt); teraz tylko przez
    adresy, które odpowiedziały, a jednoznaczna odmowa serwera kończy próby,
  - lista gier systemu skompresowana (np. SNES 5 MB → 0,86 MB),
  - **tylko zmienione gry**: serwer pobierający grafiki podbijał wersję całego systemu, więc PC
    co 5 minut ściągał od nowa wszystkie listy (62 systemy, 45 tys. gier — kilkadziesiąt
    sekund); teraz zmiana grafiki lub opisu gry wysyła tylko tę grę, pełna lista — po skanie.
- Uruchamianie gry: okno „Przygotowuję…” pokazuje się od razu po A (wcześniej dopiero po
  sprawdzeniu konta i pobraniu BIOS-ów — przez ten czas nic się nie działo); BIOS-y, których
  serwer nie ma, nie są sprawdzane przy każdej grze (pamięć na 30 minut); w logu czas przed
  startem sesji, gdy przekracza 0,5 s. Rozpakowywanie (np. paczek MSU-1) z paskiem postępu,
  bez podwójnego „Rozpakowuję do pamięci…”.
- Zapisy i stany: „undefined” zamiast „Wszystkie urządzenia” przy stanach wspólnych.
- **Systemy rozpoznawane po każdej nazwie**: kod ES, ES-DE (`atarilynx`, `gc`, `genesis`,
  `wonderswan`), No-Intro / Redump, libretro i nazwy zwyczajowe (`Atari Lynx`, `PS1`, `Super
  Famicom`) — jedna tabela dla folderów z grami, motywów logo i paczki logo. Wcześniej m.in. folder
  `atarilynx` nie był rozpoznawany, a dla 28 systemów żaden motyw nie dawał logo (np. Lynx 0 → 6).
- Panel serwera: wybór logo systemu w oknie nad listą (Esc / kliknięcie obok zamyka) zamiast na
  dole długiej listy systemów; podaje liczbę propozycji.
- Lista gier na PC: okładka i zrzut miały stałą wysokość z edytora wyglądu, choć pod krótkim
  opisem zostawało dużo pustego miejsca. Teraz rosną w wolne miejsce (do granicy szerokości
  panelu), a opis zostaje cały.
- Serwer, który nie zna komputera (np. inny serwer pod tym samym adresem) albo ma inny
  certyfikat: EmuStart w kółko pytał wszystkie adresy, czekając po 10 s na każdy niedostępny,
  a pasek bez końca pokazywał „Serwer: łączę…”. Teraz pierwsza jednoznaczna odpowiedź kończy
  próby, pasek od razu mówi, co zrobić (Ustawienia → Zaloguj), a kolejna próba jest po 2 minutach.
- Pobieranie emulatorów: okno „Pobierz brakujące emulatory” i wyrównanie w tle pisały naraz do
  tych samych plików (WinError 32, „emulator działa”, znikające pliki) — teraz jedna
  instalacja naraz. Zamiast składania ZIP-a z tysięcy plików przy każdym pobraniu serwer
  trzyma gotowe archiwum wersji.
- Okno wyboru logo: podgląd, który się nie wczytał, przebudowywał całą siatkę — przeglądarka
  pobierała wtedy od nowa wszystkie obrazki (powolne ładowanie, puste kafelki). Teraz znika
  tylko ten kafelek; podglądy z serwera zostają w pamięci okna na godzinę. Ciemne logo na
  szachownicy w szarościach zamiast na czarnym tle (wcześniej warianty „czarne” były niewidoczne).
- **Nowe konto nie pojawiało się na komputerze ani w panelu** („Kto gra?” pokazywał tylko
  „Inny użytkownik”, panel — „Brak kont”): świeży serwer bez ustawionego folderu profili
  szukał jeszcze folderu z czasów SMB i nie miał żadnego. Teraz zakłada `profiles` we
  własnym katalogu (albo w folderze z panelu), a konta zatwierdzone wcześniej bez folderu
  dostają go przy starcie. EmuStart na PC zawsze pokazuje konto, którym jest zalogowany —
  także gdy serwer chwilowo nie poda listy kont.
- Ekran „Kto gra?” / „Profile” sprawdza konta na serwerze co 15 s, póki jest otwarty —
  nowo zatwierdzone konto (albo konto po powrocie serwera) pojawia się samo, a nie dopiero
  po ponownym wejściu na ekran.
- Panel serwera: pusta sekcja „Konto — pliki i kopie” (i ostrzeżenie o kluczu TLS) nie
  pokazuje się już sama po przejściu na zakładkę — tylko po „Pliki i kopie”. Wygasły kod
  „Dodaj urządzenie” znika razem z pustym linkiem i przyciskiem „Kopiuj link”.
- Ustawienia na PC: wybór folderu (np. z emulatorami), wpisanie ścieżki i zmiana emulatora
  systemu kończyły się „Błąd interfejsu: saveSetting is not defined” — przy podziale na serwer
  i klienta wypadły funkcje, których Ustawienia nadal używają. Test sprawdza teraz, że
  interfejs nie woła funkcji ani metod programu, których nie ma.

## [0.44.8] — 2026-10-13

### Zmienione
- **Przygotowanie do rozdziału na EmuStart i EmuStart Server (0.45).** Aktualizacja serwera
  pobiera plik serwera (`EmuStart-Server-…-win64.zip`), gdy wydanie go ma, i sama przechodzi
  na nowy program: `EmuStartServer.exe` w folderze serwera, zadanie Harmonogramu
  „EmuStart Server” i skrót w menu Start przestawione na niego, dawny `EmuStart.exe`
  z folderu serwera usunięty; dane serwera (folder `server`) zostają. Wydania bez pliku
  serwera — jak dotąd. EmuStart na PC aktualizuje się jak dotąd.

## [0.44.7] — 2026-10-13

### Poprawione
- **PC z biblioteką z serwera: brak logo systemów** (i grafik, których komputer nie ma
  u siebie). Pobieranie brakujących grafik z serwera działało tylko w trybie przeglądarki —
  w zwykłym oknie EmuStart nigdy. Teraz w obu. Chwilowy błąd logowania nie blokuje już
  grafiki na 10 minut (pamiętany jest tylko prawdziwy brak na serwerze).
- **PC: odrzucane przez serwer (401) wysyłanie wyglądu systemów, zgłaszanie folderów
  z grami i czytanie gry w trakcie pobierania** — szły z identyfikatorem urządzenia zamiast
  tokenu sesji (pozostałość sprzed logowania kluczem urządzenia). Teraz z tokenem, a po
  wygaśnięciu tokenu — z nowym.
- **PC z biblioteką z serwera nie wysyła przy starcie swojego wyglądu systemów**: nadpisałby
  logo wybrane na serwerze dawno pobranymi na tym komputerze. Wysyła tylko to, co zmienisz
  na nim ręcznie.
- **PC z biblioteką z serwera: bez „Grafiki i metadane” w menu** — grafiki i opisy pobiera
  serwer, komputer bierze je od niego.

## [0.44.6] — 2026-10-13

### Poprawione
- **FBA bez grafik po 0.44.4**: gry dostały pełne nazwy z pliku DAT FinalBurn Neo
  („10yard” → „10-Yard Fight (World, set 1)”), a grafiki zostały zapisane pod starymi
  nazwami setów — lista wskazywała pliki, których nie ma. Teraz zmiana nazwy gry przy
  skanowaniu przenosi jej grafiki (okładka, zrzut, logo), a serwer przy starcie sam
  przenosi grafiki zapisane pod nazwą setu (na serwerze: ok. 6300 gier FBA).

## [0.44.5] — 2026-10-13

### Poprawione
- **Telefon: przewijanie spustami po literach przeskakiwało co drugą literę** (A, B, D, F…).
  Wiele padów zgłasza spust dwa razy — jako przycisk L2/R2 i jako oś — i każde zgłoszenie
  liczyło się jako osobne naciśnięcie. Teraz jedno pociągnięcie spustu = jedna litera.
- **Telefon: brak grafik w systemach z małą listą gier** (do 100 gier, np. Sega CD, MSU-1;
  błąd z 0.44.4): adresy grafik były przerabiane dwa razy („/remote/remote/…”). Duże systemy
  (PSX, PS2) działały. Przerobienie adresu jest teraz jednorazowe z założenia.

## [0.44.4] — 2026-10-13

### Poprawione
- **Duże listy gier na telefonie (np. FBA) — zero gier po przerwanym wczytywaniu**: lista
  przychodziła w jednym kawałku (kilka MB), a nieudane pobranie zostawało zapamiętane jako
  pusta lista do ręcznego przeładowania. Teraz:
  - lista z pamięci (ostatnie połączenie) od razu, odświeżana w tle;
  - bez listy w pamięci — najpierw 100 gier z góry i 100 z dołu, reszta dociągana w tle
    porcjami po 1000 z ponawianiem; po dociągnięciu lista odświeża się sama (ta sama gra
    zaznaczona); nieudane pobranie — ponowienie, nigdy „zero gier”;
  - serwer liczy listę raz (kolejne porcje natychmiast) i wysyła ją skompresowaną (gzip).
- **Przy pustej liście zostawały grafiki i opis poprzedniej gry** (z innej platformy) —
  podgląd jest czyszczony.
- **FBA / FinalBurn Neo bez MAME na serwerze**: nazwy setów („10yardj”) zamiast tytułów
  i każda wersja gry osobno. Bez MAME serwer bierze nazwy, rodziców wersji, BIOS-y, rok
  i producenta z oficjalnego pliku DAT FinalBurn Neo (raz na 30 dni) i skanuje systemy
  arcade od nowa: lista pokazuje grę raz (np. 6607 setów → ok. 2160 gier), BIOS-y znikają.
  Inne wersje (japońska, rewizje, bootlegi) — w opcjach gry → **Wersja gry** (PC
  i telefon, wybór zapamiętany w profilu; gra startuje w wybranej wersji, z ROM-ami
  rodzica).

## [0.44.3] — 2026-10-13

### Poprawione
- **Android: aplikacja wychodziła na pulpit zaraz po starcie** (0.44.2): odświeżenie listy
  systemów w tle wywoływało kod interfejsu (WebView) z wątku w tle, a Android na to nie
  pozwala. Teraz każde wywołanie kodu interfejsu przechodzi na wątek interfejsu — także
  przyszłe z dowolnego miejsca.

## [0.44.2] — 2026-10-13

Zmiany, które miały wejść do 0.44.1, ale trafiły na gałąź już po jego wydaniu.

### Poprawione
- **Zapisy na urządzeniach konta administratora — błąd 403 „konto wymaga hasła”** (od
  0.43, serwer): administrator to cecha konta, a sprawdzanie dostępu traktowało jego
  urządzenia jak „widzące wszystkie konta” i dla własnego konta z hasłem żądało tokenu konta,
  którego urządzenie właściciela nie ma. Menedżer zapisów i synchronizacja zapisów odmawiały
  na każdym urządzeniu tego konta. Teraz własne konto urządzenia jest zawsze dostępne;
  cudze konto z hasłem — nadal tylko po haśle. **Wymaga aktualizacji serwera**; zapisy
  czekające w kolejce wyślą się same.
- **Start bez czekania na serwer** (telefon i komputer): telefon pokazywał cokolwiek dopiero
  po wyborze adresu serwera i zalogowaniu (z wyłączonym Tailscale ponad 10 s). Teraz lista
  systemów od razu z pamięci (ostatnie połączenie), a serwer w tle — po odpowiedzi
  interfejs odświeża się sam; pasek stanu na komputerze też nie czeka. W trakcie łączenia
  pasek pokazuje **„Serwer: łączę…”** zamiast „Serwer niedostępny”.
- **ArmSX2**: EmuStart nie wpisuje już konta RetroAchievements do `inis/PCSX2.ini` — ArmSX2
  trzyma ustawienia i konto we własnych ustawieniach aplikacji, więc wpis nic nie dawał
  (konto loguje się raz w ArmSX2). Ustawienia osobne dla gry (rozdzielczość, łatki 16:9,
  usuwanie przeplotu) ArmSX2 stosuje przy starcie z EmuStart od wersji **2.8.2** — starsze
  przy starcie z zewnątrz brały tylko ustawienia globalne.
- **Instalator emulatorów na telefonie**: z wydań z wersjami pod poziom Androida (ArmSX2:
  sdk30 / sdk33 / sdk35) bierze najwyższą zgodną z telefonem zamiast pierwszej z listy.

## [0.44.1] — 2026-10-13

### Poprawione
- **Wolne pobieranie gier na telefonie** (od 0.43): każdy 4-MB blok gry szedł nowym
  połączeniem (TCP + TLS + wolny start), bo przy każdym otwarciu połączenia szyfrowanego
  powstawał nowy obiekt sprawdzania nazwy hosta, a pula połączeń Androida używa połączenia
  ponownie tylko przy tym samym. Teraz jeden obiekt — 8 strumieni na stałych połączeniach.
- **„Serwer niedostępny” po wyłączeniu Tailscale** (telefon i komputer): test adresów
  serwera logował się przez każdy badany adres pod wspólną blokadą logowania — martwy adres
  trzymał ją do końca czasu połączenia, testy nie mieściły się w czasie i wybierany był
  martwy adres na 5 minut. Teraz test bez logowania (szybki, niczego nie liczy); gdy żaden
  adres nie odpowiada — ponowienie za 15 s; status serwera na telefonie wraca sam (lista
  systemów z pamięci po błędzie — ponowna próba co 30 s).
- Ustawienia → **Serwer**: którędy idzie połączenie (sieć lokalna / Tailscale / port
  publiczny / tunel).

## [0.44.0] — 2026-10-13

### Dodane
- **Nowe konto z zalogowanego urządzenia** (PC, Android, serwer, panel): *Inny użytkownik* →
  nazwa, której nie ma na serwerze → **Utwórz konto** → hasło (dwa razy, z wymaganiami).
  Urządzenie jest zalogowane kluczem, więc serwer wie, kto prosi; konto powstaje dopiero po
  zatwierdzeniu przez administratora (okienko na jego urządzeniu albo panel — z kontem
  i urządzeniem, które prosi). Po zatwierdzeniu konto samo pojawia się na liście profili
  tego urządzenia (bez ponownego hasła); urządzenie administratora zakłada konto od razu.
  Najwyżej 3 oczekujące prośby z jednego urządzenia.
- **Wygląd na telefonie wspólny dla telefonów konta** (jak wybór emulatorów): zapisywany na
  koncie (`settings/android/look.json`), nowszy wygrywa; przy przełączeniu na innego
  użytkownika — jego wygląd.

### Poprawione
- **PC na urządzeniu konta pokazywał stare lokalne profile** sprzed kont (z kłódką), których
  serwer nie podaje. Lista profili = konto urządzenia i konta zalogowane tu hasłem — tak jak
  na telefonie; lokalne wpisy zostają w bazie.

## [0.43.2] — 2026-10-13

### Poprawione
- **PC po zalogowaniu kontem pokazywał profil pod starą nazwą** (np. „Gracz” zamiast
  „Jezus”): lokalna nazwa profilu sprzed kont została, choć folder profilu na serwerze był
  już przemianowany — wyglądało, jakby konta nie było na liście. Teraz profil konta nosi
  nazwę konta (przy zalogowaniu, przy starcie i na liście „Kto gra?”); inny lokalny profil
  o tej nazwie dostaje dopisek „(lokalny)”. Android bierze nazwy z serwera — bez zmian.

## [0.43.1] — 2026-10-13

Pierwsze wydanie zmian 0.43.0 (jego budowa przerwała się na błędzie opisanym niżej). Zawiera
też niewydane 0.41.1 (poprawka startu na Androidzie) i 0.42.0 (podpis wydań).

### Zmienione
- **Jedna droga do serwera: logowanie kontem** (PC, Android, serwer, panel). W Ustawieniach
  zostały dwa wiersze: **Zaloguj (kod QR)** (telefon) i **Zaloguj (adres serwera)**, a po
  zalogowaniu **Wyloguj z serwera**. Zawsze konto i hasło + klucz urządzenia.
  - **Kod QR z panelu** (Dodaj urządzenie) niesie adresy serwera, odcisk certyfikatu
    i jednorazowy kod (10 min): połączenie od pierwszego zapytania szyfrowane i przypięte,
    bez czekania na zatwierdzenie. Komputer: *Kopiuj link* w panelu i wklejenie w
    **Zaloguj** (albo `EmuStart.exe --server-key` — link do schowka).
  - **Adres** — tylko domena z prawdziwym certyfikatem (tunel); prośba czeka na
    zatwierdzenie urządzenia konta albo administratora. Adres IP bez kodu QR nie jest
    przyjmowany.
- **Administrator to cecha konta**, nie urządzenia (panel → Konta → *Administrator*).
  Pierwsze konto zalogowane kodem QR z panelu, gdy żadne nie jest administratorem, zostaje nim.
- **Tylko szyfrowane połączenia urządzeń**: serwer podaje wyłącznie adresy HTTPS (port 8741,
  publiczny, tunel); zwykły port 8740 obsługuje API tylko z samego serwera (panel).
- Panel: **Urządzenia** (konto, gry z serwera, *Unieważnij*), **Proszą o zalogowanie**,
  **Konta** (hasło, administrator).

### Usunięte
- **Parowanie kodem** (`/v1/pair`, „Połącz z serwerem (kod QR)”, „Połącz kodem”) i
  **dołączanie z zatwierdzeniem kodem z ekranu** (`/v1/pair/request|poll|approve`,
  „Dołącz do serwera”, „Zatwierdź nowe urządzenie”) — dawały rolę administratora bez konta
  i hasła. Rejestracje bez konta są unieważniane przy starcie serwera (urządzenie loguje się
  ponownie). Przejście starych kluczy-haseł (`/v1/auth/upgrade`), role per urządzenie,
  pola „Adres serwera” i „Klucz”, „Sprawdź połączenie”.

### Bezpieczeństwo
- **Podpis wydań** (Ed25519). CI podpisuje zip wydania kluczem z sekretu repozytorium
  (`EMUSTART_SIGNING_KEY`, `tools/release_sign.py`) i dołącza `EmuStart-<wersja>-win64.zip.sig`;
  aktualizacja w programie (aplikacja i panel serwera) sprawdza podpis kluczem publicznym
  wbudowanym w EmuStart.exe i bez poprawnego podpisu niczego nie rozpakowuje. Skrót SHA-256
  z GitHuba chronił tylko przed uszkodzonym pobraniem — podpis chroni też przed podmianą
  wydania po przejęciu konta GitHub. Podpis obejmuje nazwę pliku: podpis starszej wersji nie
  pasuje do nowszej. Budowa z tagu bez sekretu się przerywa.

### Poprawione
- **Android: po starcie samo tło z paskiem** („błąd interfejsu: applyLook is not defined”).
  Start interfejsu wyprzedzał wczytanie `look.js` (pliki w WebView przychodzą
  asynchronicznie); teraz rusza po wykonaniu wszystkich skryptów strony — także na PC.
- **Kontrola tunelu i portu publicznego** (od 0.39): `/v1/info` bez klucza dawało 401 —
  panel pokazywał „tunel: odpowiedź 401”, a każda kontrola liczyła się jako nieudane
  logowanie z adresu domu (blokada adresu). Teraz sonda bez klucza (wyzwanie logowania),
  rozpoznająca odpowiedź serwera EmuStart i niczego nieliczącą.
- Ekran pierwszego uruchomienia nie każe już wpisywać klucza serwera.
- **Pierwsze otwarcie bazy w kilku wątkach naraz** (serwer, pobieranie, interfejs) — migracja
  kolumn potrafiła się wykonać dwa razy („duplicate column name”), a przełączanie w tryb WAL
  kończyło się „database is locked”. Teraz inicjalizacja i migracje (gry, profile, arcade)
  raz — blokada w procesie i transakcja IMMEDIATE.


## [0.41.0] — 2026-10-13

Audyt bezpieczeństwa całości (serwer, PC, Android, interfejs) i sprzątanie kodu. Zawiera też
zmiany z 0.40.0, którego wydanie się nie zbudowało (niestabilny test na CI).

### Bezpieczeństwo
- **Krytyczne: API programu i panel serwera z sieci.** W trybie serwera interfejs, `/api`
  i `/admin` (reset haseł, zatwierdzanie urządzeń, aktualizacja) chronił tylko nagłówek
  `Host`, który ustawia klient — urządzenie z sieci lokalnej albo Tailscale mogło go
  podrobić. Teraz decyduje adres połączenia (tylko ten komputer, nie tunel, nie port publiczny).
- **Wysokie: zatwierdzanie próśb z sieci lokalnej** (urządzenie dostaje rolę administratora)
  — teraz tylko urządzenie administratora, nie dowolne urządzenie konta.
- **Wysokie: ścieżki w folderze profili**, które Windows utożsamia z innymi (`Jezus.`,
  `Jezus ` , `JEZUS~1`, `CON`…), omijały sprawdzanie konta — odrzucane; dawny plik PIN-u
  ukryty bez względu na wielkość liter; nazwy kont bez nazw zarezerwowanych w Windows.
- **Średnie:** identyfikator sprzętu z nagłówka nie unieważnia już innych rejestracji
  (dowolne urządzenie mogło tak odciąć cudze); odpowiedź po zwykłym porcie nie zmienia
  zapamiętanego odcisku certyfikatu ani adresów serwera (PC i Android); sprawdzanie haseł
  ograniczone do 2 naraz (scrypt ≈ 32 MB każde) i łączny limit oczekujących próśb o dołączenie;
  aktualizacja tylko z adresu wydań repozytorium i ze zgodnym skrótem SHA-256 z GitHuba.
- **Niskie:** klucz tylko w nagłówku (bez `?k=` w adresie); błędy 500 bez szczegółów
  wyjątku; Android — adres interfejsu `emustart.local` nigdy nie idzie do sieci (mDNS);
  interfejs — escapowanie logo systemu i wierszy narzędzia „Grafiki”.

### Usunięte
- Martwy kod: dawny PIN serwera w `pinauth` (token, odczyt, zapis, weryfikacja), nieużywane
  funkcje i importy (PC, testy); nieaktualne opisy modułów (wspólny klucz, `?k=`).

### Poprawione
- Test uruchamiania PowerShella w tle czeka do 60 s — pierwszy start na CI bywa wolny
  (przez niego nie zbudowało się wydanie 0.40.0).

## [0.40.0] — 2026-10-12 (niewydane — zmiany w 0.41.0)

### Dodane
- **Hasła kont na serwerze** (PC, Android, serwer, panel). Konto (profil) ma hasło: co
  najmniej 10 znaków, wielka litera, cyfra i znak specjalny — sprawdzane w aplikacji i na
  serwerze. Serwer trzyma tylko skrót (scrypt, losowa sól) w `accounts.json` w katalogu
  serwera, nie w folderze profili; hasło idzie w treści żądania (nigdy w adresie), z
  urządzeń z odciskiem certyfikatu — portem HTTPS. Złe hasła: rosnące opóźnienie na konto
  i limit prób z jednego adresu (z internetu liczone też do blokady adresu).
  - Dołączanie przez internet: **Nowe konto** ustawia hasło, **Mam konto** je sprawdza —
    prośba nadal czeka na zatwierdzenie na innym urządzeniu konta albo przez administratora.
    W prośbie zostaje tylko skrót, usuwany po decyzji.
  - **Inny użytkownik** na urządzeniu konta: nazwa konta i jego hasło → serwer rozpoznaje
    tego użytkownika (token konta, unieważniany zmianą hasła).
  - Konta bez hasła przy pierwszym wyborze proszą o jego ustawienie; panel serwera →
    **Konta**: lista kont i ustawianie nowego hasła przez administratora.

### Zmienione
- **PIN jest tylko lokalny** — blokada profilu na jednym urządzeniu (skrót na urządzeniu,
  pytanie przy wyborze profilu raz na uruchomienie); serwer już go nie sprawdza. Dawne PIN-y
  profili z serwera zastępuje hasło konta.

## [0.39.0] — 2026-10-12

### Zmienione
- **Tylko klucze urządzeń — wszędzie** (komputer, telefon, serwer, panel). Każde połączenie
  z serwerem to klucz urządzenia (TPM/DPAPI na Windows, Android Keystore) i logowanie
  podpisem; serwer nie wydaje już kluczy-haseł:
  - parowanie kodem (panel, QR, `EmuStart.exe --server-key`) i dołączanie z zatwierdzeniem
    na telefonie tworzą urządzenie z kluczem; starsza wersja dostaje „Zaktualizuj EmuStart”,
  - urządzenia ze starym kluczem-hasłem **same przechodzą** na klucz urządzenia przy starcie
    (ten sam wpis w panelu — nazwa, konto, rola; bez duplikatów); do tego czasu stary klucz
    otwiera tylko sprawdzenie serwera i przejście,
  - wspólny klucz serwera działa tylko na komputerze serwera (nie z sieci, nie przez tunel);
    samosprawdzanie portu publicznego i tunelu obywa się bez niego.
- `EmuStart.exe --server-key` pokazuje jednorazowy **kod parowania** zamiast wspólnego klucza.

### Usunięte
- Panel serwera: przycisk **Pokaż klucz**. Aplikacje (PC i Android): pole **Klucz serwera**
  i **Wklej klucz ze schowka** — zamiast nich wiersz **Klucz** z rodzajem klucza urządzenia.

## [0.38.3] — 2026-10-12

### Poprawione
- **Klucz urządzenia odrzucony przez serwer — sprzeczne informacje** (u góry profil, lista
  profili pusta, „Nowy profil”, „Serwer niedostępny”). Teraz komputer i telefon rozpoznają
  odrzucony klucz (401): pasek „Klucz urządzenia nieważny”, jednorazowy komunikat z drogą
  naprawy (Ustawienia → Dołącz przez internet), a ekran profili zamiast pustej listy
  wyjaśnia, co się stało.
- **Panel serwera ostrzega przed unieważnieniem urządzenia w użyciu** — gdy łączyło się
  w ostatnich 6 godzinach, potwierdzenie mówi, kiedy (łatwo pomylić dwie rejestracje
  o tej samej nazwie).

## [0.38.2] — 2026-10-12

Zakres: serwer (`emustart/devices.py`, `enroll.py`, `remote.py`), komputer (`netsrc.py`,
`api.py`), aplikacja na Androida (`Server.kt`, `Bridge.kt`), interfejs i panel serwera.

### Poprawione
- **Ten sam telefon (komputer) zarejestrowany kilka razy** — np. stare parowanie wspólnym
  kluczem i nowe dołączenie przez internet. Urządzenia wysyłają teraz **skrót
  identyfikatora sprzętu** (Android: `ANDROID_ID` — stały dla aplikacji na tym telefonie,
  także po ponownej instalacji; Windows: `MachineGuid` + konto Windows, więc dwa konta na
  jednym komputerze to dwa urządzenia; wysyłany jest tylko skrót SHA-256). Serwer trzyma
  **jedną rejestrację na sprzęt**: nowa rejestracja tego samego sprzętu (dołączenie,
  parowanie) unieważnia poprzednią, a rejestracje sprzed tej wersji dostają identyfikator
  przy najbliższym kontakcie. Dwa różne telefony tego samego modelu (ta sama nazwa) zostają
  osobno.
- Prośba o dołączenie pokazuje zatwierdzającemu (aplikacja i panel serwera), którą
  wcześniejszą rejestrację tego sprzętu zastąpi.

## [0.38.1] — 2026-10-12

### Poprawione
- **Aktualizacja z aplikacji na komputerze pobierała się i nic dalej** oraz **panel serwera
  nie wracał po aktualizacji** — jedna przyczyna: PowerShell uruchamiany bez konsoli
  (`DETACHED_PROCESS`) kończy się od razu, nie wykonując skryptu (skrypt podmiany plików,
  proces czekający na serwer, żeby otworzyć panel). Teraz oba przez jedną funkcję z ukrytą
  konsolą (`CREATE_NO_WINDOW`), nadal niezależnie od zamykanego procesu. Aktualizacja serwera
  działała, bo jej skrypt startuje przez Harmonogram zadań.
- Uwaga: aktualizację *do* tej wersji wykonuje mechanizm poprzedniej — na komputerze zainstaluj
  ją ręcznie (zip z wydania), panel serwera po aktualizacji serwera otwórz jeszcze ręcznie.

## [0.38.0] — 2026-10-12

Zakres: komputer (`emustart/libsync.py` — nowy, `api.py`, `config.py`, `bios.py`,
`server.py`), serwer (`remote.py`), interfejs (`web/app.js`). Etap 2 koncepcji 2.0 —
„cienki klient”.

### Dodane
- **Komputer bez folderów z grami bierze wszystko z serwera** — bez skanowania:
  - **biblioteka**: `GET /v1/library` (systemy z wersją = czas skanowania na serwerze)
    i `GET /v1/library?es=…` (gry: ścieżki, pliki, rozmiary, tagi, znaczniki grafik);
    synchronizacja przy starcie, po zatwierdzeniu nowego urządzenia i z menu Start →
    *Odśwież bibliotekę z serwera* — pobierane są tylko systemy zmienione na serwerze,
    systemy usunięte z serwera znikają;
  - **gry** w całości przez serwer (istniejące pobieranie wieloma strumieniami);
  - **grafiki** gier i własne logo systemów — lokalny serwer interfejsu pobiera brakujący
    plik z serwera i zapamiętuje go;
  - **BIOS-y** rdzeni RetroArcha z serwera, gdy komputer nie ma własnego folderu BIOS-ów;
  - **emulator i rdzeń dla systemu** zalecane przez serwer (jak na komputerze
    administratora) przy wykrywaniu emulatorów.
- Komputer z serwerem, bez folderów z grami, jest „skonfigurowany” (nie wraca do ekranu
  pierwszego uruchomienia).
- Konto zatwierdzone bez gier nie dostaje biblioteki (serwer: 403).

### Poprawione
- **Odmowy serwera przed przeczytaniem treści zapytania** (POST/PUT/DELETE: obcy klient,
  brak nagłówka interfejsu, nieznana funkcja, port publiczny bez klucza) — Windows potrafił
  zerwać połączenie (RST) zamiast oddać kod błędu. Teraz jedna funkcja odmowy najpierw
  doczytuje treść (do 1 MB), dopiero potem odpowiada — we wszystkich tych miejscach.

### Ograniczenia
- Emulatory trzeba mieć na komputerze (folder emulatorów); instalacja z serwera — etap 3.
- Automaty (arcade): zestawy rodzica i BIOS-y automatów (np. `neogeo.zip`) jeszcze nie są
  pobierane dla gier z biblioteki serwera.

## [0.37.5] — 2026-10-12

Zakres: komputer (`emustart/api.py`, `profiles.py`), aplikacja na Androida (`Bridge.kt`),
interfejs (`web/app.js`, `web/options.js`). Urządzenie przypisane do konta — wszędzie tak samo.

### Poprawione
- **Po zatwierdzeniu nowego urządzenia konto staje się jego profilem** — na komputerze
  i telefonie. Wcześniej komputer pokazywał wybór profili z pustym „Gracz” z pierwszego
  uruchomienia (bez konta z serwera), a telefon zapisywał konto, nie ustawiając profilu.
  Pusty „Gracz” jest usuwany. Gdy zatwierdzenie przyszło bez połączenia — dokończenie przy
  starcie.
- **Kłódka / prośba o PIN serwera przy profilu konta** — urządzenie z kluczem tego konta
  nie potrzebuje PIN-u serwera (komputer i telefon); PIN jest lokalny.
- **Pasek stanu u klienta serwera** (bez folderów z grami): „Serwer <nazwa>” / „Serwer
  niedostępny” zamiast „NAS offline, tylko gry lokalne” — komputer i telefon.
- **Ekran profili na urządzeniu przypisanym do konta**: tylko to konto — bez „Nowy profil”,
  „Zmień nazwę”, „Usuń” (serwer i tak na to nie pozwala) — komputer i telefon.

## [0.37.4] — 2026-10-12

### Bezpieczeństwo
- **Nowa instalacja niczego nie zakłada.** Domyślne ustawienia zawierały ścieżki z pierwszej
  instalacji — folder z grami `Z:\ROMS\ROMS`, emulatory `D:\emu\emulatory`, folder
  profili `Z:\emustart\Profiles` — więc świeży EmuStart od razu sięgał po gry, emulatory
  i profile z udziału NAS i dysku. Teraz domyślne są puste: bez folderów z grami, bez
  wykrywania emulatorów, bez folderu profili przez SMB. Istniejące ustawienia, które na tych
  wartościach polegały, zachowują je (dopisywane przy wczytaniu).
- **Folder profili przeniesiony na serwer nie jest już używany przez SMB.** Stary folder
  (ze znacznikiem `.emustart-moved`, panel serwera → przeniesienie profili) jest traktowany
  jako niedostępny — żadnych odczytów ani zapisów (wcześniej świeża instalacja wysłała tam
  pliki profilu).
- Uwaga: dostęp do gier i zapisów przez sam udział SMB (np. zamapowany dysk z zapisanym
  hasłem) omija serwer EmuStart — do zamknięcia po stronie udziału.

### Poprawione
- **Część logo gier nie trafiała na serwer (błąd 400).** Grafiki są zapisywane jako `*.png`,
  ale część źródeł daje WebP albo JPEG — serwer przyjmował wyłącznie PNG (np. 1602 logo gier
  z Amigi). Teraz przyjmuje PNG, JPEG, WebP i GIF.

## [0.37.3] — 2026-10-12

### Zmienione
- **Telefon: opis platformy w 3 liniach** (było: 1), karuzela z logo systemów wyżej — pod
  nią nazwa, liczba gier i emulator, producent / lata / nośnik i opis.

### Poprawione
- Komputer bez skonfigurowanego emulatora systemu (np. świeża instalacja) nie kasuje na
  serwerze zalecanego emulatora i rdzenia tego systemu.

## [0.37.2] — 2026-10-12

Zakres: aplikacja na Androida (`Bridge.kt`, `Emulators.kt`, `Downloader.kt`, `Prefs.kt`),
serwer (`remote.py`), komputer (`logos.py`).

### Poprawione
- **Wolne pobieranie gier przez internet (ok. 30 zamiast 80 MB/s).** Telefon zamykał
  połączenie po każdym bloku 4 MB — każdy blok to było nowe połączenie TCP, nowe uzgadnianie
  TLS i wolny start. Teraz połączenia wracają do puli i kolejne bloki idą nimi (pula: 16
  połączeń, było 5 przy 8 strumieniach).
- **Telefon „sam zmieniał” rdzeń.** Wybór pierwszego rdzenia z listy zapisywał się jako sam
  „retroarch” — gdy serwer podał rdzenie w innej kolejności, ten sam zapis oznaczał inny
  rdzeń. Teraz rdzeń jest zawsze w zapisie; stare wybory są raz utrwalane.
- **Brak szczegółów platformy na telefonie** (producent, lata, nośnik, opis) — telefon
  pobiera je z serwera (`GET /v1/sysinfo`), jak komputer.

### Dodane
- **Rdzeń jak na komputerze.** Komputer zgłasza serwerowi emulator i rdzeń każdego systemu
  (razem z wyglądem systemu); serwer podaje je telefonom jako zalecane. Telefon bez własnego
  wyboru bierze zalecany (np. RetroArch z bsnes, jak na PC — stany zgodne).
- **Ostrzeżenie o rozbieżności — od razu przy wyborze.** W ustawieniach emulatorów opcja
  zgodna z komputerem ma dopisek „jak na komputerze”, inne — „⚠ inny rdzeń niż na
  komputerze — stany z PC się nie wczytają”; to samo ostrzeżenie w opisie platformy.
- **Wybór emulatora / rdzenia na koncie.** Wybór na telefonie zapisuje się na serwerze
  (`<profil>/settings/android/emulators.json`, nowszy wygrywa) — nowy telefon tego konta
  dostaje go od razu.

## [0.37.1] — 2026-10-12

### Poprawione
- **Brak logo platform na telefonie połączonym przez internet** (tunel / port publiczny).
  Port publiczny przepuszczał tylko `/v1/` i `/media/`, a logo wbudowane w EmuStart (23
  systemy) są pod `/assets/`, logo z paczki — pod `/local/`. Teraz przepuszcza też te
  ścieżki (nadal tylko z kluczem urządzenia). Przez Tailscale i w sieci lokalnej działało.

## [0.37.0] — 2026-10-12

Zakres: serwer (`emustart/enroll.py` — nowy, `devices.py`, `remote.py`, `public.py`,
`server.py`, `srvadmin.py`), klient na Windows (`emustart/keystore.py` — nowy, `netsrc.py`,
`nasfs.py`, `api.py`), interfejs (`web/app.js`), panel serwera (`web/server.*`), aplikacja na
Androida (`DeviceKey.kt` — nowy, `Server.kt`, `Bridge.kt`, `Prefs.kt`).

### Dodane
- **Nowe urządzenie przez internet, znając tylko adres serwera** (np. tunelu). Ustawienia →
  *Dołącz przez internet* → *Mam konto* / *Nowe konto*. Urządzenie tworzy własną parę
  kluczy EC P-256 — klucz prywatny nie opuszcza urządzenia: **Windows — TPM** (gdy jest
  i działa), bez niego klucz zaszyfrowany **DPAPI**; **Android — Keystore** (StrongBox, gdy
  telefon go ma); przyszły Linux — plik tylko dla właściciela. Serwer zna tylko klucz
  publiczny.
- **Zatwierdzanie z kodem weryfikacyjnym** (z klucza publicznego — ten sam na obu
  ekranach). Prośbę widać w aplikacji EmuStart przy otwarciu (i co minutę) oraz w panelu
  serwera: czas, IP, nazwa, rodzaj urządzenia, konto, kod. Zatwierdzenie *z grami* albo
  *tylko zapisy i profil*. Nowe konto — tylko administrator; „Mam konto” — inne urządzenie
  tego konta, a gdy konto nie ma żadnego — administrator. Prośba czeka 24 h (na dysku
  serwera — przetrwa restart i aktualizację), urządzenie sprawdza jej stan samo.
- **Logowanie podpisem:** wyzwanie od serwera → podpis kluczem urządzenia → token sesji
  (1 h, odnawiany sam; po restarcie serwera — nowe logowanie). Skopiowane dane aplikacji nie
  wystarczą do podszycia się bez klucza prywatnego z tego urządzenia.
- **Konta i role:** *administrator* (urządzenie zero — sparowane kodem z panelu; rolę nadaje
  panel) i *użytkownik* — tylko swoje konto: folder profilu, menedżer zapisów, lista
  profili; gry z serwera — tylko z uprawnieniem; bez zmian w ustawieniach serwera.
  Dla urządzenia z kluczem PIN profilu nie jest potrzebny na serwerze (PIN — lokalnie).
- Panel serwera: sekcja *Nowe instalacje z internetu* (Zatwierdź z grami / Bez gier /
  Odrzuć), kolumna *Konto / rola*, nadawanie i odbieranie roli administratora.
- API: `/v1/enroll/request|poll` i `/v1/auth/challenge|token` — bez klucza (także przez
  tunel i port publiczny), `/v1/enroll/pending|decide` — z kluczem.

### Bezpieczeństwo
- Wyzwania logowania jednorazowe (kilka naraz na urządzenie), zły podpis liczy się do blokady
  adresu; unieważnienie urządzenia kończy jego sesje.
- Urządzenia sparowane przed 0.37 działają jak dotąd (pełny dostęp) — przejście na klucze
  urządzeń, wyłączenie wspólnego klucza serwera i PIN tylko lokalnie: 0.38.

## [0.36.1] — 2026-10-12

### Poprawione
- **Telefon bez Tailscale nie łączył się z serwerem.** Aplikacja na Androida uczyła się listy
  adresów serwera (sieć lokalna, port publiczny, tunel) tylko przy ręcznym „Sprawdź
  połączenie” — telefon sparowany wcześniej znał wyłącznie adres Tailscale. Teraz po każdym
  udanym połączeniu (najwyżej raz na godzinę) pobiera aktualną listę adresów i odcisk
  certyfikatu serwera, jak EmuStart na Windows.

## [0.36.0] — 2026-10-12

Zakres: serwer (`emustart/public.py`, `remote.py`, `srvadmin.py`, `main.py`), panel serwera
(`web/server.*`), klient na Windows (`netsrc.py`), aplikacja na Androida (`Net.kt`,
`Server.kt`, `Bridge.kt`).

### Dodane
- **Tunel Cloudflare jako adres serwera.** Panel serwera → *Dostęp z internetu* → *Adres
  tunelu* (`server_tunnel_url`, np. `https://emustart.emucloud.org`; tunel prowadzi do portu
  publicznego `https://localhost:8742`). Serwer sprawdza, czy tunel działa, i podaje go
  urządzeniom w liście adresów jako rodzaj „tunnel” — ze zwykłym certyfikatem (Cloudflare),
  bez przypinania. Urządzenia biorą go na końcu: sieć lokalna → Tailscale → port publiczny →
  tunel, więc gry idą bezpośrednio, gdy tylko się da.
- Komputer i telefon: adres serwera wpisany jako nazwa domeny (np. `emustart.emucloud.org`)
  to https na porcie 443; adres IP / nazwa komputera — jak dotąd http na 8740.

### Bezpieczeństwo
- **Prawdziwy adres klienta przez tunel.** `cloudflared` łączy się z serwerem z 127.0.0.1,
  więc blokada za serię złych kluczy zablokowałaby wszystkich przychodzących tunelem. Teraz
  dla połączeń z tego komputera serwer bierze adres z nagłówka `CF-Connecting-IP` (blokady,
  „ostatnio z internetu”).
- Adres https wpisany ręcznie, którego serwer nie podał jako własny, jest sprawdzany
  zwykłym trybem certyfikatów (nie przypinaniem).

## [0.35.3] — 2026-10-12

### Bezpieczeństwo
- **Zwykły (8740) i szyfrowany (8741) port przyjmują tylko sieć lokalną, Tailscale i ten
  komputer.** Gdy router przekierowuje na serwer ruch z internetu (DMZ, zakres portów,
  ręczne przekierowanie), zapytania z zewnątrz dostają odmowę, a log serwera ostrzega (raz na
  godzinę). Z internetu — wyłącznie port publiczny (8742: HTTPS, tylko z kluczem urządzenia,
  bez parowania). Wcześniej z internetu działało m.in. parowanie po zwykłym HTTP.

### Poprawione
- **„Nie znaleziono routera z UPnP”, choć UPnP był włączony.** Wyszukiwanie wysyła teraz
  zapytanie z każdej karty sieciowej osobno (domyślną bywa karta Tailscale albo wirtualna),
  powtarza je do 3 razy, a znaleziony router zapamiętuje (`server\router.json`) i przy
  następnym starcie łączy się z nim od razu. Bez routera kolejna próba po minucie
  (było: 5 minut).

## [0.35.2] — 2026-10-12

### Poprawione
- **Panel serwera nie wracał po aktualizacji** (serwer aktualizował się i startował sam od
  0.34.2, okno panelu trzeba było otwierać ręcznie). Teraz panel przed zamknięciem zapisuje,
  na czyim koncie działa, a skrypt aktualizacji po starcie serwera otwiera go w sesji tego
  użytkownika jednorazowym zadaniem Harmonogramu „EmuStart Panel” (tylko gdy użytkownik jest
  zalogowany). Zapasowo — jak dotąd — proces na koncie użytkownika czeka na nową wersję
  serwera (do 10 minut); nie otwiera drugiego okna, gdy panel już działa.
- Przebieg powrotu panelu w `logs\update.log` (wpisy „panel: …”), zamknięcie panelu
  w `logs\panel.log`.
- Uwaga: działa od aktualizacji *z* tej wersji (skrypt i panel poprzedniej wersji robią
  to po staremu).

## [0.35.1] — 2026-10-12

Zakres: interfejs (`web/look.js`, `web/app.js`, `web/app.css`, `web/index.html`), grafiki
gier komputer → serwer (`emustart/artsync.py`, `remote.py`, `api.py`).

### Dodane
- **Logo gier na telefonie.** Komputer pobiera logo gier sam (serwer miał ich 0), więc
  aplikacja na Androida nie miała czego pokazać. Teraz komputer po starcie wysyła na serwer
  grafiki gier, których serwer nie ma (logo, brakujące okładki i zrzuty) — tylko brakujące,
  grafiki serwera zostają. Grafika wybrana ręcznie w opcjach gry trafia na serwer od razu.
  API: `GET /v1/media/have?es=…`, `PUT /v1/media?es=…&file=…` (tylko PNG, tylko istniejące
  gry, nazwa bez ścieżki).
- **Telefon: logo gry pod szczegółami** (zamiast w nagłówku listy) — zajmuje miejsce, które
  zostaje pod listą szczegółów, i zmniejsza się, gdy go brakuje.

### Poprawione
- **Edytor wyglądu na telefonie dało się obsłużyć tylko padem.** Dotknięcie zaznaczało wiersz,
  ale wartości nie zmieniało. Teraz każdy suwak ma przyciski − / +, wybory mają ◀ / ▶,
  a dotknięcie paska ustawia wartość wprost — np. *Wysokość wiersza listy* (odstępy między
  tytułami) i *Lista gier* (czcionka). Działa też myszą na komputerze.

## [0.35.0] — 2026-10-12

Zakres: serwer (`emustart/upnp.py`, `public.py`, `server.py`, `remote.py`, `srvadmin.py`,
`main.py`), panel serwera (`web/server.*`).

### Dodane
- **Dostęp z internetu** (panel serwera → *Dostęp z internetu*, `server_public`). Serwer
  otwiera osobny port szyfrowany (domyślnie 8742, `server_public_port`), prosi router przez
  UPnP o przekierowanie i odnawia je co 20 minut (wygasa samo po wyłączeniu serwera; routery
  tylko ze stałymi przekierowaniami — obsłużone). Adres zewnętrzny trafia do listy adresów
  serwera jako „publiczny” — urządzenia biorą go dopiero, gdy sieć lokalna i Tailscale nie
  odpowiadają. Reguła zapory dla portu dodawana automatycznie.
- UPnP bez dodatkowych bibliotek (`emustart/upnp.py`): SSDP, opis routera, SOAP
  (adres zewnętrzny, dodanie i usunięcie przekierowania).
- Panel: stan — router, adres zewnętrzny, przekierowanie, osiągalność (własna próba przez
  adres zewnętrzny albo ostatnie połączenie z internetu), zablokowane adresy, błędy.
- Wykrywanie CGNAT (adres zewnętrzny routera prywatny / 100.64.0.0/10) — panel wyjaśnia,
  że z internetu się nie da.

### Bezpieczeństwo
- Port publiczny: tylko HTTPS z przypiętym certyfikatem, tylko API /v1 i grafiki, każde
  zapytanie z kluczem urządzenia; bez parowania, panelu i interfejsu. 20 złych kluczy
  w 10 minut z jednego adresu = blokada na 30 minut (zrywana jeszcze przed TLS); najwyżej
  32 połączenia naraz z jednego adresu.

## [0.34.2] — 2026-10-12

### Poprawione
- **Aktualizacja serwera z panelu pobierała, zamykała serwer i na tym koniec.** Skrypt
  podmiany startował jako proces potomny serwera uruchomionego z Harmonogramu zadań i ginął
  razem z nim, zanim zdążył cokolwiek zrobić (brak `logs\update.log`), a serwer już się nie
  podnosił. Teraz skrypt startuje jako osobne, jednorazowe zadanie Harmonogramu „EmuStart
  Update” (konto SYSTEM), niezależne od serwera, i usuwa je po sobie.
- **Serwer zamyka się dopiero po potwierdzeniu startu skryptu** (wpis w `logs\update.log`).
  Gdy potwierdzenie nie przyjdzie w 30 s, serwer działa dalej na obecnej wersji, a panel
  pokazuje błąd — nigdy więcej wyłączonego serwera po nieudanej aktualizacji.
- Uwaga: aktualizację *do* tej wersji wykonuje jeszcze skrypt poprzedniej; jeśli serwer nie
  wróci, uruchom zadanie „EmuStart Server” ręcznie (albo uruchom komputer ponownie).

## [0.34.1] — 2026-10-12

### Poprawione
- **Zapora Windows blokowała port szyfrowany.** Reguła zapory z instalacji autostartu
  dopuszcza tylko zwykły port (8740). Serwer (konto SYSTEM) dopisuje teraz przy starcie
  regułę „EmuStart Server (szyfrowany)” dla portu szyfrowanego; instalacja autostartu
  dodaje obie.

## [0.34.0] — 2026-10-12

Zakres: serwer (`emustart/tls.py`, `server.py`, `remote.py`, `main.py`), klient na Windows
(`netsrc.py`, `nasfs.py`, `logos.py`, `api.py`), aplikacja na Androida (`Net.kt`,
`Server.kt`, `Prefs.kt`, `Bridge.kt`). Pierwszy krok do dostępu spoza Tailscale.

### Dodane
- **Port szyfrowany serwera (HTTPS, domyślnie 8741)** obok zwykłego. Serwer tworzy przy
  pierwszym starcie własny certyfikat (EC P-256, `server\tls-cert.pem`); urządzenia ufają mu
  po odcisku SHA-256 zapamiętanym przy parowaniu (przypięcie), więc HTTPS działa także pod
  gołym adresem IP. Uzgadnianie TLS w wątku połączenia — obcy klient nie blokuje serwera.
  Port: `server_tls_port` w ustawieniach serwera.
- **Lista adresów serwera i automatyczny wybór.** `/v1/info` i odpowiedzi parowania podają
  adresy serwera (sieć lokalna, Tailscale; zwykły i szyfrowany) oraz odcisk certyfikatu.
  Komputer i telefon sprawdzają je równolegle i biorą najlepszy działający (sieć lokalna →
  Tailscale → publiczny, zwykły port przed szyfrowanym), bez czekania na nieosiągalne;
  ponowny wybór co 5 minut i po błędzie połączenia.
- Biblioteka `cryptography` w EmuStart.exe (certyfikat serwera).

### Bezpieczeństwo
- Adres HTTPS bez zapamiętanego odcisku nie jest używany; niezgodny certyfikat = odmowa
  połączenia z komunikatem „połącz urządzenie z serwerem ponownie”.
- Adresy nauczone od serwera komputer trzyma w osobnym pliku `data\serwer-adresy.json`
  (nie w `config.json`).

## [0.33.0] — 2026-10-12

Zakres: parowanie urządzeń (`emustart/devices.py`, `remote.py`, `server.py`, `netsrc.py`,
`api.py`), interfejs (`web/app.js`, `web/index.html`), panel serwera (`web/server.*`),
aplikacja na Androida (`Bridge.kt`, `Server.kt`).

### Dodane
- **Dołącz do serwera — zatwierdzenie telefonem** (jak logowanie telewizora). Nowy komputer
  (Ustawienia → Serwer EmuStart → *Dołącz do serwera*) wysyła prośbę i pokazuje kod oraz QR;
  sparowany telefon skanuje go (*Zatwierdź nowe urządzenie*), widzi nazwę i adres nowego
  urządzenia i zatwierdza albo odrzuca. Komputer sam odbiera własny klucz — bez przepisywania
  kodu z panelu serwera.
- Zatwierdzanie także kodem na innym sparowanym komputerze i w panelu serwera (sekcja
  *Proszą o dołączenie* z przyciskami Zatwierdź / Odrzuć). Panel odświeża listę co 4 s.
- API: `POST /v1/pair/request`, `/v1/pair/poll` (bez klucza), `/v1/pair/approve` (tylko
  sparowane urządzenie). Prośba ważna 10 minut, klucz oddawany raz, najwyżej 5
  oczekujących próśb z jednego adresu; w rejestrze urządzeń zapisane, kto zatwierdził.

## [0.32.3] — 2026-10-12

Zakres: stany RetroArcha wspólne dla urządzeń (`emustart/rastates.py`, `launcher.py`),
aplikacja na Androida (`EmuProfile.kt`), aktualizacja serwera (`updater.py`).

### Poprawione
- **Autozapis z telefonu nie wczytywał się na komputerze, a przy wyjściu był nadpisywany.**
  Komputer kopiował autozapis (`.state.auto`) z serwera we właściwe miejsce, ale RetroArch
  startował z wyłączonym autowczytywaniem (włączane tylko po „szybki zapis i wyjście”),
  więc gra zaczynała się od nowa, a autozapis z tej sesji nadpisywał wspólny. Teraz, gdy
  autozapis tej gry (ten sam rdzeń) jest na miejscu i aktualny, RetroArch wczytuje go przy
  starcie.
- **Stan z sesji nie nadpisuje innej wersji na serwerze.** Po grze stan trafia do wspólnego
  miejsca tylko wtedy, gdy sesja wystartowała z wersji, która nadal jest na serwerze. Jeśli
  wersja z serwera się nie pobrała, gra ruszyła bez serwera albo inne urządzenie zapisało
  w międzyczasie — stan z komputera idzie do kopii zapasowej (`_backup/…/konflikt/`),
  wspólny zostaje bez zmian, a przy następnym uruchomieniu gry pojawia się komunikat.
- **Telefon: autozapis i autowczytywanie RetroArcha zawsze włączone** w pliku ustawień
  profilu, który aplikacja podaje RetroArchowi (jak na komputerze). Telefon już wcześniej
  nie nadpisywał nowszej wersji z serwera (zostaje serwer, wersja z telefonu w kopii).
- **Aktualizacja serwera:** po podmianie plików skrypt sprawdza, czy serwer naprawdę ruszył
  z zadania Harmonogramu; jeśli nie (zadanie wciąż „uruchomione”), uruchamia go
  bezpośrednio. Log podmiany z kodami robocopy: `logs\update.log`.

## [0.32.2] — 2026-10-12

Zakres: panel serwera (`web/server.js`). Bez zmian w działaniu serwera.

### Poprawione
- Panel serwera: potwierdzenie restartu / aktualizacji wspominało o „trwającym pobieraniu
  na telefonie” zawsze — także gdy nic się nie pobierało (wyglądało jak błąd). Teraz tylko
  gdy któreś urządzenie naprawdę pobiera grę, z jej nazwą.
- Opis wydania w sekcji Aktualizacja bez znaczników Markdown (`###`, `**`, `` ` ``).

## [0.32.1] — 2026-10-12

Zakres: `requirements.txt`, `emustart.spec`, testy. Bez zmian w działaniu poza obrazkiem QR.

### Dodane
- **Kod QR parowania w panelu serwera** — biblioteka `segno` (czysty Python, BSD) wchodzi do
  `EmuStart.exe`, więc komputer z serwerem nie potrzebuje Pythona ani instalowania czegokolwiek.
  Gdyby jej zabrakło (uruchamianie ze źródeł), panel pokazuje sam kod do wpisania.

## [0.32.0] — 2026-10-12

EmuStart 2.0, etap 1 ([koncepcja](docs/koncepcja-2.0.md)). Zakres: `emustart/devices.py`
(nowy), `emustart/qr.py` (nowy), `emustart/paths.py`, `main.py`, `emustart/remote.py`,
`emustart/server.py`, `emustart/srvadmin.py`, `emustart/nasfs.py`, `emustart/netsrc.py`,
`emustart/api.py`, panel serwera (`web/server.*`), ustawienia (`web/app.js`), Android
(`Bridge.kt`, `Server.kt`, `build.gradle.kts`), testy (`tests/test_three_devices.py`).
Bez zmian: lista gier, uruchamianie, pobieranie gier, synchronizacja zapisów (poza wyborem
drogi: serwer / SMB).

### Dodane
- **Własny klucz dla każdego urządzenia i parowanie kodem QR** — panel serwera → Sparowane
  urządzenia → Dodaj urządzenie (kod jednorazowy, 10 min, tekst i kod QR); telefon:
  **Połącz z serwerem (kod QR)** (skaner Google Play) albo **Połącz kodem**; komputer:
  **Połącz kodem**. Panel pokazuje urządzenia (rodzaj, wersja, ostatni kontakt), pozwala
  zmienić nazwę i **unieważnić** pojedyncze urządzenie. Serwer trzyma tylko skróty kluczy;
  błędne kody — przerwa po 10 próbach. Wspólny klucz serwera działa dalej.
- **Katalog serwera `server\`** — ustawienia, biblioteka, grafiki i rejestr urządzeń w jednym
  podkatalogu programu (przenośny serwer). Przy pierwszym starcie serwera `config.json`
  i `data\` przenoszone automatycznie.
- **Przeniesienie profili do katalogu serwera** (panel → Folder profili) — po potwierdzeniu,
  z kopiowaniem i sprawdzeniem każdego pliku; stary folder zostaje jako zapas.
- Test „trzech urządzeń” (serwer + komputer + telefon): parowanie, klucze, unieważnienie,
  zapis z komputera czytany na telefonie i odwrotnie, przeniesienie katalogu i profili.

### Zmienione
- **Klienci z ustawionym serwerem zapisują profile tylko przez serwer** — bez SMB (także gdy
  przez SMB widać folder profili); serwer chwilowo niedostępny = tryb offline (kolejka).
- Zapytania klientów niosą wersję EmuStart (`X-EmuStart-Version`) — widoczną w panelu.

## [0.31.2] — 2026-10-12

Zakres: nagłówek listy gier i podgląd na komputerze (`web/index.html`, `web/app.css`, `web/app.js`);
testy (`tests/conftest.py`). Bez zmian: telefon (logo gry zostaje w nagłówku listy).

### Zmienione
- **Logo gry nad okładką** — na szerokość okładki, od górnej krawędzi ekranu do okładki
  (obok logo platformy było za małe i nieczytelne).
- **Nagłówek listy gier**: większe logo platformy, bez nazwy platformy (jest na logo) —
  zostaje liczba gier. Gdy system nie ma logo, nazwa jest jak dotąd.

### Poprawione
- Testy nie zapisują już prawdziwych plików EmuStart: uruchamiane z folderu programu
  nadpisywały `config.json` (klucz testowy zamiast ustawień — EmuStart pokazywał „pierwsze
  uruchomienie”). Każdy test dostaje własny, tymczasowy folder programu (`tests/conftest.py`),
  a po testach sprawdzane jest, że prawdziwe `config.json` i baza się nie zmieniły.

## [0.31.1] — 2026-10-12

Zakres: stany RetroArcha (`emustart/rastates.py` — nowy, `emustart/launcher.py`,
`emustart/savemgr.py`, Android `Saves.kt`), edytor wyglądu na telefonie (`web/look.js`,
`web/app.css`, `web/app.js`). Bez zmian: zapisy w grze, karty pamięci, stany PCSX2/ARMSX2.

### Zmienione
- **Stany RetroArcha wspólne dla komputera i telefonu, niezależnie od ustawień RetroArcha.**
  Na serwerze jedno miejsce w stałym układzie: `<profil>/states/shared/retroarch/<rdzeń>/`.
  Każde urządzenie zgłasza swój układ stanów w `.layouts.json` (płasko / folder rdzenia /
  folder gry / folder gry + rdzenia): komputer z `retroarch.cfg` (`savestate_directory`,
  `sort_savestates_enable`, `sort_savestates_by_content_enable`), telefon — z miejsca,
  w którym RetroArch zapisał stan. Przed grą nowsze stany tej gry i tego rdzenia trafiają
  tam, gdzie RetroArch danego urządzenia ich szuka (poprzednia wersja do kopii zapasowej),
  po grze zmienione wracają na serwer. Gdy stan jest tylko z innego rdzenia — komunikat
  zamiast cichego „nie widzi”. Menedżer zapisów pokazuje wspólne stany.

### Poprawione
- **PC nie widział stanu RetroArcha z telefonu**: telefon wysyłał stan płasko (bez folderu
  rdzenia), a menedżer zapisów kopiował go do „najczęstszego” folderu rdzeni — np. stan SNES
  do folderu PUAE (Amiga). Menedżer nie zgaduje już folderu: tylko folder rdzenia stanu,
  a dla starych stanów bez rdzenia — folder, w którym komputer ma stany tej gry, gdy jest
  dokładnie jeden.
- **Edytor wyglądu na telefonie**: suwaki układu (logo w karuzeli i nad listą, wysokość
  wierszy, szerokość listy, wysokość okładki i zrzutu, wiersze ustawień, linie opisu) nic
  nie zmieniały — układ telefonu nadpisywał ich wartości. Teraz przeliczają się na wymiary
  telefonu (100 % = domyślny układ telefonu).

## [0.31.0] — 2026-10-12

Zakres: panel serwera (`web/server.html`, `web/server.js`, `emustart/srvadmin.py`), nowy
`emustart/updater.py`, okno panelu w `main.py`; pozycja „Aktualizuj EmuStart” w menu Start
(`web/app.js`, `emustart/api.py`, Android: `Bridge.kt`, `Installer.kt`). Bez zmian: lista
gier, pozostałe pozycje menu, sterowanie, API dla telefonu.

### Dodane
- **Aktualizacja serwera z panelu** (`EmuStart.exe --server-panel` → sekcja Aktualizacja):
  sprawdzenie najnowszego wydania na GitHubie (C4rl0s79/EmuStart), pobranie
  `EmuStart-<wersja>-win64.zip` z postępem, sprawdzenie archiwum, podmiana plików programu
  (`EmuStart.exe`, `_internal`) przez skrypt po zamknięciu serwera — z kopią zapasową
  i przywróceniem przy błędzie — oraz ponowne uruchomienie serwera (zadanie Harmonogramu
  „EmuStart Server”, a bez niego proces z tymi samymi parametrami). `config.json`, `data`,
  `cache`, `logs` zostają nietknięte. Panel zamyka się na czas podmiany i otwiera sam po
  starcie nowego serwera. Przebieg w `logs/update.log`.
- **Aktualizacja w EmuStart na komputerze i telefonie** — menu Start → „Aktualizuj
  EmuStart” (nad „Wyjdź z EmuStart”), sprawdzanie tylko na żądanie. Komputer: pobranie
  ZIP-a z postępem, zamknięcie EmuStart, podmiana plików jak na serwerze (kopia,
  przywrócenie przy błędzie) i ponowne uruchomienie z tymi samymi parametrami. Telefon:
  pobranie `EmuStart-<wersja>-android.apk` z wydania i systemowy instalator (jak przy
  instalacji emulatorów). Poza tą pozycją menu nic się nie zmienia.

## [0.30.6] — 2026-10-11

Zakres: ścieżki plików gier wysyłane telefonowi (`emustart/remote.py`) i ich odczyt na
Androidzie (`Server.game()`). Bez zmian: lista gier, menu, sterowanie, tłumaczenia, Windows.

### Naprawione
- **Android: gry z podfolderów (np. kolekcja `[T-En]Collection`) nie startowały** —
  „open failed: EPERM” dla `…\NEC - PC-9801 [T-En] Collection\Gra.zip.part`. Serwer na
  Windows wysyłał ścieżki z `\`, a Android traktuje `\` jako znak w nazwie pliku.
  Serwer wysyła teraz ścieżki z `/`, a telefon zamienia `\` na `/` także dla starszego
  serwera.

## [0.30.5] — 2026-10-11

Zakres: poprawki z przeglądu kodu 0.30.4 — `emustart/launchbox.py`, fokus okna
w `emustart/api.py`, `requirements.txt`. Bez zmian: lista gier, menu, sterowanie, tłumaczenia.

### Naprawione
- **Baza LaunchBox — połączenie na zapytanie zamiast połączeń wątków** (0.30.4 trzymało
  połączenie każdego wątku do końca sesji — wyciek przy przewijaniu listy; podmiana bazy
  zamykała połączenia innych wątków w trakcie zapytania). Odczyty i podmiana pliku
  przechodzą przez bramkę: podmiana czeka na trwające odczyty, nowe odczyty — na koniec
  podmiany (bez trzymania blokady przy ponawianiu). `status()`/`ready()` też przez bramkę.
- **Fokus okna**: obsługa aktywacji podpinana raz (bez dublowania przy kolejnym
  pokazaniu okna); kontrolka WebView2 szukana na dwa sposoby, brak — ostrzeżenie w logu.
  pywebview przypięty do 6.x (poprawka korzysta z jego wewnętrznych pól).

## [0.30.4] — 2026-10-11

Zakres: tylko poniższe. Bez zmian: lista gier, menu, tłumaczenia.

### Naprawione
- **Klawiatura i kółko myszy przestawały działać po grze** (pad działał) — po powrocie
  okna na wierzch (koniec gry, menu w grze, Alt+Tab) fokus dostawał formularz okna, a nie
  strona w WebView2; pywebview oddaje go stronie tylko przy pierwszym pokazaniu okna.
  Teraz każda aktywacja okna przekazuje fokus stronie.
- **„Aktualizuj bazę LaunchBox” kończyła się błędem WinError 32** (plik `launchbox.db`
  używany przez inny proces) — baza była otwarta do odczytu w wątkach pobierania opisów
  i grafik, a Windows nie podmienia otwartego pliku. Przed podmianą połączenia są
  zamykane, wątki otwierają nową bazę same.

### Diagnostyka
- W `logs/emustart.log` przy starcie: wersje Pythona, pywebview, pythonnet i clr_loader
  (binarka z GitHub Actions bierze najnowsze — do sprawdzenia niedziałającej klawiatury).

## [0.30.3] — 2026-10-11

### Naprawione
- **Czytelna lista gier z tłumaczeniami** — na liście bez dopisku tłumaczenia:
  „Bahamut Lagoon (Japan)” zamiast „… [T-En by DeJap v1.2]”. Pełne oznaczenia (język,
  autor, wersja) są w szczegółach gry obok listy.

## [0.30.2] — 2026-10-11

### Naprawione
- **Kolekcja tłumaczeń „T-Eng” nie była rozpoznawana** — folder z `T-Eng`, `T-En`, `T+Pol`
  w nazwie (bez nawiasów) jest teraz kolekcją tłumaczeń, także dodany jako osobny folder
  z grami; język z nazwy folderu.
- **Kolekcja `[T-En]Collection` z RomVaulta** — foldery systemów w rodzaju „Nintendo -
  Super Famicom [T-En] Collection” są rozpoznawane (nazwy Famicom, Super Famicom, Mega
  Drive, Master System, PC Engine, PC-8801, PC-9801, DSi). Foldery bez tagu w kolekcji
  („… - Speed Hacks”, „… - MSU1 [roms only]”, „… - MSU-MD”) to dodatkowe wersje gry
  z etykietą folderu.
- **Tłumaczenia o tych samych nazwach plików co oryginały znikały** — liczyły się jako ta
  sama gra z drugiego folderu. Gry kolekcji tłumaczeń mają teraz ścieżkę z folderem
  systemu (`snes\Gra.sfc`) i są osobnymi wersjami.

## [0.30.1] — 2026-10-11

### Naprawione
- **Klawiatura przestawała działać na komputerze** (pad działał) — fokus zostawał w ukrytym
  polu tekstowym (np. po wpisaniu PIN-u na klawiaturze ekranowej) i każdy klawisz trafiał
  do tego pola. Klawisz w nieaktywnym polu zdejmuje z niego fokus i działa normalnie.
- **Błędy interfejsu nie znikają po cichu** — błąd JavaScriptu (także błąd wywołania API
  z Pythona) pokazuje się na ekranie i trafia do `logs/emustart.log` („UI: BŁĄD JS: …”
  z ekranem i otwartym oknem). Diagnostyka niedziałającej klawiatury na komputerze.

## [0.30.0] — 2026-10-11

### Dodane
- **Tłumaczenia jako wersje gier** — fanowskie tłumaczenia z folderu kolekcji (`[T-En]
  Collection\<system>\`, folder systemu z tagiem `[T-…]` albo pliki z `[T-…]` w nazwie)
  są schowane pod oryginałem: na liście jedna gra, a w opcjach gry (przytrzymaj A)
  **Wersja** — oryginał albo tłumaczenie (język i autor z tagu). Wybrana wersja pobiera
  się, uruchamia i ma swoje zapisy; wybór należy do profilu gracza i jest wspólny dla
  komputera i telefonu (`<profil>/variants.json`). Pliki na dysku zostają nietknięte.
  Rozpoznawanie języka i tytułu bazowego przeniesione z RomHelpera. Tłumaczenie bez
  własnego emulatora używa emulatora wybranego dla oryginału.

## [0.29.0] — 2026-10-11

### Dodane
- **Android: instalacja emulatorów** — gra bez emulatora (albo Ustawienia → Emulatory →
  **Pobierz brakujące emulatory**) proponuje pobranie: APK z najnowszego oficjalnego wydania
  (RetroArch — buildbot.libretro.com, ArmSX2, melonDS, Azahar, Flycast — GitHub, PPSSPP —
  ppsspp.org) i systemowy instalator (potwierdzasz instalację; za pierwszym razem zgoda
  „Instalowanie nieznanych aplikacji” dla EmuStart). DuckStation, Dolphin, DraStic, M64Plus FZ,
  Redream — strona w Google Play. Gdy adresu APK nie da się ustalić — strona wydania w przeglądarce.
- **Android: brakujący rdzeń RetroArcha** — gdy RetroArch wraca od razu, a rdzeń jeszcze nigdy
  nie uruchomił gry na tym telefonie, EmuStart mówi, który rdzeń pobrać (Online Updater → Core
  Downloader) i otwiera RetroArch (Y). Rdzeni nie da się wgrać z zewnątrz — leżą w prywatnym
  folderze RetroArcha.

## [0.28.0] — 2026-10-11

### Dodane
- **Android: BIOS-y rdzeni RetroArcha z serwera** — przed grą telefon pobiera brakujące
  BIOS-y rdzenia (lista z pliku `.info` RetroArcha na serwerze, pliki z folderu BIOS-ów
  serwera i folderów `system` jego RetroArchów) do `RetroArch/system`: BIOS PS1 dla
  SwanStation, Sega CD, PC Engine CD, FDS, Kickstarty Amigi itd. — jak EmuStart na Windows.
- **Android: Amiga WHDLoad** — Kickstarty także do `…/WHDLoad/Devs/Kickstarts` (wcześniej
  PUAE brał zamiennik AROS: „Kickstart ROM 'kick40068.A1200' not found”, a WHDLoad kończył
  się „Couldn't find VCL instruction”).
- **Android: gry arcade z rodzicem i BIOS-em** (np. `neogeo.zip`) — serwer dołącza je do
  plików gry, telefon pobiera je obok gry.
- **Android: konto RetroAchievements z profilu** — RetroArch i ArmSX2 na telefonie logują się
  kontem gracza (to samo co na komputerze; profil bez konta — wyłączone, profil bez wyboru —
  zostaje logowanie z emulatora).
- **Android: ustawienia RetroArcha należą do profilu i synchronizują się między telefonami**
  (osobno od komputera; serwer: `<profil>/settings/android/retroarch/`). RetroArch dostaje plik
  profilu z `#include` swojego `retroarch.cfg` — zmiany z menu RetroArcha trafiają do profilu,
  oryginał zostaje nietknięty; ścieżki, sterowniki i urządzenia zostają z danego telefonu.
  „Ustawienia emulatora” w opcjach gry też zapisują do profilu.
- Serwer: `GET /v1/bios?core=…` (lista), `GET /v1/bios?name=…` (plik). **Zaktualizuj serwer.**

## [0.27.3] — 2026-10-11

### Poprawione
- **Stany RetroArcha (SNES, MSU-1 i inne) niewidoczne w menedżerze zapisów** — RetroArch
  nazywa stan od pliku gry **w środku ZIP-a** (MSU-1: `Chrono_Trigger_(USA)_(MSU1).state`),
  a EmuStart szukał go po nazwie ZIP-a (`Chrono Trigger (USA) (MSU1)`). Teraz stany i zapisy
  dobierane są także po nazwach plików w archiwum, bez różnicy „_” / spacja (serwer, komputer,
  telefon), a komputer zapamiętuje nazwy stanów po grze („Wczytaj zapis”).
- Telefon wysyła stany RetroArcha **z podfolderem rdzenia** (`RetroArch/states/bsnes/…` →
  `states/android/retroarch/bsnes/…`): menedżer pokazuje rdzeń, a kopia na komputer trafia
  do folderu tego rdzenia, w którym RetroArch jej szuka.
  **Zaktualizuj serwer, telefon i komputer.**

## [0.27.2] — 2026-10-11

### Poprawione
- **Autozapis ARMSX2 był niewidoczny** — ARMSX2 zapisuje stan przy wyjściu jako
  `<płyta> (CRC).autosave.p2s` (starsze: `.autosave.2–5.p2s`) i to on wczytuje się przy
  starcie gry; EmuStart znał tylko sloty i `.resume.p2s` PCSX2, więc ani telefon go nie
  wysyłał, ani menedżer zapisów go nie pokazywał. Teraz jest rozpoznawany (telefon i serwer),
  w menedżerze jako „autozapis przy wyjściu”.
- Kopia autozapisu ARMSX2 na komputer trafia jako stan wznowienia PCSX2 (`.resume.p2s`),
  a wznowienie z komputera na telefon — jako autozapis ARMSX2 (wczyta się przy starcie gry).
  **Zaktualizuj serwer i aplikację na telefonie.**

## [0.27.1] — 2026-10-11

### Poprawione
- **Menedżer zapisów pokazywał karty pamięci innych platform** — np. gra SNES widziała
  karty PS2 (`Mcd001.ps2`) i wspólną kartę PS1. Karty i stany PS1/PS2 są teraz tylko przy
  grach swojej platformy. Listę liczy serwer — **zaktualizuj serwer**.
- **Stany ARMSX2 / AetherSX2 z telefonu nie trafiały na serwer** (menedżer zapisów ich nie
  pokazywał): telefon rozpoznawał stany PS2 tylko po przedrostku poznanym z pliku zmienionego
  w trakcie gry, a stan wznowienia (`.resume.p2s`) ARMSX2 zapisuje przy wyjściu — często już
  po synchronizacji. Teraz telefon rozpoznaje stany także po numerze płyty z metadanych gry
  (jak PCSX2 na komputerze), bez względu na czas zapisu; zaległe wyślą się po następnej grze.
  **Zaktualizuj aplikację na telefonie.**

### Dodane
- Menedżer zapisów na komputerze: **Kopiuj tutaj i graj** — stan z telefonu (np. wznowienie
  z ARMSX2) od razu na ten komputer i start gry od niego w PCSX2.

## [0.27.0] — 2026-10-11

### Dodane
- **PIN profilu gracza** (4–8 cyfr) — rozróżnia graczy i chroni ich zapisy:
  - **Nazwy profili są unikalne** bez względu na wielkość liter. Zakładając profil
    o zajętej nazwie trzeba podać PIN istniejącego: dobry — to Twój profil (np. z innego
    urządzenia) i trafia na to urządzenie; zły — trzeba wybrać inną nazwę.
  - **PIN raz na urządzeniu**: przy pierwszym wyborze profilu na komputerze / telefonie;
    urządzenie pamięta go do **Wyloguj z tego urządzenia** (Start na karcie profilu).
    Zmiana PIN-u (z dotychczasowym) wylogowuje inne urządzenia.
  - **Stare profile** (bez PIN-u) przy pierwszym wyborze proszą o ustawienie PIN-u;
    folder i zapisy zostają, gdzie były.
  - **Serwer pilnuje**: pliki profilu z PIN-em (zapisy, stany, ustawienia, blokada,
    menedżer zapisów) wydaje tylko urządzeniu z tokenem po podaniu PIN-u; skrót PIN-u
    (scrypt) nigdy nie wychodzi z serwera; złe PIN-y — rosnące opóźnienie.
    Nowe API: `GET /v1/profiles`, `PUT /v1/profiles/login|create|pin`.
  - Komputer bez serwera (sam SMB) sprawdza PIN sam — rozróżnia konta, ale kto ma dostęp
    do udziału, ma dostęp do plików.
  - **Zaktualizuj serwer i aplikację na telefonie** — starsza aplikacja nie odczyta profili
    z PIN-em.

## [0.26.0] — 2026-10-11

### Dodane
- **Menedżer zapisów** — opcje gry (przytrzymane A) → **Zapisy i stany**, na komputerze
  i telefonie: stany tej gry z obu platform (emulator, slot, data, rozmiar) i wspólne zapisy
  w grze. Wybrany stan można **skopiować na drugą platformę** — trafia do jej folderu
  w profilu na NAS-ie i pobiera się przy następnej grze (na ten komputer od razu, jest
  w „Wczytaj zapis”; stamtąd też „Graj od tego stanu”). Nadpisywany stan idzie do kopii
  zapasowej profilu.
  - **PCSX2 ↔ ARMSX2 / AetherSX2**: zgodność sprawdzana z pliku — stan `.p2s` zapisuje
    wersję formatu, a ARMSX2 ma ten sam format co PCSX2 2.x (`9A59`, czyta też starsze).
    Ta sama wersja = ✓; inna = ✗ (kopiowanie dalej możliwe, z ostrzeżeniem). Telefon
    rozpoznaje skopiowany stan po numerze płyty (indeks przedrostków na serwerze).
  - **RetroArch**: stan należy do rdzenia — ten sam rdzeń na obu urządzeniach (np.
    SwanStation, Snes9x, mGBA) zwykle się wczyta. Na komputerze stan trafia do podfolderu
    rdzenia, gdy RetroArch sortuje stany wg rdzenia.
  - Serwer: `GET /v1/savemgr/catalog`, `PUT /v1/savemgr/copy` (z kluczem). EmuStart na
    komputerze bez serwera robi to samo przez SMB. **Serwer trzeba zaktualizować.**

## [0.25.13] — 2026-10-11

### Poprawione
- **„Failed to resolve Python.Runtime.Loader.Initialize” po rozpakowaniu pobranego ZIP-a**
  (okno EmuStart i `--server-panel`): Windows oznacza pliki z pobranego archiwum jako
  „z internetu” i .NET nie wczytuje wtedy `Python.Runtime.dll`. EmuStart przy starcie sam
  zdejmuje ten znacznik ze swoich plików — `Unblock-File` nie jest już potrzebne.
- Panel serwera, gdy okno mimo to nie wystartuje, otwiera się w przeglądarce (jak EmuStart).

## [0.25.12] — 2026-10-11

### Dodane
- **Usuwanie gier z dysku** — opcje gry (przytrzymane A, prawy przycisk myszy) →
  **Usuń z dysku**, z potwierdzeniem. Usuwa kopię lokalną, także niedokończone pobieranie
  (`.part` i mapę bloków), i przypięcie; gra zostaje na NAS-ie. Na telefonie: opcje gry
  (przytrzymane A, długie dotknięcie) z **Graj / Przypnij / Usuń z telefonu**.
- **Panel serwera** — `EmuStart.exe --server-panel` (skrót „EmuStart Server” w menu Start
  po `--install-server`): wersja i czas pracy, adres dla telefonu, liczba gier, urządzenia
  i pobierane właśnie gry; przyciski **Przeładuj listę gier**, **Restart serwera**,
  **Pokaż klucz**, **Folder logów**. Gdy serwer nie działa, panel go uruchamia. Działa tylko
  na komputerze z serwerem (127.0.0.1).

- **Ustawienia emulatora z EmuStart** — opcje gry → **Ustawienia emulatora**: emulator
  gry bez gry (RetroArch w menu), z ustawieniami profilu; po zamknięciu zmiany trafiają
  do profilu i na NAS. Na telefonie otwiera emulator (ustawienia tego telefonu).

### Poprawione
- **Ustawienia RetroArcha wracały po każdej grze** (np. obraz w SwanStation): na czas gry
  RetroArch miał wyłączony zapis ustawień przy wyjściu, więc zmiany z menu (proporcje,
  skalowanie, shadery…) ginęły, a profil wgrywał stare. Teraz RetroArch pracuje na kopii
  `retroarch.cfg` i przy wyjściu ją zapisuje; zmienione wartości wracają do `retroarch.cfg`
  (i do profilu) — bez ustawień sesji EmuStart (komendy, foldery profilu, konto
  RetroAchievements, kolejność padów). Opcje rdzeni i nadpisania zapisywały się już wcześniej.
- **Przypięta gra nie kończyła pobierania** (zostawał pełny plik `.part`, status „przypięta
  (pobierze się)”): błąd jednego ze strumieni przerywał całe pobieranie, nawet gdy pozostałe
  pobrały już wszystkie bloki. Teraz chwilowe błędy sieci są ponawiane (5 prób na strumień),
  komplet bloków zawsze kończy się plikiem gry, a plik zajęty przez inny program jest
  przemianowywany w tle, gdy tylko zostanie zwolniony. Przypięta, niepełna gra wznawia
  pobieranie sama po wejściu na nią.
- **Postęp pobierania 40272 %** — po usunięciu gry z pamięci podręcznej zostawała mapa
  pobranych bloków; następne pobieranie liczyło z niej, że brakuje tylko kilku MB. Mapa
  jest usuwana razem z grą, a bez pliku `.part` ignorowana (Windows i Android); procent
  nie przekracza 100.
- **MSU-1 na telefonie: „W archiwum nie ma pliku gry”** — ROM (`.sfc`) w archiwum z muzyką
  wybierany jest także wtedy, gdy serwer nie podał rozszerzeń systemu (serwer starszy niż
  0.25.9) albo folder systemu ma inną wielkość liter (`snesmsu1`): pomijane są tylko pliki
  towarzyszące (`.msu`, `.pcm`, `.bps`, `.bml`, opisy). To samo na Windows.
- Status „lokalnie” liczony z plików w pamięci podręcznej, nie tylko z bazy.

### Zmienione
- **Binarka Windows budowana w GitHub Actions** (`.github/workflows/windows.yml`, to samo
  co `build.ps1`): przy każdym pushu na `main` i PR — ZIP jako artefakt przebiegu; przy tagu
  `v*` ZIP trafia do wydania (wydanie zakładane z opisem z CHANGELOG, APK obok).
- **Ustawienia emulatorów z Windows na NAS-ie w `<profil>\settings\windows\<emulator>`** —
  osobno od Androida (inne wersje emulatorów i rdzeni). Dotychczasowe `settings\<emulator>`
  są kopiowane tam przy pierwszej synchronizacji (stary folder zostaje).
- `--install-server`: zadanie Harmonogramu bez limitu 72 h pracy (schtasks zatrzymywał
  serwer po 3 dniach) i z ponownym uruchomieniem po awarii. **Uruchom `--install-server`
  ponownie** (jako administrator).

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
