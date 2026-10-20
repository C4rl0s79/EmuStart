# Synchronizacja zapisów gier — projekt (server-authoritative)

Serwer EmuStart jest jedynym źródłem prawdy o zapisach, stanach i kartach pamięci.
Klient zna wyłącznie identyfikatory i dostaje krótkotrwałe, podpisane adresy plików.

## Ustalenia (2026-10-20)

1. **Jedna prawda, jak Steam Cloud.** Każda paczka ma jedną aktualną wersję (revision).
   Rozbieżne zmiany z dwóch urządzeń (np. gra bez internetu) to konflikt — rozstrzyga gracz
   („z tego urządzenia” / „z serwera” / „pomiń”); przegrana wersja zostaje w historii na
   serwerze (kopia zapasowa). Dotyczy też stanów — bez automatycznego „nowszy wygrywa”.
2. **PCSX2 / ARMSX2 / NetherSX2: karta pamięci osobna dla każdej gry** (wspólna karta
   wszystkich gier dawałaby konflikt przy każdej zmianie). Przy pierwszej grze karta gry
   powstaje z dotychczasowej wspólnej (zawiera też zapisy tej gry).
3. **Android bez `MANAGE_EXTERNAL_STORAGE`** — dostęp do folderów emulatorów przez SAF
   (jednorazowy wybór folderu, trwałe uprawnienie).
4. Poprzednie próby (`feature/save-sync`, lokalna `split/save`) usunięte — miały konto z treści
   zapytania (dostęp do cudzych zapisów), lokalny numer gry jako `game_id` i paczki ze
   wszystkich plików zmienionych w sesji.

## Klucz paczki

`konto + platform_id + game_id + emulator_id + core_id + bundle_kind`

- **konto i urządzenie** — z tokenu sesji urządzenia (nie z treści zapytania). Inne konto
  niż konto urządzenia tylko z tokenem tego konta (`X-Profile-Token`, gracz zalogowany hasłem
  na cudzym urządzeniu).
- **platform_id** — kod systemu (`snes`, `psx`, `ps2`…).
- **game_id** — stały, taki sam na każdym urządzeniu: `savesync.game_id(system, nazwa gry)`;
  serwer podaje go w listach gier (`/v1/library`, `/v1/systems/<es>/games`).
- **core_id** — tylko RetroArch (obowiązkowy): dane Snes9x i bsnes są całkowicie osobne,
  nic nie kopiuje się automatycznie między rdzeniami.
- **bundle_kind** — `saves` (RetroArch: .srm, .sav, .rtc…), `states` (.state, .stateN,
  .state.auto + miniatury, DuckStation, .p2s), `memcards` (DuckStation .mcd, PCSX2 .ps2).

Paczka to **pełny zestaw plików** gry dla danego emulatora / rdzenia / rodzaju — nie pliki
zmienione w sesji. Zmiany wykrywa SHA-256 zestawu (nie czas modyfikacji).

## Zapisy wspólne dla zgodnych rdzeni (0.47)

`savebundles.SAVE_GROUPS` — jawna lista rdzeni o tym samym formacie zapisu w grze (surowy
SRAM): `sram-snes` (Snes9x, bsnes, Mesen-S…), `sram-nes` (FCEUmm, Nestopia, Mesen, QuickNES).
Rodzaj `saves` tych rdzeni ma `core_id` grupy; stany zostają per rdzeń. Serwer mapuje rdzeń z
grupy na grupę także dla klientów 0.46 i pokazuje im paczkę grupy pod ich rdzeniem.

## Migracja zapisów sprzed 0.46 (0.47)

`emustart/savemigrate.py` — z panelu serwera, ręcznie; foldery profili → paczki. Istniejąca
paczka nie jest nadpisywana: stara wersja idzie do historii (`imported`, `note`), przywracana
przez `savesync.restore`. Idempotentna (porównanie zawartości), stare foldery zostają.

## API

| Metoda i adres | Treść | Odpowiedź |
|---|---|---|
| `GET /v1/savesync/{platform}/{game}/manifest` | — | manifest z `download_url` (10 min) |
| `GET /v1/savesync/{platform}/{game}/status` | — | manifest bez adresów |
| `POST /v1/savesync/upload/init` | `platform_id, game_id, emulator_id, core_id, bundle_kind, base_revision, sha256, size` | `upload_id, upload_url` (30 min) albo **409** `{current}` |
| `PUT <upload_url>` | ZIP | zapis strumieniowy do `.part`, SHA-256, rozmiar, poprawność ZIP-a |
| `POST /v1/savesync/upload/commit` | `upload_id, base_revision, sha256, size` | `revision, bundle_id` albo **409** `{current}` |
| `POST /v1/savesync/{platform}/{game}/resolve` | `choice: local\|remote\|skip`, `upload_id` | `local` — wersja z urządzenia zostaje, serwerowa do historii |
| `GET <download_url>` | — | ZIP (Range) |

Adresy plików: `/v1/files/u/<token>`, `/v1/files/d/<token>` — token podpisany HMAC (sekret
serwera), związany z kontem, urządzeniem, paczką / wysyłką i rodzajem operacji, z terminem
ważności. Nie zawiera ścieżek serwera. W logach tylko identyfikatory (konto, urządzenie,
platforma, gra, emulator, rdzeń, rodzaj, revision, SHA-256, rozmiar, `upload_id`).

Commit tylko przy `base_revision == current_revision`, manifest zmieniany atomowo pod
blokadą gry. Poprzednia wersja paczki zostaje w historii (ostatnie 10).

## Etapy

1. **Serwer** — API, wersje, konflikty, historia, podpisane adresy (`emustart/savesync.py`). Gotowe (0.46.0).
2. **Klient Windows** (`emustart/syncclient.py`, `savebundles.py`) — stan lokalny w bazie,
   pobieranie / wysyłanie przed i po grze, okno konfliktu, status w opcjach gry. RetroArch
   saves i states per rdzeń, DuckStation i PCSX2 — karta pamięci gry. Foldery zapisów
   profilu w ustawieniach emulatora na czas gry (bez junction). Zastępuje synchronizację
   tych folderów profilu i `rastates`. Gotowe (0.46.0).
3. **Android** (`SaveSync.kt`, `Saves.kt`) — te same paczki: RetroArch per rdzeń, PS1 w
   RetroArchu (`.srm` = karta DuckStation z PC), ArmSX2 / AetherSX2 (`Mcd001.ps2` = karta
   gry z PC, karty innych gier w schowku aplikacji), okno konfliktu, „Wczytaj zapis” z
   paczki. Gotowe (0.46.0) — z dotychczasowym dostępem do plików
   (`MANAGE_EXTERNAL_STORAGE`).
4. **Android bez `MANAGE_EXTERNAL_STORAGE`** (SAF: jednorazowy wybór folderu emulatora,
   ekran „Integracja emulatora” z testem dostępu, WorkManager do wysyłania zaległych
   zmian). Uprawnienie służy dziś też grom w publicznym folderze (emulatory muszą je
   czytać), BIOS-om i ustawieniom RetroArcha — zmiana dotyka uruchamiania gier, wymaga
   testów na telefonie.
5. Później: stany PCSX2 / ARMSX2 (.p2s) paczkami ze zgodnością wersji formatu, Dolphin,
   PPSSPP, RPCS3 (dziś: synchronizacja folderów profilu, junction), eksport / import.
6. Osobny projekt: Android z własnym frontendem libretro.
