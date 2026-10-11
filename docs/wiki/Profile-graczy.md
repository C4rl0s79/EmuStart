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

- **Zmiana ustawień**: przytrzymaj A na grze → **Ustawienia emulatora** — emulator tej gry
  uruchamia się bez gry (RetroArch w menu), z ustawieniami Twojego profilu; po jego
  zamknięciu zmiany zapisują się w profilu i na NAS-ie. Zmiany zrobione w trakcie gry
  (RetroArch: menu F1 → Ustawienia, Opcje rdzenia, nadpisania) też zostają — RetroArch
  musi zostać zamknięty normalnie (menu EmuStart → Wyjdź, albo z menu RetroArcha).
- **Windows i Android mają osobne ustawienia** (inne wersje emulatorów i rdzeni, inne
  ścieżki): na NAS-ie ustawienia z Windows są w `<profil>\settings\windows\<emulator>\`,
  telefon ma własne — na telefonie **Ustawienia emulatora** otwiera emulator na tym
  urządzeniu. Wspólne są tylko zapisy (save'y).

**PIN profilu.** Każdy profil ma PIN (4–8 cyfr). Nazwy profili są unikalne (bez względu
na wielkość liter): zakładając profil o zajętej nazwie podajesz PIN istniejącego — dobry
PIN dodaje Twój profil na to urządzenie, zły oznacza, że to inny gracz i trzeba wybrać
inną nazwę. PIN podajesz raz na każdym urządzeniu; **Start** na karcie profilu → **Zmień
PIN** / **Wyloguj z tego urządzenia**. Profile sprzed wersji 0.27 przy pierwszym wyborze
proszą o ustawienie PIN-u. Serwer EmuStart wydaje pliki profilu z PIN-em tylko
urządzeniu, które podało PIN (po złych próbach — coraz dłuższe czekanie); przy samym SMB
PIN tylko rozróżnia konta.

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

**Synchronizacja z NAS-em.** Przed grą nowsze pliki z NAS-a → lokalnie, po grze
nowsze lokalne → NAS (`Z:\emustart\Profiles\<profil>\`). Nadpisywane wersje trafiają
do kopii zapasowych. Szczegóły: [Kilka komputerów, jeden NAS](Kilka-komputerów-jeden-NAS).

**Kopie zapasowe** nadpisywanych plików są w `<profil>\_backup\<data>-<urządzenie>\` (ostatnie 10).
Pliki, które się kompresują (np. karty pamięci), są tam spakowane zstd jako `<plik>.zst` —
rozpakujesz je np. 7-Zipem albo `zstd -d`. Stany gry zostają bez zmian (emulatory już je
kompresują).

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
