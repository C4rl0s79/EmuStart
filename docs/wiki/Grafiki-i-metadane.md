# Grafiki i metadane

Okładki, zrzuty, logo gier, opisy i dane gier (rok, gatunek, gracze, producent) pobiera
i trzyma **[EmuStart Server](EmuStart-Server)** — jest źródłem prawdy. EmuStart na PC
i telefon pobierają je od serwera i trzymają lokalną kopię (szybki interfejs); zmiana na
serwerze trafia do wszystkich urządzeń (PC sprawdza zmiany przy starcie i co 5 minut).

## Pobieranie (panel serwera → Grafiki i opisy)

- **Brakujące grafiki i opisy** — dla wszystkich systemów albo jednego (**Pobierz** przy
  systemie), z postępem; **Zatrzymaj** przerywa.
- **Tylko grafiki**, **Tylko opisy**, **Opisy + Wikipedia** (dla gier bez opisu; wolniej,
  ok. 1–2 s na grę).
- **Baza LaunchBox** (ok. 108 MB, potem działa offline) — opisy, daty, producenci, wydawcy,
  gatunki, liczba graczy i grafiki, w tym **Clear Logo** (logo tytułów).
- Opis gry, którą ktoś ogląda na urządzeniu, a której opisu brakuje, serwer pobiera sam
  w tle.
- Opis platformy na karuzeli systemów: LaunchBox + Wikipedia/Wikidata.

Źródła grafik, w kolejności prób:

1. libretro (dokładna nazwa No-Intro/Redump),
2. libretro (dopasowanie po liście plików: inna wersja nazwy, „, The”, „(Disc 1)”),
3. LaunchBox,
4. SteamGridDB, 5. IGDB, 6. TheGamesDB — wymagają kluczy API (panel → Grafiki i opisy →
   Źródła; zapisane zaszyfrowane na serwerze). Klucze z PyLinks: EmuStart na komputerze
   administratora → Ustawienia → **Wyślij klucze źródeł grafik z PyLinks na serwer** (PyLinks
   szyfruje je kluczem tego komputera — odczytać da się je tylko tam).

Dopasowanie po nazwie w zewnętrznych bazach wymaga podobieństwa ≥ 0,8, żeby nie wstawiać
okładek innych gier.

## Jedna gra — ręcznie

- **Panel serwera** → Grafiki i opisy → **Gra — grafiki i opis**: wyszukaj grę, zmień
  okładkę, zrzut albo logo (propozycje, szukanie pod inną nazwą, usunięcie), popraw opis
  i dane gry, **Przywróć dane pobrane**, **Pobierz opis z sieci**.
- **Administrator na PC albo telefonie**: przytrzymane A na grze → Okładka / Zrzut ekranu
  / Logo gry / Metadane i opis / Pobierz opis z sieci. Zapis idzie na serwer — widać go na
  wszystkich urządzeniach. Zwykłe konto widzi metadane tylko do odczytu.

Ręczne zmiany wygrywają z automatycznymi.

## Logo systemów

Panel serwera → Konfiguracja → **Systemy — logo i nazwy** (logo, nazwa, poświata) albo
administrator na urządzeniu: przytrzymane A na logo systemu → Logo / Poświata logo / Nazwa.
Tak samo na wszystkich urządzeniach.

Propozycje logo: wbudowane, motywy EmulationStation / ES-DE (Art Book Next, Carbon, Alekfull NX,
Canvas, Simple, Pixel, SNES Mini), ikony z 9 motywów RetroArcha — z internetu, bez plików na
serwerze — oraz własna paczka logo (np. `platform_logos` z PyLinks): panel → Konfiguracja →
Foldery → Paczka logo platform. Serwer wczytuje ją do jednego pliku `server\media\_logopack.media`
(folder może potem zniknąć); ten plik można też po prostu skopiować do `server\media`. Kafelki mają szachownicę w szarościach:
widać na niej i czarne, i białe warianty.

## Amiga WHDLoad

- **Gry** — szukane w LaunchBox (platforma Commodore Amiga) po tytule z nazwy paczki;
  „Kings Quest 5” znajduje „King's Quest V”, „Speedball 2” — „Speedball 2: Brutal
  Deluxe” (dalsza część musi być podtytułem, a numery części muszą się zgadzać).
  W ostateczności dane i grafiki brane są z tej samej gry w kolekcji zipów Amigi.
- **Dema** — dane z [Pouet.net](https://www.pouet.net) i [Demozoo](https://demozoo.org):
  grupa, rok, party z miejscem w konkursie, typ (demo, intro, musicdisk…), opis
  i zrzut ekranu. Grupa z nazwy paczki (`…_v1.0_Hypnosis`) rozstrzyga, gdy dem o tej
  samej nazwie jest kilka. Zapytania najwyżej jedno na sekundę, więc pełne pobranie dla
  ok. 900 dem trwa kilkadziesiąt minut.

## Miejsce na dysku

- Grafiki zapisywane są **zmniejszone do rozmiaru ekranowego, w WebP** (okładki do 900 px,
  logo do 1000×400, zrzuty do 960×720).
- **Zmniejsz zapisane grafiki** — przerabia grafiki pobrane wcześniej (zwykle ok. 8× mniej
  miejsca).
- Pobieranie zatrzymuje się z komunikatem, gdy na dysku serwera zostaje mniej niż 2 GB.

## Gatunki

Źródła nazywają gatunki różnie („RPG”, „Role-Playing”, „Role playing games”). EmuStart
ujednolica je przy filtrowaniu i wyświetlaniu (RPG, Fighting, Racing, Beat 'em Up,
Puzzle, Sports…); zapisane metadane zostają bez zmian. Wpisy złożone („Racing / Driving”)
liczą się do każdego gatunku.
