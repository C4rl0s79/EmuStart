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

## Amiga

**Dyskietki (.ipf, .adf)** — folder `Commodore - Amiga` (No-Intro) albo `amiga`. Zipy
No-Intro zawierają obrazy `.ipf` (dokładne kopie dyskietek z zabezpieczeniami);
gra na kilku dyskietkach ma je wszystkie w jednym zipie — EmuStart wypakowuje je
i podaje emulatorowi playlistę `.m3u` (zmiana dyskietki: menu RetroArcha →
Sterowanie dyskami). Foldery `(Flux)` i `(Bitstream)` to surowe zrzuty KryoFlux —
emulatory ich nie uruchamiają, zostają nierozpoznane.

Do `.ipf` rdzeń PUAE (RetroArch) potrzebuje biblioteki **capsimg** — przy pierwszej
grze EmuStart proponuje jej pobranie (z GitHuba projektu capsimg) do folderu
`system` RetroArcha.

**WHDLoad (.lha)** — dwa osobne systemy: **Amiga WHDLoad — gry** i **Amiga WHDLoad —
dema**. Dodaj w Ustawieniach folder nadrzędny (np. `Z:\WHDLoad`) — jego podfoldery
`Games` i `Demos` zostaną rozpoznane (także nazwy `WHDLoad Games`,
`Commodore Amiga - WHDLoad - Games` itp.). Nazwy paczek zamieniane są na tytuły:
`WhereInTheWorldIsCarmenSandiego_v0.1_NTSC_2479.lha` → „Where In The World Is Carmen
Sandiego (v0.1) (NTSC)”, w demach z nazwą grupy, np. „Vector Balls (v1.0) (Hypnosis)”.

**Kickstart** — rdzeń PUAE najlepiej działa z oryginalnymi ROM-ami Kickstart
w folderze `system` RetroArcha (`kick34005.A500` — Kickstart 1.3 dla A500,
`kick40068.A1200` — 3.1 dla A1200 i WHDLoad). To pliki chronione prawem autorskim —
EmuStart ich nie pobiera (są np. w Amiga Forever albo z własnej Amigi). Gdy są
w [folderze z BIOS-ami](Emulatory#bios-y), EmuStart sam kopiuje je do RetroArcha
(razem z `capsimg.dll`). Bez nich rdzeń używa zamiennika AROS, a EmuStart pokazuje
ostrzeżenie przy starcie gry.

**Klawiatura** — w grach na komputery (Amiga, C64, MSX, Atari ST, ZX…) uruchamianych
przez RetroArch cała klawiatura trafia do emulowanego komputera (Shift, F1–F10, Esc,
strzałki działają jak na oryginale); skróty RetroArcha są na ten czas wyłączone.
Wyjście z gry: menu EmuStart (A + Y na padzie) albo Scroll Lock (przywraca skróty
RetroArcha) i Esc.

## Tłumaczenia (wersje gier)

Fanowskie tłumaczenia trzymaj w osobnym folderze kolekcji — EmuStart pokazuje je jako
**wersje** oryginału, nie jako osobne gry. Rozpoznawane są:

- folder kolekcji w folderze z grami albo dodany w Ustawieniach jako osobny folder z grami:
  w nazwie `T-Eng`, `T-En`, `T+Pol`, `[T-En]` albo słowo „Translations”/„Tłumaczenia”,
  w środku foldery systemów — np. `Z:\ROMS\T-Eng\Nintendo - Super Nintendo Entertainment System\`.
  Nic nie trzeba oznaczać — wystarczy nazwa. Pasuje układ kolekcji z RomVaulta:
  `ROMS\[T-En]Collection\Nintendo - Super Famicom [T-En] Collection\`. Foldery bez tagu
  w kolekcji (`… - Speed Hacks`, `… - MSU-MD`) to dodatkowe wersje gry z etykietą folderu,
- folder systemu z tagiem: `Nintendo - Super Nintendo Entertainment System [T-En]`,
- plik z tagiem `[T-En]`, `[T+Pol by …]` w nazwie (także obok oryginałów).

Tłumaczenie łączy się z oryginałem po tytule bez tagów (`Seiken Densetsu 3 (Japan)
[T-En by Neill Corlett]` → `Seiken Densetsu 3`). Język z tagu `[T-…]` w nazwie pliku,
a gdy go nie ma — z nazwy kolekcji. Tłumaczenie bez oryginału w bibliotece jest zwykłą
grą na liście.

Wybór: przytrzymaj A na grze → **Wersja** → oryginał albo tłumaczenie. Wybrana wersja
pobiera się, uruchamia i ma swoje zapisy (inny plik gry — zapisy i stany oryginału
zostają przy oryginale). Wybór należy do profilu gracza i jest wspólny dla komputera
i telefonu (`variants.json` w folderze profilu). Pliki na dysku zostają nietknięte —
kolekcję porządkuje RomHelper. Emulator wybrany dla oryginału (opcje gry → Emulator)
obowiązuje też tłumaczenia.

Pliki w kolekcji mogą mieć te same nazwy co oryginały — język bierze się wtedy z nazwy
kolekcji. Po dodaniu kolekcji odśwież bibliotekę (menu Start → Skanuj kolekcję ponownie);
telefon widzi tłumaczenia po skanie na komputerze z serwerem.

## Ukrycie systemu

Przytrzymane A na logo systemu → **Ukryj system**, albo w Ustawieniach → Systemy
(A włącza/wyłącza). System bez widocznych gier znika z karuzeli sam.
