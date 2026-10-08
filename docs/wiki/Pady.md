# Pady i ich kolejność

## Obsługa padów w menu

EmuStart czyta pady przez **XInput**, tylko gdy jego okno jest na wierzchu —
w trakcie gry i w innych programach nie dotyka padów wcale. Ustawienie:
Ustawienia → Wygląd → Obsługa padów w menu (XInput / przeglądarka / wyłączona),
albo parametr `--pady=…` (zob. [Instalacja](Instalacja-i-pierwsze-uruchomienie)).

Pady PlayStation muszą być widoczne jako XInput — przez Steam Input albo DS4Windows.

## Kolejność graczy

Start → **Kolejność padów**. Windows numeruje pady w kolejności wykrycia (np. pad
Bluetooth przed USB), co bywa niewygodne. EmuStart przed startem gry ustawia
w emulatorze kolejność **Gracz 1, 2, …** według wybranego trybu:

| Tryb | Kolejność |
|---|---|
| Jak w Windows | kolejność podłączenia, bez zmian |
| Bezprzewodowe przed przewodowymi | pady Bluetooth/radiowe jako pierwsi gracze |
| Ręcznie | wciśnij przycisk na padzie, który ma być Graczem 1, potem 2… |

Działa w RetroArchu, DuckStation, PCSX2 i Dolphinie. Zmiana dotyczy tylko sesji —
po grze ustawienia emulatora wracają (także po awarii, przy następnym starcie).
