# Emulatory

## Przypisanie

**Wykryj emulatory i skanuj** (Ustawienia) przypisuje każdemu systemowi emulator
znaleziony w folderze emulatorów. Zmiana: Ustawienia → Systemy (← / →) albo
przytrzymane A na logo systemu → Emulator. Pojedyncza gra może mieć własny
emulator: przytrzymane A na grze → Emulator.

## Pobieranie

Uruchomienie gry z systemu bez emulatora proponuje jego pobranie i instalację
w folderze emulatorów (`<folder emulatorów>\<Emulator>`). Wszystkie brakujące naraz:
Ustawienia → **Pobierz brakujące emulatory**.

Źródła (zawsze najnowsze wydania, przez HTTPS):

| Emulator | Systemy (przykłady) | Źródło |
|---|---|---|
| RetroArch + rdzenie | NES, SNES, Mega Drive, GB/GBA, PC Engine, arcade… | buildbot.libretro.com |
| DuckStation | PS1 | GitHub |
| PCSX2 | PS2 | GitHub |
| RPCS3 | PS3 | GitHub |
| PPSSPP | PSP | GitHub |
| Vita3K | PS Vita | GitHub |
| Dolphin | GameCube, Wii | dolphin-emu.org |
| capsimg (dla rdzenia PUAE) | Amiga `.ipf` | GitHub |
| Cemu | Wii U | GitHub |
| Eden | Switch | eden-emu.dev |
| Azahar | 3DS | GitHub |
| melonDS | NDS | GitHub |
| Flycast | Dreamcast, Naomi | GitHub |
| xemu / Xenia | Xbox / Xbox 360 | GitHub |
| MAME | arcade | GitHub |

Archiwa `.7z` rozpakowuje 7-Zip (zainstalowany albo `7zr.exe` pobrany z 7-zip.org).
Emulatory instalowane są w trybie przenośnym (ustawienia obok exe), gdy go mają.

## BIOS-y

Ustawienia → **BIOS-y**: folder z plikami BIOS w układzie folderu `system`
RetroArcha (np. `bios` z RetroBat lub Batocery). Przed startem gry na RetroArchu
EmuStart czyta z pliku `info\<rdzeń>_libretro.info`, jakich BIOS-ów potrzebuje
rdzeń, i kopiuje **brakujące** do folderu `system` RetroArcha (istniejących nie
nadpisuje). Dla Amigi kopiuje też `capsimg.dll`.

## Co EmuStart umie z danym emulatorem

| Emulator | Menu w grze (stany) | Wznawianie | Kolejność padów | Profile: save'y | Profile: ustawienia | RetroAchievements |
|---|---|---|---|---|---|---|
| RetroArch | ✔ (komendy sieciowe) | ✔ | ✔ | ✔ | ✔ | ✔ |
| DuckStation | ✔ (skróty z ustawień) | ✔ | ✔ | ✔ | ✔ | ✔ |
| PCSX2 | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Dolphin | ✔ | ✔ | ✔ | ✔ | — | — |
| PPSSPP | ✔ | ✔ | — | ✔ | — | — |
| RPCS3 | — | — | — | ✔ | — | — |
| pozostałe | wyjście z gry | — | — | — | — | — |

Skróty zapisu/wczytania stanu EmuStart czyta z ustawień emulatora, więc działają
także po ich zmianie.

## Pliki gry dla emulatora

- **ZIP**: emulatory, które czytają archiwa (RetroArch, MAME, Snes9x, mGBA…), dostają
  zip wprost; pozostałym EmuStart wypakowuje go do pamięci (`%TEMP%`, plik
  tymczasowy). Zip z kilkoma plikami (np. MSU-1) jest wypakowywany zawsze.
- **Gry wielopłytowe**: emulatory obsługujące `.m3u` dostają playlistę płyt.
- Folder sesji z wypakowanymi plikami jest usuwany po grze (a po awarii — przy
  następnym starcie).
