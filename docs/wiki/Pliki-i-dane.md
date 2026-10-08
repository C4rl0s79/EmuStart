# Pliki i dane

## Obok programu (lokalnie)

| Ścieżka | Zawartość |
|---|---|
| `config.json` | ustawienia komputera: foldery, emulator per system, wygląd, klucze API (zaszyfrowane) |
| `data\library.sqlite` | biblioteka gier, historia gry, stan pamięci podręcznej, metadane |
| `data\launchbox.db` | baza LaunchBox (po pobraniu) |
| `data\media\<system>\` | okładki, zrzuty, logo (WebP) |
| `data\resume\<profil>\` | stany „wznowienia” |
| `cache\<system>\` | lokalne kopie gier |
| `profiles\<id>\` | save'y, kopie ustawień, `emustart.json`, `retroachievements.json` profilu |
| `profiles\_machine\` | ostatnie ustawienia emulatorów tego komputera sprzed podmiany na profil |
| `logs\emustart.log` | log (rotowany, 4 × 2 MB) |

## Na NAS-ie

`Z:\emustart\Profiles\<profil>\` — patrz
[Kilka komputerów, jeden NAS](Kilka-komputerów-jeden-NAS). Folder NAS-a zmienia
ustawienie `profiles_nas` w `config.json`.

## W emulatorach

- Foldery save'ów są dowiązaniami (junction) do `profiles\<id>\…` — usunięcie
  dowiązania nie usuwa save'ów.
- Na czas gry EmuStart zmienia w ustawieniach emulatora kolejność padów (wraca po
  grze) i wpisuje konto RetroAchievements profilu.

## Pliki tymczasowe

`%TEMP%\emustart\run\` — wypakowane gry i konfiguracja sesji RetroArcha;
usuwane po grze (po awarii — przy następnym starcie).

## Kopia zapasowa

Wystarczy skopiować `config.json`, `data\library.sqlite` i `profiles\`. Grafiki
i bazę LaunchBox da się pobrać ponownie; save'y i ustawienia profili są też na NAS-ie.
