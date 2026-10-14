# Menu w grze

W trakcie gry przytrzymaj **A + Y przez 2 sekundy** (pad PlayStation: X + trójkąt).
EmuStart pauzuje emulator (gdy trzeba) i pokazuje menu nad grą:

| Pozycja | Działanie |
|---|---|
| Wróć do gry | zamyka menu |
| Zapisz stan | zapis w bieżącym slocie emulatora |
| Wczytaj stan | wczytanie z bieżącego slotu |
| Reset gry | RetroArch: gra od początku bez wychodzenia (jak przycisk RESET konsoli) — np. gdy przy starcie wczytał się stan z niepasującymi ustawieniami |
| Quicksave i wyjdź | zapisuje stan „wznowienia” i zamyka grę — następne uruchomienie tej gry wczyta go automatycznie |
| Włącz / wyłącz shader | RetroArch: shader ekranu do końca gry — np. czarny ekran, bo shader nie pasuje do ustawień rdzenia (skala rozdzielczości) |
| Bez shadera w tym systemie | RetroArch: gry tego systemu bez shadera na stałe (własne ustawienia shadera RetroArcha zostają); przywrócenie: opcje gry → Ustawienia emulatora → Shader ekranu |
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
