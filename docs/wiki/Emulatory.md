# Emulatory

## Przypisanie

**Wykryj emulatory i skanuj** (Ustawienia) przypisuje każdemu systemowi emulator
znaleziony w folderze emulatorów. Zmiana: Ustawienia → Systemy (← / →) albo
przytrzymane A na logo systemu → Emulator. Pojedyncza gra może mieć własny
emulator: przytrzymane A na grze → Emulator.

## Emulatory z serwera

Wersje emulatorów ustala **EmuStart Server** ([[EmuStart Server|EmuStart-Server]] → zakładka
**Emulatory**): raz dziennie (i przyciskiem w panelu) pobiera z internetu nowe wydania emulatorów
potrzebnych systemom biblioteki i trzyma je u siebie. EmuStart na PC bierze emulatory
**tylko z serwera**:

- po wybraniu folderu emulatorów (Ustawienia), przy starcie i co kilka minut sam
  **doinstalowuje brakujące** i **aktualizuje starsze** — w tle, pasek „⬇ emulatory” u góry,
- emulator zastany w folderze (np. z RetroBata) też jest aktualizowany — wymieniane są tylko
  pliki programu; **konfiguracja i zapisy zostają** (np. `settings.ini`, `inis\`, `memcards\`,
  `retroarch.cfg`, `saves\`, `states\`), plików spoza wydania nic nie usuwa,
- działający emulator nie jest ruszany — aktualizacja przy następnej okazji,
- bez serwera nic się nie pobiera; EmuStart działa na tym, co jest zainstalowane,
- **wymagane ustawienia** emulatorów (panel serwera → Emulatory) EmuStart nakłada tuż przed
  każdą grą — np. wyjście z gry bez okna z pytaniem.

Uruchomienie gry z systemu bez emulatora proponuje jego instalację od razu
(`<folder emulatorów>\<Emulator>`); wszystkie brakujące naraz: Ustawienia →
**Pobierz brakujące emulatory**.

Źródła, z których pobiera serwer (najnowsze wydania, przez HTTPS):

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
| Eden, Citron | Switch | eden-emu.dev, GitHub |
| Azahar | 3DS | GitHub |
| melonDS | NDS | GitHub |
| Flycast | Dreamcast, Naomi | GitHub |
| xemu / Xenia | Xbox / Xbox 360 | GitHub |
| MAME | arcade | GitHub |

Archiwa `.7z` rozpakowuje na serwerze 7-Zip (zainstalowany albo `7zr.exe` pobrany z 7-zip.org).
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
