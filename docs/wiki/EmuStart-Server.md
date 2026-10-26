# EmuStart Server

**EmuStart Server** to osobny program na komputerze z grami. Trzyma bibliotekę gier,
grafiki, opisy, konta graczy i ich zapisy. Urządzenia — **EmuStart na PC** i **aplikacja na
Androida** — biorą wszystko od niego i trzymają lokalną kopię (szybki interfejs, gry
pobrane na urządzenie działają bez serwera). Serwer jest źródłem prawdy: zmiana grafiki,
opisu albo logo systemu dociera do wszystkich urządzeń.

Serwer działa w tle (zadanie Harmonogramu na koncie SYSTEM), a obsługuje się go z **panelu
serwera** — okna na tym samym komputerze.

## Instalacja

1. Z [Releases](https://github.com/C4rl0s79/EmuStart/releases) pobierz
   `EmuStart-Server-<wersja>-win64.zip` i rozpakuj, np. do `E:\EmuStart`. Jeśli zip był
   pobrany z internetu, odblokuj pliki (inaczej okno panelu się nie uruchomi):
   ```
   Get-ChildItem -Recurse 'E:\EmuStart' | Unblock-File
   ```
2. W wierszu poleceń **uruchomionym jako administrator**, w tym folderze:
   ```
   EmuStartServer.exe --install
   ```
   Dodaje zadanie Harmonogramu „EmuStart Server” (start razem z Windows, bez limitu
   czasu, ponowny start po awarii), reguły zapory (porty 8740 i 8741) i skrót
   **EmuStart Server** w menu Start, a folder programu (razem z danymi serwera) robi
   tylko do odczytu dla zwykłych użytkowników — zapis tylko serwer (SYSTEM) i administratorzy.
   Uruchom `--install` ponownie po przeniesieniu folderu.
3. Uruchom **EmuStart Server** z menu Start (albo `EmuStartServer.exe`) — otwiera się
   panel; gdy serwer nie działa, panel go uruchamia.
4. Panel → **Konfiguracja → Foldery**: foldery z grami, BIOS-y i pozostałe (niżej).
5. Panel → **Grafiki i opisy**: baza LaunchBox, potem **Brakujące grafiki i opisy**.
6. Panel → **Urządzenia i konta → Dodaj urządzenie** (wybierz konto albo wpisz nowe): kod QR dla
   telefonu, link dla PC ([logowanie urządzeń](#logowanie-urządzeń)). Dla innych osób:
   [strona serwera i zaproszenia](#strona-serwera-i-zaproszenia).

Dane serwera (ustawienia, biblioteka, grafiki, konta, profile, kopie) są w folderze
`server\` obok `EmuStartServer.exe` — skopiowanie folderu programu przenosi serwer. Grafiki
gier są w jednym pliku na system (`server\media\<system>.media`) zamiast tysięcy małych
plików — szybkie kopiowanie i kopie zapasowe. Stare pliki grafik serwer przenosi sam przy
starcie (każdy usuwany dopiero po odczycie kontrolnym).

### Przeniesienie serwera na inny komputer

Klucz certyfikatu serwera jest zaszyfrowany i działa tylko na komputerze, na którym powstał
(sama kopia folderu nic nie da — ochrona przed kopiami z udziału czy kopii zapasowych).
1. Panel → Konfiguracja → **Przeniesienie serwera** → hasło przeniesienia (dwa razy) →
   **Przygotuj przeniesienie**. W folderze serwera powstaje `przeniesienie.emustart`
   (klucz certyfikatu i klucze źródeł grafik, zaszyfrowane tym hasłem).
2. Zatrzymaj serwer i skopiuj cały folder programu na nowy komputer; tam `--install`.
3. Panel na nowym komputerze pokazuje „Serwer przeniesiony” → podaj hasło przeniesienia →
   **Odblokuj na tym komputerze**. Serwer uruchamia się ponownie z tym samym certyfikatem —
   telefony i komputery działają dalej bez ponownego logowania.

### Przejście z EmuStart sprzed 0.45

Do wersji 0.44 serwer był trybem zwykłego `EmuStart.exe` (`--server`). Aktualizacja z panelu
(0.44.8 lub nowsza) przechodzi na nowy program sama: pobiera `EmuStart-Server-…`, przestawia
zadanie Harmonogramu i skrót na `EmuStartServer.exe`, usuwa z folderu serwera dawny
`EmuStart.exe`. Dane zostają. Do grania na tym komputerze zainstaluj **EmuStart** (PC)
w osobnym folderze i zaloguj go do serwera jak każde inne urządzenie.

## Parametry

| Polecenie | Działanie |
|---|---|
| `EmuStartServer.exe` | panel serwera (uruchamia serwer, gdy nie działa) |
| `EmuStartServer.exe --panel` | to samo (skrót w menu Start, skrypty aktualizacji) |
| `EmuStartServer.exe --service` | serwer bez okna (zadanie Harmonogramu) |
| `EmuStartServer.exe --install` | autostart, zapora, skrót — jako administrator |
| `EmuStartServer.exe --login-link` | link logowania urządzenia do schowka (komputer bez aparatu) |

## Panel serwera

### Stan
Wersja i czas pracy, adresy, liczba gier i systemów, folder profili, pobierane gry
i urządzenia, które się łączyły. **Przeładuj listę gier** (skan folderów z paskiem
postępu), **Restart serwera**, **Folder logów**.

### Konfiguracja
- **Foldery** — każdy folder serwer sprawdza tak, jak sam go widzi (konto SYSTEM)
  i pokazuje, co tam jest:
  - **Foldery z grami** — kilka, kolejność = pierwszeństwo przy tych samych grach
    (patrz [Foldery z grami i systemy](Foldery-z-grami-i-systemy)); po zmianie lista gier
    przeładowuje się sama,
  - **Folder BIOS-ów** — urządzenia dostają z niego BIOS-y przed grą,
  - **Folder emulatorów** — RetroArch na serwerze (listy BIOS-ów rdzeni),
  - **Folder profili graczy** (pusty — `server\profiles`),
  - **Folder kopii kont** (pusty — `server\backup`),
  - **MAME** (opcjonalnie) — pełne dane gier arcade; bez niego nazwy i wersje z pliku DAT
    FinalBurn Neo.

  Konto SYSTEM nie widzi dysków sieciowych podłączonych literą (np. `Z:`). Folder wybrany
  przyciskiem **Wybierz…** na takim dysku panel zamienia na ścieżkę sieciową
  (`\\NAS\udział\…`); wpisany ręcznie — dostaje ostrzeżenie.
- **Systemy — logo i nazwy** — logo (propozycje albo bez logo), nazwa, poświata,
  widoczność na urządzeniach, **zalecany emulator** i **rdzeń RetroArcha** (urządzenie bez
  własnego wyboru używa tych — stany zgodne między urządzeniami).
- **Folder profili** — przeniesienie profili do katalogu serwera.
- **Dostęp z internetu** — port publiczny (UPnP) i tunel Cloudflare (niżej).

### Grafiki i opisy
- **Pobieranie**: brakujące grafiki i opisy (wszystkie systemy albo jeden), tylko grafiki,
  tylko opisy, opisy z Wikipedią, zmniejszenie zapisanych grafik; postęp i **Zatrzymaj**.
- **Gra — grafiki i opis**: wyszukanie gry, okładka, zrzut i logo gry (propozycje
  z libretro, LaunchBox, SteamGridDB, IGDB, TheGamesDB, szukanie pod inną nazwą, usunięcie),
  opis i dane gry z ręczną poprawką, przywróceniem danych pobranych i pobraniem z sieci.
- **Klucze z pliku**: `art_keys.json` w folderze `server` serwera (`{"sgdb_key": "…",
  "igdb_client_id": "…", "igdb_client_secret": "…", "tgdb_key": "…"}`) — przy starcie serwer
  zapisuje je zaszyfrowane i usuwa plik.
- **Źródła**: baza LaunchBox, klucze SteamGridDB / IGDB / TheGamesDB (zaszyfrowane
  na serwerze).

Szczegóły: [Grafiki i metadane](Grafiki-i-metadane).

### Urządzenia i konta
- **Urządzenia**: lista z kontem, wersją i ostatnim kontaktem; **Dodaj urządzenie** (kod
  logowania wybranego konta — istniejącego albo nowego), prośby „Mam konto” do zatwierdzenia,
  **Unieważnij**, nazwa, *Z grami / Bez gier*.
- **Zaproszenia**: adres Google + konto (nowe albo istniejące) → link do strony serwera;
  stan (czeka / wykorzystane / wygasło), **Usuń**.
- **Konta**: hasło (opcjonalne), konto Google (**Odłącz**), **Administrator**, liczba urządzeń,
  **Pliki i kopie**:
  - co jest w profilu konta (zapisy, stany, ustawienia — pliki, rozmiar, ostatnia zmiana)
    i ostatnio zmienione pliki,
  - **Zrób kopię teraz** — jeden ZIP (profil + wpis konta z hasłem) w folderze kopii,
  - **kopie automatyczne** raz dziennie, gdy konto się zmieniło; zostaje 14 najnowszych
    (do zmiany),
  - **Przywróć** — najpierw kopia obecnego stanu; pliki, których w kopii nie ma, zostają.

### Emulatory
Magazyn emulatorów (domyślnie `server\emulators`, zmiana: Konfiguracja → Foldery → Magazyn
emulatorów): emulatory potrzebne systemom biblioteki — wg zalecanego emulatora i rdzenia systemu
(Konfiguracja → Systemy) — i **wszystkie rdzenie RetroArcha** z buildbota (około 245). Komputer
dostaje dla każdego systemu biblioteki zalecany emulator i wszystkie pasujące rdzenie (wg plików
`.info` RetroArcha, np. snes9x, bsnes, bsnes-jg dla SNES) — do wyboru w Ustawieniach. Serwer
trzyma każdą wersję jako archiwum; nowe systemy i rdzenie dociąga co godzinę. Serwer sprawdza nowe wydania raz dziennie;
**Sprawdź i pobierz teraz** — od razu, **Pobierz ponownie** — jeden emulator jeszcze raz.
Tabela: systemy, wersja na serwerze (i poprzednia — zostaje na wszelki wypadek), rozmiar,
ostatni błąd pobierania.

**PCSX2 a wspólne stany PS2 (0.49).** Stany PS2 są wspólne dla PCSX2 (PC) i ArmSX2 (telefon), a
nowa wersja PCSX2 zwykle zmienia format stanów. Serwer czyta format stanów z paczek zapisów —
osobno ostatni z PC i z telefonu. Gdy konta mają stany z obu, **nowa wersja PCSX2 nie rozchodzi
się sama**: w tabeli jest „nowa wersja … wstrzymana” z powodem —
- *formaty się rozjechały* (PC nowszy niż telefon) — stany z PC nie wczytują się na telefonie;
  przycisk **Zaktualizuj mimo to**;
- *formaty zgodne* albo *telefon nowszy* — aktualizacja możliwa, ale stany zapisane w obecnej
  wersji mogą przestać się wczytywać; przycisk **Zaktualizuj**.

Przed aktualizacją panel ostrzega; karty pamięci działają we wszystkich wersjach. Komputery doinstalowują i aktualizują emulatory same
(zob. [[Emulatory]]). Wersja to pliki z sumami SHA-256 i jedno archiwum ZIP: komputer pobiera
archiwum jednym strumieniem (przerwane pobieranie wznawia się) i rozpakowuje u siebie tylko
brakujące albo zmienione pliki.

**Wymagane ustawienia emulatorów** (ta sama zakładka): wartości, które komputery nakładają tuż
przed każdą grą — wygrywają z ustawieniami profilu. RetroArch: `klucz = wartość` (jak
w `retroarch.cfg`, na czas gry), DuckStation / PCSX2 / Dolphin: `Sekcja|Klucz = wartość` (w pliku
ini emulatora). Domyślnie to, czego EmuStart potrzebuje: wyjście z gry bez pytań
(`Main|ConfirmPowerOff = false`, `UI|ConfirmShutdown = false`, `Interface|ConfirmStop = False`),
w RetroArchu pauza w tle, obrazki stanów, `quit_on_close_content = 2`, **każdy zapis stanu z nowym
numerem** (`savestate_auto_index`) i pad: **Start + Select** — menu RetroArcha, **Select** —
przycisk skrótów, **Select + RB** — przyspieszenie (wł./wył.), **Select + Y** — zapis stanu
(sterownik XInput; na telefonie te same skróty). **Przywróć domyślne** — wartości EmuStart.

### Aktualizacja
**Sprawdź aktualizacje** → **Aktualizuj do …**: pobranie pliku serwera z wydania,
sprawdzenie skrótu SHA-256 i podpisu wydania, podmiana plików po zamknięciu serwera,
ponowny start; panel wraca sam. Przebieg: `logs\update.log`.

## Strona serwera i zaproszenia

Pod adresem tunelu (np. `https://emustart.emucloud.org`) działa **strona serwera**: animowane
logo, grafika retro i **Zaloguj przez Google**. Wchodzą tylko zaproszeni.

1. Panel → **Konfiguracja → Strona serwera — logowanie Google**: w Google Cloud Console
   utwórz klienta OAuth typu „Web application”, w „Authorized redirect URIs” wklej adres
   z panelu (`<adres tunelu>/auth/google/callback`), a identyfikator i sekret klienta wpisz
   w panelu. Ekran zgody: „External”, zakresy `openid`, `email`, `profile`.
2. Panel → **Urządzenia i konta → Zaproszenia**: adres Google + konto (nowe albo istniejące)
   → **Zaproś** → skopiuj link i wyślij go sam (SMS, komunikator, e-mail).
3. Zaproszony otwiera link, loguje się kontem Google **o tym adresie** (link przekazany komuś
   innemu nic nie da) — serwer zakłada konto albo łączy istniejące i od tej chwili rozpoznaje
   je po koncie Google. Zaproszenie ważne 30 dni, jednorazowe.

Po zalogowaniu strona daje:
- **programy do pobrania** z opisem i zrzutem ekranu: EmuStart (Windows), EmuStart na
  Androida, Lumen. Serwer pobiera najnowsze wydania z GitHuba co 6 godzin, sprawdza podpis
  wydania i trzyma kopie u siebie (panel → **Programy do pobrania**);
- **Pokaż kod logowania** — kod QR, link i 8-znakowy kod (jednorazowy, 10 minut) do
  zalogowania urządzenia tego konta;
- **hasło konta** (opcjonalne) — do „Mam konto” i do dodania konta na wspólnym urządzeniu.

## Logowanie urządzeń

Urządzenie łączy się z serwerem **kodem logowania** (albo kontem i hasłem) oraz **kluczem
urządzenia** (para kluczy tworzona na urządzeniu: Windows — TPM, bez niego DPAPI; Android —
Android Keystore; klucz prywatny nie opuszcza urządzenia). W Ustawieniach EmuStart (i w Lumen):

- **Zaloguj (kod QR)** (telefon) — strona serwera → **Pokaż kod logowania** (albo panel →
  **Dodaj urządzenie**) → zeskanuj kod. Kod niesie adresy serwera, odcisk certyfikatu,
  jednorazowy kod i konto — bez hasła i bez czekania na zatwierdzenie. PC: **Kopiuj link**
  i wklej go w **Zaloguj**.
- **Zaloguj (adres serwera)** — adres tunelu (prawdziwy certyfikat), potem:
  - **Mam kod logowania ze strony serwera** — 8 znaków ze strony; od razu;
  - **Mam konto i hasło** — tylko konto z hasłem; prośba czeka na zatwierdzenie na innym
    urządzeniu konta albo w panelu (kod weryfikacyjny musi się zgadzać).

**Nowe konto** nie powstaje w aplikacji (0.49): tylko z zaproszenia (strona serwera) albo
w panelu (**Dodaj urządzenie** → *nowe konto*).

**Administrator** to cecha konta (panel → Konta). Urządzenia administratora widzą wszystkie
konta, zatwierdzają „Mam konto” kont bez urządzeń i **zmieniają grafiki, opisy i logo systemów
z PC i telefonu** (opcje gry i opcje systemu) — zmiana idzie na serwer i trafia do wszystkich
urządzeń. Pierwsze konto zalogowane kodem logowania, gdy żadne nie jest administratorem,
zostaje nim samo.

## Biblioteka na urządzeniach

- **Lista systemów i gier** — z serwera, z lokalną kopią na urządzeniu; PC sprawdza zmiany
  przy starcie i co 5 minut, telefon przy otwarciu systemu (duże systemy porcjami w tle).
- **Grafiki i opisy** — lokalna kopia; zmiana na serwerze (panel albo administrator na
  urządzeniu) podbija wersję systemu, a urządzenia pobierają zmienione grafiki od nowa.
- **Opis gry** — gdy urządzenie ogląda grę bez opisu, serwer pobiera go w tle.
- **Gry** — z serwera (wiele strumieni, wznawianie), z zależnościami arcade (gra nadrzędna,
  BIOS) — patrz [Uruchamianie gier](Uruchamianie-gier-NAS-i-pamięć-podręczna).
- **Zapisy, stany, ustawienia emulatorów, konto RetroAchievements** — w profilu konta na
  serwerze ([Profile graczy](Profile-graczy)).

## Adresy serwera i szyfrowanie

Urządzenia łączą się **tylko szyfrowanie**: port `8741` (sieć lokalna, Tailscale), port
publiczny `8742` i tunel. Port `8740` obsługuje tylko panel na tym komputerze. Certyfikat
serwer tworzy sam (`server\tls-cert.pem`, 20 lat); urządzenia porównują jego odcisk
z zapamiętanym przy logowaniu. Serwer podaje urządzeniom swoje adresy; urządzenie wybiera
najlepszy działający (sieć lokalna → Tailscale → port publiczny → tunel), odświeżając wybór
co 5 minut i po błędzie.

## Dostęp z internetu

Panel → Konfiguracja → **Dostęp z internetu** → **Włącz**: osobny port szyfrowany `8742`
przekierowany na routerze (UPnP). Tylko API urządzeń i grafiki, zawsze z tokenem urządzenia;
seria złych kluczy z jednego adresu = blokada na 30 minut. Gdy router jest za NAT-em
operatora (CGNAT), panel to pokazuje — zostaje Tailscale albo tunel.

### Tunel Cloudflare
1. Cloudflare Zero Trust → tunel (`cloudflared`) na komputerze z serwerem → aplikacja
   np. `emustart.twojadomena.org`, usługa **`https://localhost:8742`**, *TLS → Disable TLS
   certificate verification* włączone. Nie kieruj tunelu na 8740.
2. Panel → Dostęp z internetu → **Adres tunelu** → *Zapisz*.
3. Urządzenia używają tunelu na końcu; nowe urządzenie może zalogować się samym adresem
   tunelu. Nie włączaj *Bot Fight Mode* ani wyzwań dla tej subdomeny.

## API (dla aplikacji)

Każde zapytanie z tokenem sesji (`Authorization: Bearer <token>`), połączeniem szyfrowanym.

| Adres | Zawartość |
|---|---|
| `POST /v1/enroll/request`, `/v1/enroll/poll` | prośba o zalogowanie i jej stan |
| `POST /v1/auth/challenge`, `/v1/auth/token`, `/v1/auth/logout` | logowanie podpisem, wylogowanie |
| `GET /v1/info` | wersja, funkcje, czy urządzenie jest administratorem (`admin`) |
| `GET /v1/library`, `/v1/library?es=` | biblioteka dla PC: systemy z wersją, gry z czasami grafik i opisami |
| `GET /v1/systems`, `/v1/systems/<es>/games` | systemy i gry (telefon; strony `offset`/`limit`, gzip) |
| `GET /v1/find?es=&rel=` | gra po ścieżce: pliki i zależności arcade (PC) |
| `GET /v1/games/<id>`, `/v1/games/<id>/file/<ścieżka>` | szczegóły gry, plik (`Range`) |
| `GET /v1/bios?core=`, `/v1/bios?name=` | BIOS-y rdzenia |
| `GET /v1/emulators` | emulatory w magazynie serwera (wersje) i które potrzebuje dany system |
| `GET /v1/emulators/manifest?item=&version=` | pliki wersji z rozmiarem i SHA-256 |
| `GET /v1/emulators/archive?item=&version=` | cała wersja w jednym ZIP-ie (`Range` — wznawianie) |
| `GET /media/…` | grafiki |
| `GET/PUT/DELETE /v1/nas/…` | folder profilu konta (zapisy, ustawienia) |
| `GET/PUT/DELETE /v1/admin/…` | administrator: grafiki i opis gry, logo i nazwa systemu |

## Bezpieczeństwo

- Klucz prywatny urządzenia nie opuszcza urządzenia; hasła kont tylko jako skrót (scrypt)
  w `accounts.json`, rosnące opóźnienie po złych próbach.
- Serwer wydaje tylko pliki gier z biblioteki i ich zależności; zapis tylko w folderze
  profilu konta; zmiany grafik i opisów tylko urządzenia administratora.
- Panel działa tylko na tym komputerze; nie wystawiaj portu 8740 do internetu.
