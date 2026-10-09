# Aplikacja na Androida

EmuStart na Androida ma ten sam interfejs co na Windows (karuzela systemów, lista gier,
podgląd, edytor wyglądu) i obsługę padem (np. GameSir). Gry, opisy i grafiki bierze
z [serwera EmuStart](Serwer-dla-Androida) na komputerze z grami — telefon nic nie
skanuje. Gra pobiera się do telefonu (kilka strumieni naraz, wznawianie) i uruchamia
w emulatorze zainstalowanym w telefonie.

## Instalacja

1. Na komputerze z grami uruchom [serwer EmuStart](Serwer-dla-Androida) i zanotuj
   klucz (`EmuStart.exe --server-key`).
2. Pobierz `EmuStart-<wersja>-android.apk` z
   [Releases](https://github.com/C4rl0s79/EmuStart/releases) i zainstaluj (zgoda na
   instalację z nieznanych źródeł).
3. Przy pierwszym uruchomieniu zezwól na **dostęp do wszystkich plików** — gry trafiają
   do `/storage/emulated/0/EmuStart/games`, skąd czytają je emulatory.
4. W Ustawieniach wpisz **adres serwera** (np. `100.85.254.31` albo nazwę z Tailscale —
   port 8740 dopisze się sam) i **klucz**, potem **Sprawdź połączenie**.
5. Wybierz emulator dla systemów (←/→ w Ustawieniach → Emulatory).

Telefon musi być w tej samej sieci Tailscale co serwer.

## Emulatory

EmuStart wykrywa zainstalowane emulatory i uruchamia w nich grę:

| System | Emulatory |
|---|---|
| PS2 | ArmSX2, NetherSX2 |
| PS1 | DuckStation, RetroArch (SwanStation) |
| PSP | PPSSPP, RetroArch |
| GameCube / Wii | Dolphin |
| 3DS | Azahar |
| NDS | melonDS, DraStic, RetroArch |
| Dreamcast / Naomi | Flycast, Redream, RetroArch |
| N64 | M64Plus FZ, RetroArch (Mupen64Plus-Next) |
| Switch | Eden |
| pozostałe | RetroArch z rdzeniem jak na Windows |

RetroArch dostaje ścieżkę pliku, pozostałe emulatory — plik z uprawnieniem do odczytu.
Rdzenie RetroArcha pobierz w samym RetroArchu (Online Updater → Core Downloader).

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
- Zapisy (save'y) i profile — w kolejnych wersjach.
