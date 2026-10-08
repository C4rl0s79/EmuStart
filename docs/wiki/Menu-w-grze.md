# Menu w grze

W trakcie gry przytrzymaj **A + Y przez 2 sekundy** (pad PlayStation: X + trójkąt).
EmuStart pauzuje emulator (gdy trzeba) i pokazuje menu nad grą:

| Pozycja | Działanie |
|---|---|
| Wróć do gry | zamyka menu |
| Zapisz stan | zapis w bieżącym slocie emulatora |
| Wczytaj stan | wczytanie z bieżącego slotu |
| Quicksave i wyjdź | zapisuje stan „wznowienia” i zamyka grę — następne uruchomienie tej gry wczyta go automatycznie |
| Wyjdź z gry | zamyka emulator |

Pozycje niedostępne dla danego emulatora są wyszarzone (tabela w [Emulatory](Emulatory)).

## Wznawianie

- Stan „wznowienia” jest kopiowany poza sloty emulatora, więc późniejsze zapisy
  go nie nadpiszą. Jest jednorazowy — po wczytaniu znika.
- Jest zapisywany w profilu na NAS-ie, więc **można przerwać grę na jednym
  komputerze i dokończyć na innym**. Zużyty na jednym komputerze znika też na
  pozostałych.
- Wybrany zapis można też wczytać ręcznie: przytrzymane A na grze → Wczytaj zapis.

## Uwagi

- Menu nie działa, gdy obsługa padów w menu jest wyłączona (`--pady=brak`).
- W trybie hardcore RetroAchievements stany są zablokowane przez emulator.
