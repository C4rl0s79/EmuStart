# RetroAchievements

Każdy profil może mieć własne konto [RetroAchievements](https://retroachievements.org).
EmuStart przed każdą grą wpisuje konto profilu do emulatora — **RetroArch,
DuckStation, PCSX2**. Profil bez konta ma osiągnięcia wyłączone.

## Logowanie

Start → Zmień profil → na karcie profilu **Start** → **Zaloguj do RetroAchievements**:
nazwa użytkownika, potem hasło (klawiatura ekranowa ukrywa hasło).

- Hasło trafia tylko do retroachievements.org (zapytanie POST przez HTTPS)
  i **nie jest nigdzie zapisywane**. Zapisywany jest token — tak samo robią to
  same emulatory.
- **Użyj konta z emulatora** — gdy w RetroArch/DuckStation/PCSX2 jest już
  zalogowane konto, można je przypisać do profilu bez ponownego logowania.
  Konto zastane w emulatorach na komputerze dostaje automatycznie profil tego
  komputera.
- **Tryb hardcore** — osiągnięcia hardcore; emulator blokuje wtedy stany zapisu
  (menu w grze: zapis/wczytanie stanu i „quicksave i wyjdź” nie działają).
- **Wyloguj** — profil bez konta.

## Jak to wygląda w emulatorach

| Emulator | Gdzie EmuStart wpisuje konto |
|---|---|
| RetroArch | konfiguracja sesji (`--appendconfig`), Twój `retroarch.cfg` bez zmian |
| DuckStation | `settings.ini` [Cheevos] — token zaszyfrowany tak jak robi to DuckStation |
| PCSX2 | `inis\PCSX2.ini` [Achievements] + token w `inis\secrets.ini` |

DuckStation szyfruje token kluczem z nazwy użytkownika (a poza trybem przenośnym
także z identyfikatora komputera) — EmuStart zapisuje go w tym samym formacie.
Jeśli DuckStation mimo to poprosi o logowanie, zaloguj się w nim raz i zgłoś
problem.

## Gdzie jest przechowywane

`retroachievements.json` w profilu (lokalnie i na NAS-ie) — nazwa i token, bez
hasła. Token pozwala korzystać z konta RA (np. zdobywać osiągnięcia), nie zmienić
hasła. Patrz [Bezpieczeństwo i prywatność](Bezpieczeństwo-i-prywatność).
