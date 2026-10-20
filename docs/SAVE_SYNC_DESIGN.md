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

1. **Serwer** — API, wersje, konflikty, historia, podpisane adresy (gotowe: `emustart/savesync.py`).
2. **Klient Windows** — lokalny stan synchronizacji, pakowanie / pobieranie, przed i po grze;
   RetroArch saves i states per rdzeń, DuckStation memcards, PCSX2 memcards per gra; status
   gry i okno konfliktu; zastępuje dotychczasową synchronizację zapisów profilu i `rastates`.
3. **Android (zewnętrzne emulatory)** — adaptery DIRECT_DIRECTORY / SAF_DIRECTORY /
   EXPORT_IMPORT / UNSUPPORTED, ekran „Integracja emulatora” z testem dostępu, WorkManager.
4. Później: stany PCSX2 (.p2s) ze zgodnością wersji, eksport / import.
5. Osobny projekt: Android z własnym frontendem libretro.
