# Grafiki i metadane

Start → **Grafiki i metadane**.

## Baza LaunchBox

**Pobierz bazę LaunchBox** (ok. 108 MB, potem działa offline) — opisy, daty,
producenci, wydawcy, gatunki, liczba graczy oraz grafiki w kategoriach, w tym
**Clear Logo** (logo tytułów, opcja „Tytuły gier jako logo”).

## Pobieranie

- **Pobierz brakujące grafiki** — okładki, zrzuty i (gdy włączone) logo tytułów,
  dla całości albo wybranego systemu, z postępem; X zatrzymuje.
- **Pobierz brakujące metadane i opisy** — LaunchBox + baza RetroArcha;
  opcjonalnie **z Wikipedią** dla gier bez opisu (wolniej, ok. 1–2 s na grę).
- Opis platformy na karuzeli systemów: LaunchBox + Wikipedia/Wikidata.

Źródła grafik, w kolejności prób:

1. libretro (dokładna nazwa No-Intro/Redump),
2. libretro (dopasowanie po liście plików: inna wersja nazwy, „, The”, „(Disc 1)”),
3. LaunchBox,
4. SteamGridDB, 5. IGDB, 6. TheGamesDB — wymagają kluczy API (import z PyLinksWeb).

Dopasowanie po nazwie w zewnętrznych bazach wymaga podobieństwa ≥ 0,8, żeby nie
wstawiać okładek innych gier. Pojedynczą grafikę można wybrać ręcznie:
przytrzymane A na grze → Okładka / Zrzut / Logo.

## Miejsce na dysku

- Grafiki zapisywane są **zmniejszone do rozmiaru ekranowego, w WebP**
  (okładki do 900 px, logo do 1000×400, zrzuty do 960×720).
- **Zmniejsz zapisane grafiki** — przerabia grafiki pobrane wcześniej
  (zwykle ok. 8× mniej miejsca).
- Pobieranie zatrzymuje się z komunikatem, gdy na dysku zostaje mniej niż 2 GB.

## Gatunki

Źródła nazywają gatunki różnie („RPG”, „Role-Playing”, „Role playing games”).
EmuStart ujednolica je przy filtrowaniu i wyświetlaniu (RPG, Fighting, Racing,
Beat 'em Up, Puzzle, Sports…); zapisane metadane zostają bez zmian. Wpisy złożone
(„Racing / Driving”) liczą się do każdego gatunku.

## Edycja ręczna

Przytrzymane A na grze → Metadane: tytuł, rok, gatunek, gracze, producent,
wydawca, opis. Ręczne zmiany wygrywają z automatycznymi.
