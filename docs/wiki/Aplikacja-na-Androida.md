# Aplikacja na Androida

EmuStart na Androida ma ten sam interfejs co na Windows (karuzela systemów, lista gier,
podgląd, edytor wyglądu) i obsługę padem (np. GameSir). Gry, opisy i grafiki bierze
z [EmuStart Server](EmuStart-Server) na komputerze z grami — telefon nic nie
skanuje. Gra pobiera się do telefonu (kilka strumieni naraz, wznawianie) i uruchamia
w emulatorze zainstalowanym w telefonie.

## Instalacja

1. Na komputerze z grami uruchom [EmuStart Server](EmuStart-Server).
2. Pobierz `EmuStart-<wersja>-android.apk` z
   [Releases](https://github.com/C4rl0s79/EmuStart/releases) i zainstaluj (zgoda na
   instalację z nieznanych źródeł).
3. Przy pierwszym uruchomieniu zezwól na **dostęp do wszystkich plików** — gry trafiają
   do `/storage/emulated/0/EmuStart/games`, skąd czytają je emulatory.
4. Ustawienia → **Zaloguj (kod QR)** (panel serwera → Dodaj urządzenie) albo **Zaloguj (adres
   serwera)** (np. `emustart.emucloud.org`) → konto i hasło — patrz
   [Logowanie urządzeń](EmuStart-Server#logowanie-urządzeń).
5. Wybierz emulator dla systemów (←/→ w Ustawieniach → Emulatory).

Telefon łączy się z serwerem tylko szyfrowanie — w domu po sieci lokalnej, poza domem przez
Tailscale, port publiczny albo tunel (wybiera sam).

## Emulatory

EmuStart wykrywa zainstalowane emulatory i uruchamia w nich grę:

| System | Emulatory |
|---|---|
| PS2 | ArmSX2, AetherSX2 / NetherSX2 |
| PS1 | DuckStation, RetroArch (SwanStation) |
| PSP | PPSSPP, RetroArch |
| GameCube / Wii | Dolphin |
| 3DS | Azahar |
| NDS | melonDS, DraStic, RetroArch |
| Dreamcast / Naomi | Flycast, Redream, RetroArch |
| N64 | M64Plus FZ, RetroArch (Mupen64Plus-Next) |
| Switch | Eden |
| pozostałe | RetroArch z rdzeniem jak na Windows |

Gdy ten sam emulator jest zainstalowany w kilku wersjach (np. RetroArch ze Sklepu Play
i z pliku APK), w Ustawieniach widać każdą osobno, z wersją i źródłem; domyślnie
najnowsza.

RetroArch dostaje ścieżkę pliku, pozostałe emulatory — plik z uprawnieniem do odczytu.
Przed startem gra jest przygotowana jak na Windows: ZIP z kilkoma plikami (np. **MSU-1**:
ROM + `.msu` + `.pcm`, Amiga na kilku dyskietkach) zostaje rozpakowany w całości obok gry,
gry na kilku płytach dostają playlistę `.m3u` (zmiana płyty w emulatorze). Platformy
i foldery z grami są te same co na komputerze — EmuStart na Windows przekazuje serwerowi
swoje foldery z grami (np. `WHDLoad`).
Dla RetroArcha każdy rdzeń pasujący do platformy jest osobną pozycją (np. SNES: Snes9x
albo bsnes) — pierwszy to domyślny. Rdzenie pobierz w samym RetroArchu (Online Updater →
Core Downloader).

## Profile i zapisy gier

Telefon używa **tych samych profili co EmuStart na komputerze** (foldery profili na
serwerze). Przy pierwszym uruchomieniu wybierz swój profil („Kto gra na tym telefonie?”),
później: Start → **Zmień profil**. Zapisy zastane w emulatorach telefonu trafiają do
pierwszego wybranego profilu.

Zapisy z gry są **wspólne z komputerem**:

| Emulator w telefonie | Co jest synchronizowane | Na serwerze (wspólne z PC) |
|---|---|---|
| RetroArch | zapisy gry (`.srm`, `.sav`, …) | `save\retroarch\saves` |
| RetroArch, PS1 (SwanStation, Beetle PSX) | `<gra>.srm` ↔ karta DuckStation `<gra>_1.mcd` (ten sam format, 128 KB) | `save\duckstation\memcards` |
| DuckStation | karty pamięci gry (`<gra>_1.mcd`, `shared_card_*`) | `save\duckstation\memcards` |
| ArmSX2, AetherSX2 / NetherSX2 | karty `Mcd001.ps2`, `Mcd002.ps2` | `save\pcsx2\memcards` (PCSX2) |

- Przed grą nowsze zapisy z serwera trafiają do folderu emulatora, po powrocie do
  EmuStart zmienione wracają na serwer (poprzednia wersja zostaje w kopii zapasowej
  profilu na serwerze).
- Gra po obu stronach od ostatniej synchronizacji — zostaje nowsza wersja, druga
  w kopii zapasowej, EmuStart pokaże komunikat.
- Profil gra na komputerze — telefon nie uruchomi gry na tym samym profilu (i odwrotnie).
- Bez połączenia gra działa na zapisach z telefonu; wyślą się przy następnym
  połączeniu (Ustawienia → Zapisy gier → „Czekają na wysłanie”).
- **Stany gry (savestate)**: RetroArch — wspólne z komputerem (`states\shared\retroarch\<rdzeń>`),
  niezależnie od ustawień RetroArcha na każdym urządzeniu (płasko, w folderze rdzenia, w folderze
  gry): przed grą nowszy stan tej gry i tego rdzenia trafia tam, gdzie RetroArch go szuka.
  Autozapis (`.state.auto`) wczytuje się sam przy starcie gry na każdym urządzeniu. Stan z sesji
  nie nadpisuje innej wersji na serwerze (np. zapisanej w międzyczasie na drugim urządzeniu) —
  trafia do kopii zapasowej (`_backup\…\konflikt`).
  Stany różnych rdzeni się nie wczytują — ustaw ten sam rdzeń na obu urządzeniach. ARMSX2 /
  AetherSX2 — osobno (`states\android\<emulator>`), kopiowanie w menedżerze zapisów.

**Wygląd na telefonie** (menu Start → *Wygląd*): suwaki zmienia się dotykiem — przyciski − / +
albo dotknięcie paska; *Wysokość wiersza listy* to odstępy między tytułami, *Lista gier* —
wielkość czcionki listy. Wygląd telefonu jest zapisywany w telefonie, osobno od komputera.
Logo gry jest pod szczegółami gry (komputer wysyła logo gier na serwer — 0.35.1).

**Foldery emulatorów.** EmuStart musi widzieć folder zapisów emulatora — w pamięci
telefonu, nie w `Android/data` (tam Android nie wpuszcza innych aplikacji):

- **RetroArch**: Ustawienia → Katalogi → *Zapisy gier* = `/storage/emulated/0/RetroArch/saves`
  (albo „obok gry”). EmuStart wykryje folder po pierwszej grze.
- **ArmSX2, AetherSX2**: w ustawieniach emulatora wybierz folder danych w pamięci
  telefonu, np. `/storage/emulated/0/ArmSX2` — karty są w jego podfolderze `memcards`.
- **DuckStation** na Androidzie trzyma dane tylko w `Android/data` i nie ma opcji zmiany
  folderu — EmuStart nie ma do nich dostępu. Dla zapisów PS1 wspólnych z PC wybierz dla PS1
  **RetroArch** (rdzeń SwanStation — DuckStation w wersji dla RetroArcha): jego zapis `.srm`
  to ta sama karta pamięci co `.mcd` DuckStation na komputerze.

Ustawienia → **Zapisy gier** pokazują wykryte foldery. EmuStart szuka folderów
`memcards` / `sstates` w całej pamięci telefonu (do 3 poziomów), a po pierwszej grze
zapamiętuje, gdzie emulator zapisał. Folder można też wskazać: **A** — przeglądarka
folderów (padem; wystarczy folder danych emulatora, np. `ArmSX2` — EmuStart sam weźmie
z niego `memcards`, `sstates`, `saves` albo `states`), **X** — z powrotem wykrywanie
automatyczne.

**Pierwsza synchronizacja** pliku, który jest już na serwerze (np. karta pamięci PS2
z komputera), zostawia wersję z serwera — wersja z telefonu trafia do kopii zapasowej,
EmuStart pokaże komunikat. Dzięki temu nowa, pusta karta z telefonu nie nadpisze
zapisów z komputera.

## Aktualizacje aplikacji

Od wersji 0.25.0 aplikacja jest podpisana stałym kluczem — kolejne wersje instalują się
na poprzedniej. **Jednorazowo** trzeba odinstalować wersję wcześniejszą niż 0.25.0
(była podpisana kluczem tymczasowym); pobrane gry w `EmuStart/games` zostają.

## Sterowanie

Jak na Windows ([Sterowanie](Sterowanie)): krzyżak/gałka, A — wybierz (gra), B —
wstecz, X — filtry, Y — przypnij grę, LB/RB — strona, LT/RT — litera, Start — menu.
Przycisk „wstecz” telefonu działa jak B.

## Gry w telefonie

- Pobrana gra zostaje w telefonie — następnym razem startuje od razu.
- Ustawienia → **Trzymaj ostatnie gry** (domyślnie 10) — starsze są usuwane;
  **przypięte** (Y) zostają zawsze.
- Przerwane pobieranie wznawia się od brakujących fragmentów.
- Bez połączenia z serwerem lista gier jest wczytywana z ostatniej kopii, a gry
  w telefonie dalej działają.

## Ograniczenia (wersja pierwsza)

- Brak grania w trakcie pobierania (Android nie pozwala aplikacji wystawić
  wirtualnego dysku) — gra najpierw się pobiera.
- Opcje gier i systemów, grafiki, metadane i ustawienia emulatorów zmienia się
  w EmuStart na komputerze z grami.
