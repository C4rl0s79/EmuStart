# EmuStart — koncepcja

Frontend do uruchamiania gier na emulatorach, sterowany padem, przystosowany do
kolekcji leżącej na NAS-ie dostępnym raz przez LAN, raz przez Tailscale.

Stan: **wersja robocza 4** (2026-10-07), po dwóch rundach pytań.

---

## 1. Założenia (ustalone)

| Temat | Decyzja |
|---|---|
| System | tylko Windows |
| Sterowanie | pad jako podstawowy kontroler, pełny ekran; mysz/klawiatura pomocniczo |
| Wygląd | w stylu EmulationStation: karuzela systemów → lista gier z podglądem |
| Niezależność | osobny program, nie dzieli bazy ani grafik z PyLinksWeb (kod modułów kopiujemy/adaptujemy) |
| ROM-y | `Z:\ROMS\ROMS\<system>\` — zmapowany dysk, podkatalogi z nazwami jak w ES (`psx`, `ps2`, `snes`, `fbneo`…) |
| Formaty | ZIP (kartridże, arcade), CHD (płyty), RVZ (GC/Wii), PS3: ISO **i** katalogi gier w `ps3\` |
| Emulatory | `D:\emu\emulatory\<emulator>\` — mieszanka RetroArch i samodzielnych |
| Program + dane | `D:\emustart\`, grafiki i baza w `data\` |
| Cache gier | 10 ostatnio uruchomionych + dowolna liczba przypiętych (nie liczą się do limitu) |
| Pobieranie „na zaznaczenie” | wyłączone |
| Wideo-podglądy | opcja, domyślnie wyłączone |
| Grafiki | bez ScreenScrapera: libretro-thumbnails (dokładne nazwy No-Intro/Redump), dla arcade nazwy z `mame -listfull`, SteamGridDB jako zapas (własny klucz) |
| Profile | nieokreślona liczba osób, mogą grać równocześnie; save'y na `Z:\emustart\Profiles\<profil>\save` |
| Wolny RAM | ~48 GB |

## 2. Sieć: dwa bardzo różne tryby

| | LAN | Tailscale (zdalnie) |
|---|---|---|
| Przepustowość | ~1 Gb/s (~110 MB/s) | ~30 Mb/s (~3,7 MB/s) |
| ZIP kartridża (0,1–8 MB) | natychmiast | 1–3 s |
| CHD PS1 / DC (~0,4–1 GB) | 4–10 s | 2–5 min |
| CHD PS2 (~2–4 GB) | 20–40 s | 10–20 min |
| PS3 (10–30 GB) | 2–5 min | 45 min – 2,5 h |

Program **mierzy połączenie** (odczyt testowy z `Z:`) przy starcie i przed
uruchomieniem nieskopiowanej gry, a potem dobiera strategię. Tryb można wymusić
w ustawieniach (auto / LAN / zdalnie).

## 3. Uruchamianie gry

```
wybór gry
  ├─ jest w cache?            → start z cache
  └─ nie ma:
       ├─ mała gra (< 64 MB)  → kopiuj do cache → start
       ├─ CHD / RVZ / ISO
       │    ├─ LAN            → start wprost z Z:, równolegle kopiowanie do cache
       │    └─ zdalnie        → ekran pobierania → po pobraniu start z cache
       └─ katalog PS3         → zawsze najpierw pobierz do cache → start
```

### 3.1 Ekran pobierania

Duży, wyraźny komunikat: nazwa gry, pasek postępu, **pobrano / całość**
(np. `1,24 GB / 3,80 GB`), **zostało** (`2,56 GB`), prędkość (`3,6 MB/s`),
**pozostały czas** (`11 min 50 s`) i liczba plików przy grach wieloplikowych.
Przyciski: **[B] Anuluj** (część zostaje do wznowienia) i, w trybie
zdalnym dla płyt, **[X] Graj mimo to (z sieci)**.

### 3.2 ZIP i „wczytanie do RAM”

- Emulator czyta ZIP sam (MAME, FBNeo, rdzenie RetroArch dla kartridży) →
  nie rozpakowujemy.
- Emulator wymaga pliku → rozpakowanie do `%TEMP%\emustart\run\` z atrybutem
  `FILE_ATTRIBUTE_TEMPORARY` (Windows trzyma go w RAM, bez sterowników i bez
  uprawnień administratora). Sprzątanie po wyjściu z gry.
- MAME/FBNeo: ZIP + sety rodzicielskie i BIOS-y z tego samego katalogu trafiają do
  cache razem.

### 3.3 Cache

- `D:\emustart\cache\<system>\…` (ścieżka do zmiany).
- Limit **10 ostatnich gier** (LRU) + **przypięte** poza limitem.
- Kopiowanie do `.part` z wznawianiem, zmiana nazwy dopiero po kompletnym
  pobraniu. Kopia jest nieaktualna, gdy plik na NAS ma inny rozmiar lub datę.
- Gra wieloplikowa (m3u + płyty, katalog PS3) to jedna pozycja cache.
- Offline: grywalne tylko gry z cache, reszta wyszarzona.

### 3.4 Strumieniowanie CHD (etap 3, opcjonalne)

Wirtualny plik przez **WinFsp**: emulator widzi kompletny `.chd`, brakujące bloki
są pobierane priorytetowo, reszta w tle. Daje „graj od razu” przez Tailscale.

## 4. Menu w grze (hotkey)

**A + Y przytrzymane przez 2 s** (pad PS: X + trójkąt) → menu nad emulatorem.
Przycisku Xbox/PS nie używamy, bo przechwytują go same emulatory.

| Pozycja | Działanie |
|---|---|
| Wróć do gry | zamyka menu, wznawia grę |
| Zapisz stan | zapis do bieżącego slotu emulatora |
| Wczytaj stan | wczytanie z bieżącego slotu |
| Quicksave i wyjdź | zapis stanu „wznowienia” i zamknięcie; przy następnym starcie gra wczytuje ten stan **jednorazowo** |
| Wyjdź z gry | zamknięcie emulatora |

Technicznie:

- Pad czyta wątek w Pythonie przez XInput, niezależnie od fokusu. Pad PS musi
  być widoczny jako XInput (Steam Input / DS4Windows). Gdy menu jest otwarte,
  ten sam wątek steruje menu; fokus wraca do gry dopiero po puszczeniu przycisków.
- Na czas menu emulator jest pauzowany jego własnym skrótem (DuckStation, PCSX2,
  Dolphin); RetroArch i PPSSPP pauzują się same po utracie fokusu.
- Stan „wznowienia” kopiowany jest do `data/resume/<profil>/`, poza sloty emulatora.

| Emulator | zapis / wczytanie w trakcie | wznowienie przy starcie |
|---|---|---|
| RetroArch | komendy UDP (`--appendconfig` włącza je na czas sesji) | autozapis przy wyjściu + `savestate_auto_load` tylko po quicksave |
| DuckStation | skróty z `settings.ini` (u Ciebie F2 / F1) | `-statefile` |
| PCSX2 | skróty z `PCSX2.ini` (F1 / F3) | `-statefile` |
| Dolphin | skróty z `Hotkeys.ini` (Shift+F1 / F1) | `-s` |
| PPSSPP | skróty z `controls.ini` (domyślnie F2 / F4) | `--state=` |
| pozostałe | — | — (menu: tylko „Wróć” i „Wyjdź”) |

Wyjście: RetroArch komendą `QUIT`, pozostałe zamknięciem okna. Jeśli emulator pyta
o potwierdzenie (DuckStation: `ConfirmPowerOff`), ma wtedy fokus i wystarczy A;
po 15 s zamykamy go siłą.

## 5. Profile i save'y (etap 4, schemat od początku)

- Przy starcie wybór profilu padem. Liczba profili nieograniczona.
- NAS: `Z:\emustart\Profiles\<profil>\save\<emulator>\…`; lokalna kopia robocza
  `D:\emustart\profiles\<profil>\…`.
- Przed startem gry: pobranie nowszych save'ów z NAS. Po wyjściu: wysłanie.
- Podpięcie bez grzebania w konfiguracji emulatora: katalogi save'ów/stanów
  emulatora → **junction** na folder profilu na czas gry.
- **Równoczesna gra:** emulator jest jeden na komputer, więc konflikt powstaje
  tylko przy tym samym profilu na dwóch maszynach. Blokada
  `Profiles\<profil>\lock` (komputer + czas), ostrzeżenie przy próbie wejścia.
- **PS3 / RPCS3:** gry PSN i aktualizacje instalujesz w RPCS3 (`dev_hdd0`), to
  wspólne dla wszystkich. Save'y RPCS3 trzyma per użytkownik
  (`dev_hdd0\home\0000000N\savedata`), więc profil EmuStart → użytkownik RPCS3
  (albo junction `savedata`).

Baza od pierwszego dnia ma tabelę profili; czas gry, ulubione i historia są per
profil.

## 6. Układ plików

```
D:\emustart\
  emustart.exe / main.py
  config.json
  data\
    library.sqlite        ← gry, profile, historia, czas gry, stan cache
    media\<system>\       ← <gra>.box.png  <gra>.snap.png  (wideo opcjonalnie)
  cache\<system>\         ← lokalne kopie gier
  profiles\<profil>\      ← save'y (etap 4)
  logs\
```

## 7. Technologia

- Python + pywebview (WebView2), pełny ekran; UI w HTML/CSS/JS.
- Pad w UI: Gamepad API (WebView2). Pad w grze (hotkey): XInput w Pythonie.
- SQLite. Paczka: PyInstaller, przenośny `.exe`.

## 8. Etapy

1. **MVP:** konfiguracja, autodetekcja systemów i emulatorów, skan do SQLite,
   UI w stylu ES z padem, uruchamianie z cache/NAS z ekranem pobierania,
   cache 10 + przypięte, pomiar sieci, okładki libretro, nazwy arcade z MAME.
   **Stan: zrobione (2026-10-07), do sprawdzenia na prawdziwych grach i padzie.**
2. Menu w grze (A+Y przez 2 s), quicksave i wyjdź z jednorazowym wczytaniem.
   **Stan: zrobione (2026-10-07), do sprawdzenia z prawdziwymi emulatorami.**
3. Ulubione, ostatnio grane, czas gry, wyszukiwarka, opcjonalne wideo.
   Narzędzie „Grafiki” (libretro + SGDB/IGDB/TGDB z kluczami z PyLinks) — **zrobione**.
4. Profile i synchronizacja save'ów.
5. Strumieniowanie CHD (WinFsp).
