# Bezpieczeństwo i prywatność

## Co i gdzie jest zapisywane

| Dane | Gdzie | Ochrona |
|---|---|---|
| Klucze API (SteamGridDB, IGDB, TheGamesDB) | `config.json` | szyfrowane TPM, a bez TPM — DPAPI (konto Windows) |
| Hasło RetroAchievements | nigdzie | używane jednorazowo przy logowaniu |
| Token RetroAchievements | `retroachievements.json` profilu (lokalnie i na NAS-ie) | zwykły tekst — potrzebny na każdym komputerze |
| Ustawienia emulatorów profilu | `profiles\…\settings`, NAS | **bez haseł i tokenów** (puste w kopii, zostają w emulatorze) |
| Save'y, stany, historia gry | lokalnie i na NAS-ie | — |
| Klucz urządzenia (logowanie do serwera) | TPM / DPAPI (PC), Android Keystore | klucz prywatny nie opuszcza urządzenia |
| Hasło konta | na serwerze tylko skrót (`accounts.json`) | scrypt z losową solą, poza folderem profili |
| PIN profilu | na urządzeniu (baza programu / ustawienia aplikacji) | skrót scrypt / PBKDF2; serwer go nie zna |

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

- interfejs, API programu i panel serwera obsługuje tylko dla połączeń z tego komputera —
  decyduje adres połączenia, nie nagłówek `Host` (0.41: w trybie serwera urządzenie z sieci
  mogło go podrobić); do tego nagłówek `Host` musi być `127.0.0.1`/`localhost` (DNS rebinding),
- udostępnia tylko pliki interfejsu, grafiki i zarejestrowane foldery tylko do odczytu,
- w trybie deweloperskim (`--browser`) wymaga własnego nagłówka do wywołań API.

Interfejs ma politykę CSP (tylko własne skrypty), a teksty z internetu (opisy,
tytuły) są zawsze escapowane.

## Logi

`logs\emustart.log` zawiera m.in. ścieżki gier i polecenia uruchomienia
emulatorów, nazwę konta RetroAchievements i nazwę komputera — nie zawiera haseł,
tokenów, kluczy ani tytułów okien innych programów.

## Serwer EmuStart (audyt 0.41, logowanie 0.43)

- **Urządzenia:** jedyna droga to logowanie kontem i hasłem z kluczem urządzenia
  (zatwierdzone na urządzeniu konta, przez administratora albo kodem QR z panelu); potem
  logowanie podpisem (token sesji 1 h, tylko w nagłówku). Parowanie kodem i dołączanie z
  sieci lokalnej (dawały administratora bez konta) usunięte w 0.43, a ich rejestracje
  unieważnione. Wspólny klucz serwera — wyłącznie na komputerze serwera.
- **Tylko szyfrowane połączenia** (0.43): urządzenia łączą się portem HTTPS z przypiętym
  certyfikatem, portem publicznym albo tunelem; zwykły port obsługuje API tylko z samego
  serwera. Kod QR z panelu niesie odcisk certyfikatu — pierwsze połączenie też jest przypięte.
- **Role:** administrator to cecha konta (panel → Konta); zwykłe konto zatwierdza tylko
  nowe urządzenia swojego konta.
- **Konta:** hasło (scrypt, opóźnienia, limit prób z adresu, ograniczona liczba sprawdzeń
  naraz); inne konto na urządzeniu — tylko z tokenem konta po haśle.
- **Ścieżki:** folder profili odrzuca nazwy, które Windows utożsamia z innymi (kropka lub
  spacja na końcu, nazwy 8.3 typu `JEZUS~1`, `CON`/`NUL`…) — inaczej ominęłyby sprawdzanie
  konta.
- **Certyfikat:** urządzenia przypinają odcisk certyfikatu serwera; odpowiedź po zwykłym
  porcie nie zmienia już zapamiętanego odcisku ani adresów.
- **Identyfikator sprzętu** z nagłówka tylko się zapisuje — nie unieważnia innych rejestracji.
- **Aktualizacje:** tylko z wydań repozytorium (adres), ze zgodnym skrótem SHA-256 podanym
  przez GitHub i (od 0.42) z **podpisem wydania** Ed25519 (`<zip>.sig`). Klucz prywatny jest
  tylko w sekrecie repozytorium i u właściciela, publiczny — wbudowany w program. Podmienione
  wydanie (np. po przejęciu konta GitHub) bez klucza prywatnego nie przejdzie; wydanie bez
  podpisu też nie. APK chroni podpis Androida (aktualizacja tylko tym samym kluczem).
- **Android:** adres interfejsu `emustart.local` nigdy nie idzie do sieci.

## Zalecenia

- **Uprawnienia katalogu serwera.** Serwer działa jako SYSTEM. Jeśli zwykli użytkownicy
  Windows mogą modyfikować jego katalog (domyślnie na dyskach innych niż C: — „Użytkownicy
  uwierzytelnieni: modyfikacja”), każdy z nich może podmienić `EmuStart.exe` (uruchomi się
  jako SYSTEM) albo odczytać sekrety serwera. Zawężenie (wiersz poleceń jako administrator):

  ```
  icacls "E:\EmuStart" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX"
  icacls "E:\EmuStart\logs" /grant "*S-1-5-32-545:(OI)(CI)M"
  ```

  Cofnięcie: `icacls "E:\EmuStart" /reset /T`.

- Folder `emustart` na NAS-ie powinien być zapisywalny tylko dla Twojego konta —
  kto może do niego pisać, może podmienić ustawienia emulatorów w profilach.
- Emulatory pobierane są z oficjalnych źródeł przez HTTPS, ale bez weryfikacji
  podpisu (większość projektów go nie publikuje).
