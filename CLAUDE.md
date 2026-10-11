# Zasady pracy nad EmuStart

## Zakres zmian
- Zmieniaj tylko to, o co prosi użytkownik. Bez „przy okazji”: nie ruszaj wyglądu listy
  gier, sterowania, menu ani zachowania, którego prośba nie dotyczy.
- Przed kodem wpisz plan do `CHANGELOG.md` → `[Nieopublikowane]`: co się zmieni i czego
  zmiana NIE dotyka. Potem trzymaj się tego wpisu; wszystko poza nim — najpierw zapytaj.
- Gdy rozwiązanie wymaga zmiany widocznej poza prośbą (np. nowe pozycje na liście gier),
  zapytaj przed zrobieniem, z konkretnym przykładem, jak to będzie wyglądać.
- Ustalenia z użytkownikiem są wiążące — przed zmianą sprawdź je w CHANGELOG i rozmowie.

## Sprawdzanie przed PR
- `python -m pytest -q` — porównaj liczbę niepowodzeń z `main` (na Linuksie część testów
  jest tylko pod Windows).
- Zmiana w `web/`: uruchom UI (`python main.py --browser`, Chromium/Playwright) i przejdź
  klawiaturą: profile → systemy → lista gier → opcje gry (przytrzymane Enter) → menu (Tab).
  Sprawdź zrzut ekranu listy gier (czytelność tytułów).
- Nie zgłaszaj „naprawione”, jeśli nie da się tego sprawdzić tutaj (okno Windows, WebView2,
  pad) — napisz wprost, co sprawdzone, a co nie.

## Wydania
- Wersja w `emustart/__init__.py` (APK bierze ją stąd). Każdy PR z wydaniem: wersja +
  wpis w CHANGELOG.
- Tag `vX.Y.Z` użytkownik wypycha sam, dopiero gdy PR jest scalony do `main`.
