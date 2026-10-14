# Profile graczy

Każdy profil ma **osobno**:

| Co | Gdzie |
|---|---|
| save'y i stany emulatorów | RetroArch, DuckStation, PCSX2, Dolphin, PPSSPP, RPCS3 |
| ustawienia emulatorów | RetroArch, DuckStation, PCSX2 (w tym ustawienia per gra i profile padów) |
| ustawienia EmuStart | wygląd, tytuły jako logo, ukrywanie gier |
| konto RetroAchievements | RetroArch, DuckStation, PCSX2 — [RetroAchievements](RetroAchievements) |
| historia gry i stan „wznowienia” | czas gry, ostatnio grane, quicksave |

## Obsługa

- **Start → Zmień profil** — lista profili („Kto gra?”).
- Na karcie profilu: **A** — graj jako, **Y** — zmień nazwę, **X** (dwa razy) —
  usuń (pliki save'ów zostają), **Start** — opcje profilu (RetroAchievements,
  „Ustaw jako profil tego komputera”).
- Ustawienia → Profile:
  - **Profil tego komputera** — do niego trafiają save'y i konto RA zastane
    w emulatorach (np. po instalacji emulatora od nowa),
  - **Pytaj, kto gra, przy starcie** — wyłączone: od razu startuje profil komputera,
  - **Ustawienia emulatorów osobno dla profilu** — można wyłączyć (wspólne).

## Jak to działa

**Save'y.** Folder save'ów emulatora (np. `DuckStation\memcards`) jest zamieniany
na dowiązanie (junction) do folderu bieżącego profilu
(`profiles\<id>\duckstation\memcards`). Emulator zapisuje „u siebie”, a plik trafia
do profilu. Foldery czytane są z ustawień emulatora. Pierwsze podpięcie przenosi
dotychczasowe save'y do profilu tego komputera.

**Ustawienia emulatorów.** Przed grą ustawienia profilu są wgrywane do emulatora,
po grze zbierane z powrotem. Wartości zależne od komputera (ścieżki, BIOS, karta
graficzna, urządzenie audio) oraz hasła i tokeny zostają z danego komputera.
Nowy profil zaczyna od bieżących ustawień.

- **Zmiana ustawień**: przytrzymaj A na grze → **Ustawienia emulatora** — emulator tej gry
  uruchamia się bez gry (RetroArch w menu), z ustawieniami Twojego profilu; po jego
  zamknięciu zmiany zapisują się w profilu i na NAS-ie. Zmiany zrobione w trakcie gry
  (RetroArch: menu F1 → Ustawienia, Opcje rdzenia, nadpisania) też zostają — RetroArch
  musi zostać zamknięty normalnie (menu EmuStart → Wyjdź, albo z menu RetroArcha).
- **Telefon:** ustawienia RetroArcha (i konto RetroAchievements w RetroArchu) należą do
  profilu i są wspólne dla wszystkich telefonów tego profilu
  (`<profil>\settings\android\retroarch\`). Opcje rdzeni RetroArcha zostają na telefonie.
  ArmSX2 trzyma ustawienia i konto RetroAchievements u siebie (zaloguj raz w ArmSX2);
  od wersji 2.8.2 stosuje ustawienia osobne dla gry także przy starcie z EmuStart.
- **Windows i Android mają osobne ustawienia** (inne wersje emulatorów i rdzeni, inne
  ścieżki): na NAS-ie ustawienia z Windows są w `<profil>\settings\windows\<emulator>\`,
  telefon ma własne — na telefonie **Ustawienia emulatora** otwiera emulator na tym
  urządzeniu. Wspólne są tylko zapisy (save'y).

**Hasło konta (0.40).** Z serwerem EmuStart każdy profil to konto z **hasłem**: co najmniej
10 znaków, w tym wielka litera, cyfra i znak specjalny. Serwer trzyma tylko skrót hasła
(scrypt z losową solą, w danych serwera — nie w folderze profili) i nigdy go nie wydaje.
Hasło jest potrzebne:

- przy dołączaniu nowego urządzenia — **Nowe konto** ustawia hasło, **Mam konto** je
  sprawdza (prośba i tak czeka na zatwierdzenie na innym urządzeniu konta albo przez
  administratora),
- gdy na urządzeniu gra **inny użytkownik** — karta **Inny użytkownik** → nazwa konta i jego
  hasło; urządzenie pamięta logowanie do **Wyloguj konto z tego urządzenia**. Gdy takiego
  konta nie ma: **Utwórz konto** → nowe hasło → prośba czeka na administratora (jego
  urządzenie albo panel serwera); po zatwierdzeniu konto samo pojawia się na liście.

Urządzenie przypisane do konta nie pyta o hasło swojego konta — loguje się kluczem
urządzenia. Konta sprzed 0.40 przy pierwszym wyborze proszą o ustawienie hasła; zapomniane
hasło ustawia administrator w panelu serwera (**Konta** → **Nowe hasło**). Zmiana hasła
wylogowuje to konto na urządzeniach innych użytkowników. Złe hasła: coraz dłuższe czekanie
(na konto) i limit prób z jednego adresu. Hasło idzie szyfrowanym połączeniem (port HTTPS
serwera z przypiętym certyfikatem, tunel) — zwykły port przyjmuje tylko sieć lokalną i
Tailscale.

**PIN — tylko to urządzenie.** PIN (4–8 cyfr) to lokalna blokada profilu, np. na wspólnym
komputerze: **Start** na karcie profilu → **Ustaw PIN na tym urządzeniu** / **Zmień** /
**Usuń**. Urządzenie pyta o niego przy wyborze profilu (raz na uruchomienie). Skrót PIN-u
zostaje na urządzeniu — serwer go nie zna. Dawne PIN-y profili z serwera (0.27–0.39) nie są
już sprawdzane.

**Stany RetroArcha — jak w Steam** (tylko dochodzą, nigdy się nie nadpisują):

- każdy zapis stanu dostaje nowy numer (**Select + Y** na padzie albo menu RetroArcha),
  także autozapis przy wyjściu z gry trafia do historii jako kolejny stan,
- gdy dwa urządzenia zrobiły stan o tym samym numerze (np. gra bez serwera), drugi dostaje
  następny wolny numer — nic nie ginie,
- przed grą najnowszy stan z dowolnego urządzenia konta wczytuje się sam („Kontynuuj”);
  start bez stanu: opcje gry → **Graj od początku**, w trakcie gry: A + Y → **Reset gry**,
- opcje gry → **Wczytaj zapis**: wszystkie stany gry ze wszystkich urządzeń — miniatura,
  „Stan #N”, urządzenie, data, autozapis; na górze „Kontynuuj — najnowszy stan”,
- serwer trzyma 50 najnowszych stanów gry (dla każdego rdzenia).

**Ustawienia emulatora** (opcje gry → **Ustawienia emulatora**) należą do profilu: przed grą
kopia z profilu zastępuje plik emulatora, po grze zmiany wracają do profilu. Zmienia się je
w emulatorze — w trakcie gry (RetroArch: **Start + Select** → menu, np. Opcje rdzenia →
rozdzielczość) albo **Zmień w emulatorze (bez gry)**. Ręczna edycja pliku poza grą zostanie
zastąpiona kopią z profilu. Gdy zmiana coś zepsuje (np. czarny ekran po zbyt wysokiej
rozdzielczości): **Cofnij zmianę** — lista 10 ostatnich zmian (data, gra, pliki); wybrana
przywraca ustawienia sprzed niej od następnej gry, a samo przywrócenie też da się cofnąć.
**Shader ekranu** (ta sama strona): gry systemu bez shadera RetroArcha — gdy szukasz shadera
pasującego do ustawień rdzenia, a obecny daje czarny ekran (też z menu w grze A + Y).

**Menedżer zapisów** (opcje gry → **Zapisy i stany**, na komputerze i telefonie):
stany tej gry z komputera i z telefonu oraz wspólne zapisy w grze. Stan z jednej platformy
można skopiować na drugą (pobierze się przy następnej grze). Zgodność:

| Emulatory | Zgodność stanów |
|---|---|
| PCSX2 (PC) ↔ ARMSX2 / AetherSX2 (telefon) | ten sam format pliku `.p2s` → ✓ (sprawdzane z wersji zapisanej w stanie) |
| RetroArch ↔ RetroArch | ten sam rdzeń (np. SwanStation) → zwykle działa |
| DuckStation (PC) ↔ SwanStation (telefon) | ✗ — różne emulatory; wspólna jest karta pamięci |

Zapisy w grze (save'y RetroArcha, karty pamięci PS1/PS2) są wspólne dla obu platform
i synchronizują się same przy każdej grze — menedżer je tylko pokazuje.

**Synchronizacja z serwerem.** Przed grą nowsze pliki z serwera → lokalnie, po grze
nowsze lokalne → serwer (folder profilu konta na [EmuStart Server](EmuStart-Server)).
Pliki są skompresowane, kopię nadpisywanej wersji robi serwer u siebie, kilka plików idzie
naraz. Serwer niedostępny — zapisy czekają w kolejce; przy starcie EmuStart wysyła
wszystkie lokalne zapisy, których serwer nie ma. Szczegóły:
[Kilka urządzeń, jeden serwer](Kilka-komputerów-jeden-NAS). Całe konto (ZIP) kopiuje
serwer raz dziennie — panel → Konta → Pliki i kopie.

**Kopie zapasowe** nadpisywanych plików są w `<profil>\_backup\<data>-<urządzenie>\` (ostatnie 10).
Pliki, które się kompresują (np. karty pamięci), są tam spakowane zstd jako `<plik>.zst` —
rozpakujesz je np. 7-Zipem albo `zstd -d`. Stany gry zostają bez zmian (emulatory już je
kompresują).

**Blokada.** Ten sam profil nie gra naraz na dwóch urządzeniach (komputery, telefon) —
drugie dostaje komunikat „Profil gra teraz na urządzeniu …”. Blokada to dzierżawa:
urządzenie w grze odnawia ją (komputer co minutę, telefon co 2 minuty, dopóki działa
aplikacja). Gdy urządzenie padło albo aplikację zamknięto w trakcie gry, blokada
przestaje obowiązywać po 5 min (komputer) / 15 min (telefon); wcześniej można
**przejąć profil** (Y na ekranie błędu). Telefon przy uruchomieniu aplikacji zdejmuje
własną blokadę z poprzedniej, niedokończonej gry.

## Zmiana nazwy profilu

Zmiana nazwy zmienia nazwę wyświetlaną na tym urządzeniu; konto na serwerze zachowuje
swoją nazwę (po niej rozpoznają je inne urządzenia).
