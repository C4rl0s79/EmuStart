# Uruchamianie gier: NAS i pamięć podręczna

## Jak startuje gra

1. **Gra jest w pamięci podręcznej** → start od razu z dysku lokalnego.
2. **Mała gra** (kartridż, arcade; domyślnie do 64 MB) → kopia do pamięci
   podręcznej (zwykle sekunda), potem start.
3. **Duża gra płytowa** (CHD/RVZ/ISO) → EmuStart mierzy prędkość na początku
   kopiowania:
   - **LAN** (≥ 200 Mb/s, próg w Ustawieniach): start wprost z NAS-a, kopia do
     pamięci podręcznej idzie w tle — następnym razem gra uruchomi się z dysku,
   - **zdalnie** (wolniej, np. Tailscale): ekran pobierania — pobrano, zostało,
     prędkość, pozostały czas. **X — Graj teraz (z sieci)** uruchamia bez czekania.
4. **Gry PS3 (katalogi)** → zawsze najpierw pobranie.

Tryb sieci można wymusić: Ustawienia → Tryb sieci (automatycznie / zawsze LAN /
zawsze zdalnie).

## Szybsze pobieranie i granie w trakcie pobierania

- **Pobieranie w 4 strumieniach naraz** (pliki od 32 MB) — na łączu przez Tailscale
  zmierzone 268 Mb/s zamiast 110 Mb/s jednym strumieniem. Plik pobierany jest
  blokami po 1 MB, a mapa pobranych bloków (`.part.map`) pozwala wznowić przerwane
  pobieranie od brakujących bloków.
- **Graj w trakcie pobierania (WinFsp)** — gdy zainstalowany jest
  [WinFsp](https://winfsp.dev), gra w trybie zdalnym startuje od razu z wirtualnego
  dysku EmuStart (wolna litera, np. `Y:`). Fragmenty już pobrane czytane są z dysku
  lokalnego, brakujące pobierane natychmiast, przed resztą kolejki (typowe ładowanie gry
  PS2 z NAS-a oddalonego o ponad 1000 km: ok. 8 s), a pobieranie w tle
  kieruje się w miejsce, które gra właśnie czyta. Krótkie przycięcia są możliwe tylko
  przy pierwszym wejściu w niepobrany fragment. Po pobraniu całości gra jest w pamięci
  podręcznej jak zwykle.
- Domyślnie gra startuje od razu (granie w trakcie pobierania). W Ustawieniach →
  „…gdy pobieranie potrwa dłużej niż” można ustawić próg: krótsze pobranie najpierw
  w całości.
- EmuStart sprawdza WinFsp przy starcie (wynik w logu i w Ustawieniach → „Graj
  w trakcie pobierania”). Bez WinFsp działa jak dotąd: ekran pobierania i „Graj teraz”
  wprost z NAS-a. Opcję można wyłączyć w Ustawieniach.

## Gry z serwera EmuStart

Gdy na komputerze z grami działa [serwer EmuStart](Serwer-dla-Androida), EmuStart na
Windows pobiera gry z niego zamiast przez SMB. Przez Tailscale na dużą odległość to
kilka razy szybciej: SMB łączy się z NAS-em jednym połączeniem TCP, serwer — osobnym
połączeniem na każdy z 8 strumieni. Pomiar (ok. 1000 km, 33 ms, łącze 1 Gb/s):
SMB ok. 25 MB/s, serwer ok. 78 MB/s.

Ustawienia → **Serwer EmuStart**:

1. **Adres serwera** — podpowiadany z udziału sieciowego z grami (np. dysk `Z:` →
   `http://100.85.254.31:8740`).
2. **Klucz serwera** — na serwerze `EmuStart.exe --server-key` (kopiuje klucz do
   schowka), tu **Wklej klucz ze schowka** albo wpisz ręcznie.
3. **Sprawdź połączenie**.

Gra jest szukana na serwerze po systemie i ścieżce; plik idzie z serwera tylko przy
zgodnym rozmiarze. W każdym innym przypadku (serwer wyłączony, starsza wersja serwera,
brak gry) — przez SMB jak dotąd. Granie w trakcie pobierania (WinFsp) działa tak samo.
Gdy NAS przez SMB jest niedostępny, a serwer działa, gry i tak się pobiorą.
Ekran pobierania pokazuje źródło („Pobieranie z serwera EmuStart”).

## Pamięć podręczna

- Trzyma **ostatnie gry** (domyślnie 10, Ustawienia → Trzymaj ostatnie gry)
  **plus przypięte**.
- **Y na grze** przypina ją: zostaje na dysku na stałe i pobiera się w tle
  (postęp w górnym pasku).
- Na liście gry lokalne mają znacznik „lokalnie”, przypięte 📌, kopiowane „⬇ kopiuje”.

## Bez NAS-a

Gdy NAS jest niedostępny, w górnym pasku widać „NAS offline, tylko gry lokalne” —
działają gry z pamięci podręcznej. Save'y zapisane w tym czasie wysyłają się na NAS
przy następnej okazji (patrz [Kilka komputerów, jeden NAS](Kilka-komputerów-jeden-NAS)).

## Po wyjściu z gry

Ekran gry znika od razu. Zapis save'ów i ustawień profilu na NAS idzie w tle —
w górnym pasku widać „⇅ zapisuję save'y na NAS: …”. Następna gra poczeka na koniec
tego zapisu; zamknięcie EmuStart też (do 2 minut).
