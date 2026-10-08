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
