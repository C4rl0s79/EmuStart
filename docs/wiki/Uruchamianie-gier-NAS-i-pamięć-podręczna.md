# Uruchamianie gier i pamięć podręczna

Gry pochodzą z **[EmuStart Server](EmuStart-Server)**. EmuStart pobiera je na dysk
(pamięć podręczną) i stamtąd uruchamia; gry już pobrane działają też bez serwera.

## Jak startuje gra

1. **Gra jest w pamięci podręcznej** → start od razu z dysku lokalnego.
2. **Mała gra** (kartridż, arcade; domyślnie do 64 MB) → pobranie (zwykle sekunda), start.
3. **Duża gra płytowa** (CHD/RVZ/ISO) → ekran pobierania (pobrano, zostało, prędkość,
   pozostały czas) albo — z WinFsp — **granie w trakcie pobierania** (niżej).
4. **Gry PS3 (katalogi)** → zawsze najpierw pobranie.
5. **Arcade** — razem z grą pobierają się jej zależności z serwera: gra nadrzędna (dla
   wersji gry, np. japońskiej) i BIOS (np. `neogeo.zip`), obok gry.

Serwer nie odpowiada, a gry nie ma na dysku — komunikat; gry pobrane wcześniej działają.

## Pobieranie i granie w trakcie pobierania

- **Pobieranie wieloma strumieniami** (osobne połączenie na każdy strumień) — przez
  Tailscale na dużą odległość kilka razy szybciej niż jednym połączeniem. Plik pobierany
  jest blokami, a mapa pobranych bloków (`.part.map`) pozwala wznowić przerwane pobieranie
  od brakujących bloków.
- **Graj w trakcie pobierania (WinFsp)** — gdy zainstalowany jest
  [WinFsp](https://winfsp.dev), duża gra płytowa startuje od razu z wirtualnego dysku
  EmuStart (wolna litera, np. `Y:`). Fragmenty pobrane czytane są z dysku lokalnego,
  brakujące pobierane natychmiast, przed resztą kolejki (typowe ładowanie gry PS2 z serwera
  oddalonego o ponad 1000 km: ok. 8 s). Krótkie przycięcia możliwe tylko przy pierwszym
  wejściu w niepobrany fragment.
- Ustawienia → „…gdy pobieranie potrwa dłużej niż” — próg: krótsze pobranie najpierw
  w całości. Bez WinFsp — ekran pobierania.

## Pamięć podręczna

- Trzyma **ostatnie gry** (domyślnie 10, Ustawienia → Trzymaj ostatnie gry)
  **plus przypięte**.
- **Y na grze** przypina ją: zostaje na dysku na stałe i pobiera się w tle (postęp
  w górnym pasku); przerwane pobieranie przypiętej gry wznawia się samo po wejściu na nią.
- Na liście gry lokalne mają znacznik „lokalnie”, przypięte 📌, kopiowane „⬇ kopiuje”.
- **Usunięcie z dysku**: przytrzymaj A na grze → **Usuń z dysku** → A, by potwierdzić. Gra
  zostaje na serwerze. Na telefonie tak samo: **Usuń z telefonu**.

## Bez serwera

W górnym pasku: „Serwer niedostępny” — działają gry z pamięci podręcznej, lista i grafiki
z lokalnej kopii. Zapisy z tego czasu wysyłają się na serwer przy następnej okazji (patrz
[Kilka urządzeń, jeden serwer](Kilka-komputerów-jeden-NAS)).

## Po wyjściu z gry

Ekran gry znika od razu. Zapis save'ów i ustawień profilu na serwer idzie w tle —
w górnym pasku widać „⇅ wysyłam zapisy: …”. Następna gra poczeka na koniec tego zapisu;
zamknięcie EmuStart też (do 2 minut).

## Wczytanie stanu

Przytrzymane A na grze → **Wczytaj zapis**: stany gry, najnowsze pierwsze, z obrazkiem,
gdy emulator go zapisał (RetroArch — EmuStart włącza obrazki stanów; PCSX2; DuckStation).
