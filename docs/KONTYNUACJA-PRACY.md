# Procedura kontynuacji dłuższej pracy (sesje z agentem)

Dotyczy pracy prowadzonej etapami przez kilka sesji, np. „Plan: zapisy rdzeni RetroArcha w `system\` i w podfolderach
`saves\`” w `CHANGELOG.md` → `[Nieopublikowane]`.

**Jedno źródło prawdy:** plan, stan etapów, kryteria akceptacji, wyniki testów, commity i otwarte ryzyka są w
`CHANGELOG.md` → `[Nieopublikowane]` (sekcja planu i jej tabela „Stan etapów”). Ten plik opisuje tylko, *jak* pracować.

## Na starcie sesji (także po kompakcji kontekstu)
1. Przeczytaj `CLAUDE.md`, plan i tabelę „Stan etapów” w `CHANGELOG.md`.
2. `git status`, aktualna gałąź, ostatnie commity — porównaj z tabelą. Niezacommitowanych zmian użytkownika nie
   nadpisuj, nie resetuj repozytorium, nie zmieniaj gałęzi bez potrzeby.
3. `python -m pytest -q` — stan wyjściowy. Poprzednie podsumowanie to punkt startowy, nie dowód.
4. Wznów od wiersza tabeli z „następnym krokiem”.

## Cykl jednego etapu
a) Sprawdź kod, konfigurację i dokumentację / źródła emulatora; oddziel potwierdzone zachowanie od założeń.
b) Zakres i kryteria akceptacji — w tabeli.
c) Test odtwarzający brakującą funkcję (najpierw czerwony).
d) Minimalna implementacja, bez niezwiązanych refaktoryzacji.
e) Testy celowane, potem cały zestaw.
f) Przegląd diffu (błędy, bezpieczeństwo zapisów); niezależny przegląd, gdy zasadny.
g) Poprawki, testy ponownie.
h) Aktualizacja tabeli i dokumentacji.
i) Lokalny commit tylko z tym etapem.
j) Następny etap — bez pytania „dalej?”.

## Bezpieczeństwo zapisów
- Nigdy nie testuj na prawdziwych zapisach — kopie, fixture'y, katalogi tymczasowe.
- Izolacja gier i graczy, konflikty, brak katalogów, podfoldery, sortowanie zapisów w RetroArchu.
- Wspólnej karty / katalogu wielu gier nie synchronizuj jako zapisu jednej gry — tylko z bezpieczną strategią
  (np. opcja rdzenia dająca osobny plik na grę).
- Zgodność formatów między emulatorami tylko z dowodem (dokumentacja, źródła).
- Dwa poziomy weryfikacji, zawsze rozdzielone w tabeli: **testy (fixture'y)** i **na żywo (prawdziwy emulator)**.
  Etap bez sprawdzenia na żywo ma status „czeka na sprawdzenie na żywo”, nie „zrobione”.

## Granice
- Wolno: implementacja, testy, dokumentacja, lokalne commity na gałęzi roboczej, lokalny build i checklista wydania.
- Tylko za osobną zgodą: push, merge do `main`, publikacja wydania, wdrożenie na serwer, nowe zależności globalne.

## Checkpoint
Przed kompakcją kontekstu albo końcem sesji: zaktualizuj w tabeli „Stan etapów” co zakończono, nad czym trwa praca
i dokładny następny krok.
