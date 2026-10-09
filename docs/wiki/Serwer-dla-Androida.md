# Serwer dla Androida

EmuStart może działać na komputerze z grami (NAS) jako **serwer** dla aplikacji
EmuStart na Androida (w przygotowaniu). Serwer udostępnia przez sieć (Tailscale):

- listę systemów i gier z metadanymi, opisami, okładkami, zrzutami i logo — telefon
  nic nie skanuje ani nie pobiera z baz grafik,
- pliki gier we fragmentach (pobieranie kilkoma strumieniami naraz, wznawianie),
- później także zapisy i ustawienia profili.

To ten sam program (`EmuStart.exe`), uruchamiany bez okna z parametrem `--server`.

## Instalacja na komputerze z grami

1. Skopiuj EmuStart (rozpakowane wydanie) na komputer z grami, np. `C:\EmuStart`.
   Jeśli zip był pobrany z internetu, odblokuj pliki (inaczej okno EmuStart się nie
   uruchomi — błąd „Failed to resolve Python.Runtime.Loader.Initialize”):
   ```
   Get-ChildItem -Recurse 'C:\EmuStart' | Unblock-File
   ```
   (albo przed rozpakowaniem: Właściwości zipa → „Odblokuj”). Gdy okno mimo to się nie
   uruchomi, EmuStart otworzy interfejs w przeglądarce — ustawienia działają tak samo.
2. Uruchom raz zwykłe `EmuStart.exe`: w Ustawieniach wskaż **foldery z grami tak,
   jak widzi je ten komputer** (np. `D:\ROMS\No-Intro`), zeskanuj kolekcję i w Grafikach
   pobierz bazę LaunchBox, grafiki i opisy. Zamknij program.
3. Uruchom **jako administrator** wiersz poleceń w folderze EmuStart i wpisz:
   ```
   EmuStart.exe --install-server
   ```
   Dodaje to zadanie w Harmonogramie zadań („EmuStart Server”, start razem z Windows)
   i regułę zapory dla portu 8740.
4. Uruchom serwer od razu (albo zrestartuj komputer):
   ```
   schtasks /Run /TN "EmuStart Server"
   ```
5. Klucz serwera (potrzebny w aplikacji na Androida):
   ```
   EmuStart.exe --server-key
   ```

Adres serwera dla telefonu: `http://<adres Tailscale komputera>:8740`.

## Administracja

Na komputerze z serwerem pełny interfejs EmuStart jest dostępny w przeglądarce:
`http://127.0.0.1:8740/index.html?dev` (ustawienia, skanowanie, grafiki). Z innych
komputerów interfejs nie jest dostępny — tylko API z kluczem.

Nie uruchamiaj jednocześnie serwera i zwykłego EmuStart z tego samego folderu.

## API (dla aplikacji)

Każde zapytanie z kluczem: nagłówek `Authorization: Bearer <klucz>` albo `?k=<klucz>`.

| Adres | Zawartość |
|---|---|
| `GET /v1/info` | nazwa komputera, wersja |
| `GET /v1/systems` | systemy (`es`, `display`, `games`, `logo`, `kind`) |
| `GET /v1/systems/<es>/games` | gry systemu (tytuł, tagi, rozmiar, gatunek, rok, gracze, producent, adresy grafik) |
| `GET /v1/games/<id>` | szczegóły gry, opis, lista plików |
| `GET /v1/games/<id>/file/<ścieżka>` | plik gry, obsługa `Range` |
| `GET /media/...` | grafiki |

## Bezpieczeństwo

- Klucz serwera jest losowy (`config.json` → `server_token`), porównywany w stałym czasie.
- Serwer wydaje wyłącznie pliki gier z biblioteki (ścieżka spoza gry → 404).
- Połączenie przez Tailscale jest szyfrowane; nie wystawiaj portu 8740 do internetu
  bez Tailscale.
