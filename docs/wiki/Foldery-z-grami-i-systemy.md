# Foldery z grami i systemy

## Kilka folderów

W Ustawieniach można podać kilka folderów z grami, np.:

```
Z:\ROMS\REDUMP
Z:\ROMS\No-Intro
Z:\ROMS\ROMS
```

Gry z tego samego systemu z różnych folderów łączą się w jedną listę. Gdy ta sama
gra jest w kilku folderach, pokazuje się raz — z folderu wyżej na liście
(◀ ▶ na folderze zmienia kolejność, Y usuwa).

## Rozpoznawanie systemów

Podfoldery rozpoznawane są po nazwach:

- **EmulationStation**: `psx`, `snes`, `megadrive`, `ps2`…
- **No-Intro / Redump**: `Sony - PlayStation`, `Nintendo - Super Nintendo Entertainment System`…
- **libretro**: nazwy z bazy RetroArcha.

Folder, którego EmuStart nie rozpoznał, widać w Ustawieniach (sekcja Systemy) jako
„nierozpoznany folder — wyłączony”.

## Formaty

| Rodzaj | Formaty |
|---|---|
| Kartridże | ROM-y lub `.zip` (jeden plik w środku) |
| Płyty | `.chd`, `.iso`, `.cue/.bin`, `.rvz`, `.m3u` (gry wielopłytowe) |
| PS3 | katalogi gier (`PS3_GAME\USRDIR\EBOOT.BIN`) |
| Arcade | zestawy MAME/FBNeo (`.zip`), nazwy z bazy MAME |

Nazwy plików w konwencji No-Intro/Redump (`Gra (USA) (Rev 1).zip`) dają najlepsze
trafienia okładek i metadanych — oznaczenia w nawiasach służą też do filtrów
(region) i [ukrywania wersji](Filtry-i-ukrywanie-gier).

## MSU-1 (SNES z muzyką CD)

Folder `SNESMSU1` (system „SNES MSU-1”). Każda gra to `.zip` z ROM-em (`.sfc`),
plikiem `.msu` i ścieżkami `.pcm` **o tej samej nazwie**, w jednym katalogu
archiwum. EmuStart wypakowuje takie archiwum w całości do pamięci (także dla
RetroArcha), żeby emulator znalazł muzykę obok ROM-u. Archiwum bez ROM-u
(sama muzyka) daje komunikat o brakującym pliku gry.

Emulator musi obsługiwać MSU-1 — np. RetroArch z rdzeniem Snes9x lub bsnes.

## Ukrycie systemu

Przytrzymane A na logo systemu → **Ukryj system**, albo w Ustawieniach → Systemy
(A włącza/wyłącza). System bez widocznych gier znika z karuzeli sam.
