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
  „Ustaw jako profil tego komputera”, zmiana folderu na NAS-ie).
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

**Synchronizacja z NAS-em.** Przed grą nowsze pliki z NAS-a → lokalnie, po grze
nowsze lokalne → NAS (`Z:\emustart\Profiles\<profil>\`). Nadpisywane wersje trafiają
do kopii zapasowych. Szczegóły: [Kilka komputerów, jeden NAS](Kilka-komputerów-jeden-NAS).

**Przez serwer EmuStart.** Gdy w Ustawieniach jest [serwer EmuStart](Serwer-dla-Androida)
(i włączone „Zapisy i profile przez serwer”), zapisy, ustawienia, blokada profilu
i stany wznowienia idą przez serwer zamiast przez SMB: pliki są skompresowane
(pusta karta pamięci PS2 8 MB → kilka KB), kopię zapasową nadpisywanej wersji robi
serwer u siebie, kilka plików idzie naraz. To ten sam folder `Profiles` na NAS-ie —
komputery przez SMB i przez serwer mogą działać równocześnie. Serwer niedostępny —
przez SMB jak dotąd. Przy starcie EmuStart wysyła wszystkie lokalne zapisy, których
nie ma na NAS-ie (np. z gry bez połączenia).

**Blokada.** Ten sam profil nie gra naraz na dwóch urządzeniach (komputery, telefon) —
drugie dostaje komunikat „Profil gra teraz na urządzeniu …”. Blokada to dzierżawa:
urządzenie w grze odnawia ją (komputer co minutę, telefon co 2 minuty, dopóki działa
aplikacja). Gdy urządzenie padło albo aplikację zamknięto w trakcie gry, blokada
przestaje obowiązywać po 5 min (komputer) / 15 min (telefon); wcześniej można
**przejąć profil** (Y na ekranie błędu). Telefon przy uruchomieniu aplikacji zdejmuje
własną blokadę z poprzedniej, niedokończonej gry.

## Zmiana nazwy profilu

Zmiana nazwy zmienia nazwę wyświetlaną. Folder na NAS-ie zmienia się razem z nią
tylko wtedy, gdy profil jeszcze nic na NAS nie wysłał. Profil z danymi zachowuje
stary folder (inne komputery go po nim rozpoznają) — w opcjach profilu (Start)
pojawia się wtedy **„Folder na NAS: „stary” → zmień na „nowy””**: przenosi folder
i zostawia w starym miejscu `moved.json`, dzięki któremu inne komputery same
przepinają się na nowy.
