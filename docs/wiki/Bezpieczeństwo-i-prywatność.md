# Bezpieczeństwo i prywatność

## Co i gdzie jest zapisywane

| Dane | Gdzie | Ochrona |
|---|---|---|
| Klucze API (SteamGridDB, IGDB, TheGamesDB) | `config.json` | szyfrowane TPM, a bez TPM — DPAPI (konto Windows) |
| Hasło RetroAchievements | nigdzie | używane jednorazowo przy logowaniu |
| Token RetroAchievements | `retroachievements.json` profilu (lokalnie i na NAS-ie) | zwykły tekst — potrzebny na każdym komputerze |
| Ustawienia emulatorów profilu | `profiles\…\settings`, NAS | **bez haseł i tokenów** (puste w kopii, zostają w emulatorze) |
| Save'y, stany, historia gry | lokalnie i na NAS-ie | — |

Repozytorium i paczki z wydań nie zawierają żadnych danych użytkownika —
`config.json`, `data\`, `cache\`, `profiles\` i `logs\` są wykluczone.

## Sieć

EmuStart łączy się tylko z:

- serwerem miniatur libretro, buildbotem libretro, GitHubem i stronami emulatorów
  (grafiki, pobieranie emulatorów i rdzeni),
- gamesdb.launchbox-app.com (baza LaunchBox), Wikipedią/Wikidatą (opisy),
- SteamGridDB, IGDB, TheGamesDB (gdy są klucze),
- api.pouet.net i demozoo.org (dane dem Amigi),
- retroachievements.org (logowanie).

Wszystko przez HTTPS, z identyfikatorem programu w nagłówku User-Agent
(bez danych użytkownika). Sekrety idą w nagłówkach lub treści zapytań, nie w adresach.

## Lokalny serwer interfejsu

Interfejs jest stroną serwowaną z `127.0.0.1` na losowym porcie. Serwer:

- odpowiada tylko na adres `127.0.0.1`/`localhost` (ochrona przed DNS rebinding),
- udostępnia tylko pliki interfejsu, grafiki i zarejestrowane foldery tylko do odczytu,
- w trybie deweloperskim (`--browser`) wymaga własnego nagłówka do wywołań API.

Interfejs ma politykę CSP (tylko własne skrypty), a teksty z internetu (opisy,
tytuły) są zawsze escapowane.

## Logi

`logs\emustart.log` zawiera m.in. ścieżki gier i polecenia uruchomienia
emulatorów, nazwę konta RetroAchievements i nazwę komputera — nie zawiera haseł,
tokenów, kluczy ani tytułów okien innych programów.

## Zalecenia

- Folder `emustart` na NAS-ie powinien być zapisywalny tylko dla Twojego konta —
  kto może do niego pisać, może podmienić ustawienia emulatorów w profilach.
- Emulatory pobierane są z oficjalnych źródeł przez HTTPS, ale bez weryfikacji
  podpisu (większość projektów go nie publikuje).
