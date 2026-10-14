# Pliki i dane

## Obok programu (lokalnie)

| Ścieżka | Zawartość |
|---|---|
| `config.json` | ustawienia komputera: serwer, emulator per system, wygląd |
| `data\library.sqlite` | kopia biblioteki z serwera, opisy, historia gry, stan pamięci podręcznej |
| `data\media\<system>.media` | kopia grafik z serwera — jeden plik na system (pobierane przy wyświetleniu) |
| `data\media\_stany\` | obrazki stanów gry (z plików stanów emulatorów) |
| `data\resume\<profil>\` | stany „wznowienia” |
| `cache\<system>\` | lokalne kopie gier |
| `profiles\<id>\` | save'y, kopie ustawień, `emustart.json`, `retroachievements.json` profilu |
| `profiles\_machine\` | ostatnie ustawienia emulatorów tego komputera sprzed podmiany na profil |
| `logs\emustart.log` | log (rotowany, 4 × 2 MB) |

## Na serwerze

Biblioteka, grafiki, opisy, konta i profile kont (zapisy) — w folderze `server\` obok
`EmuStartServer.exe`, patrz [EmuStart Server](EmuStart-Server) i
[Kilka urządzeń, jeden serwer](Kilka-komputerów-jeden-NAS).

## W emulatorach

- Foldery save'ów są dowiązaniami (junction) do `profiles\<id>\…` — usunięcie
  dowiązania nie usuwa save'ów.
- Na czas gry EmuStart zmienia w ustawieniach emulatora kolejność padów (wraca po
  grze) i wpisuje konto RetroAchievements profilu.

## Pliki tymczasowe

`%TEMP%\emustart\run\` — wypakowane gry i konfiguracja sesji RetroArcha;
usuwane po grze (po awarii — przy następnym starcie).

## Kopia zapasowa

Na komputerze z EmuStart wystarczy `config.json` — biblioteka, grafiki i zapisy są na
serwerze. Serwer kopiuje konta sam (panel → Konta → Pliki i kopie); całość serwera to
folder `server\`.
