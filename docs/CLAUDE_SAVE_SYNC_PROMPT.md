# Prompt dla Claude Code — implementacja Save Sync

Wklej poniższy prompt do Claude Code:

```text
ZADANIE: Dodaj do EmuStart system synchronizacji zapisów gier między urządzeniami
Windows i Android.

ARCHITEKTURA JEST SERVER-AUTHORITATIVE:
- Osobny serwer EmuStart jest jedynym źródłem prawdy o plikach i metadanych.
- Klient NIE ZNA ścieżek serwera, NAS, profili ani wewnętrznego układu katalogów.
- Klient operuje wyłącznie na identyfikatorach:
  account_id, device_id, platform_id, game_id, emulator_id, core_id, bundle_id.
- Serwer zwraca krótkotrwałe, podpisane URL-e do pobrania lub wysłania plików.
- Klient nie może konstruować URL-i do plików samodzielnie.
- Wszystkie uprawnienia, walidacje, revision, konflikty i backupy obsługuje serwer.

KLUCZ SYNCHRONIZACJI:
account_id + device_id + platform_id + game_id + emulator_id + core_id + bundle_kind

DLA RETROARCH RDZEŃ JEST OBOWIĄZKOWY:
- saves i states są zawsze per emulator_id + core_id.
- Nie mieszaj danych Snes9x i bsnes.
- Nie kopiuj automatycznie danych między różnymi rdzeniami.

DLA DUCKSTATION:
- emulator_id=duckstation
- bundle_kind=memcards dla .mcd
- bundle_kind=states dla stanów DuckStation

DLA PCSX2 / ARMSX2 / NETHERSX2:
- emulator_id=pcsx2 / armsx2 / nethersx2
- bundle_kind=memcards dla .ps2
- bundle_kind=states dla .p2s
- serwer przechowuje wersję formatu .p2s
- nie scalaj automatycznie stanów między emulatorami

MANIFEST:
Serwer przechowuje manifest per account_id + platform_id + game_id.
Manifest zawiera revision, updated_at, updated_by_device_id oraz listę emulatorów,
rdzeni i bundle. Każdy bundle ma bundle_id, revision, sha256, size, file_count
oraz krótkotrwały download_url.

API:
GET  /v1/savesync/{platform_id}/{game_id}/manifest
POST /v1/savesync/upload/init
PUT  <temporary_upload_url>
POST /v1/savesync/upload/commit
GET  /v1/savesync/{platform_id}/{game_id}/status
POST /v1/savesync/{platform_id}/{game_id}/resolve

UPLOAD:
1. Klient wysyła POST /v1/savesync/upload/init
   z platform_id, game_id, emulator_id, core_id, bundle_kind,
   base_revision, sha256 i size.
2. Serwer zwraca upload_id, upload_url i expires_at.
3. Klient wysyła ZIP na upload_url.
4. Klient wysyła POST /v1/savesync/upload/commit
   z upload_id, base_revision, sha256 i size.
5. Serwer akceptuje commit tylko jeśli base_revision == current_revision.
6. W przeciwnym razie zwraca 409 CONFLICT.

LOKALNY SYNC STATE:
save_sync_state(
  account_id,
  device_id,
  platform_id,
  game_id,
  emulator_id,
  core_id,
  bundle_kind,
  bundle_id,
  synced_revision,
  synced_hash,
  local_dirty,
  conflict,
  updated_at
)

WYKRYWANIE ZMIAN:
- Nie używaj wyłącznie mtime ani rozmiaru.
- Licz SHA-256 strumieniowo.
- Dla RetroArch grupuj pliki po emulator_id + core_id.
- saves i states są osobnymi bundle.
- Nie pakuj całego katalogu emulatora.
- Nie pakuj configów, cache, shaderów ani tekstur.

FLOW PRZED GRĄ:
1. Ustal account_id, device_id, platform_id, game_id, emulator_id i core_id.
2. Pobierz manifest.
3. Porównaj synced_revision, synced_hash i manifest revision.
4. Jeśli lokalny pakiet jest niezmieniony, a zdalny nowszy — pobierz.
5. Jeśli lokalny jest zmieniony, a zdalny nie — wyślij.
6. Jeśli oba zmienione — oznacz konflikt.
7. Memcardy wymagają decyzji użytkownika.
8. Save states mogą mieć domyślnie „nowszy wygrywa”, ale zawsze z backupem.

FLOW PO GRZE:
1. Zeskanuj pliki tej gry.
2. Oblicz SHA-256.
3. Jeśli dane się zmieniły, spakuj bundle.
4. Wyślij przez upload/init, upload URL i upload/commit.
5. Przy 409 CONFLICT pobierz manifest i pokaż konflikt.

BEZPIECZEŃSTWO:
- Pliki pobieraj i wysyłaj najpierw jako .tmp.
- Po weryfikacji SHA-256 wykonuj atomową podmianę.
- Przed nadpisaniem twórz backup.
- Backup po stronie serwera również jest wymagany.
- SHA-256 licz strumieniowo.
- Dodaj timeout i retry z backoff.
- Upload i download URL muszą być krótkotrwałe.
- Nie loguj pełnych URL-i z tokenami.

WINDOWS:
- Pełny kontener EmuStart.
- Lokalne katalogi:
  roms/{platform_id}/{game_id}/
  bios/{platform_id}/
  emulators/
  configs/
  saves/{account_id}/{platform_id}/{game_id}/{emulator_id}/{core_id}/
  states/{account_id}/{platform_id}/{game_id}/{emulator_id}/{core_id}/
  cache/
  sync/
- Synchronizuj przed startem gry i po jej zamknięciu.
- Jeśli aplikacja zostanie zamknięta podczas gry, oznacz local_dirty
  i zsynchronizuj przy następnym starcie.

ANDROID — CORE EDITION:
- Wbudowany frontend libretro.
- Rdzenie .so pobierane z serwera dla właściwego ABI.
- Wszystko w context.filesDir:
  roms/, bios/, cores/, configs/, saves/, states/, cache/, sync/.
- Pełna konteneryzacja: ROM, BIOS, rdzeń, config, zapisy i stany należą do EmuStart.

ANDROID — EXTERNAL EMULATOR MODE:
- DuckStation, ARMSX2, NetherSX2 i RetroArch jako osobne aplikacje
  nie mogą być w pełni konteneryzowane.
- Nie czytaj i nie zapisuj bezpośrednio do Android/data innego emulatora.
- Nie wymagaj MANAGE_EXTERNAL_STORAGE ani roota.
- Użyj adapterów:
  DIRECT_DIRECTORY,
  SAF_DIRECTORY,
  EXPORT_IMPORT,
  UNSUPPORTED.
- Dla SAF użyj takePersistableUriPermission().
- Przed każdą grą sprawdzaj ważność URI.
- Dodaj ekran „Integracja emulatora” z testem dostępu.

ANDROID — SYNCHRONIZACJA W TLE:
- Nie wykonuj synchronizacji wyłącznie w onDestroy().
- Synchronizuj przed grą i po powrocie z emulatora.
- WorkManager jako mechanizm awaryjny dla local_dirty.
- OneTimeWorkRequest, NetworkType.CONNECTED, retry/backoff,
  unique work per account_id + platform_id + game_id + emulator_id + core_id + bundle_kind.

UI:
- „Zsynchronizowano”
- „Synchronizacja…”
- „Dane lokalne gotowe do wysłania”
- „Nowe dane z innego urządzenia”
- „Konflikt”
- „Offline”
- „Emulator nieobsługiwany”
- „Wymaga konfiguracji katalogu zapisów”
- „Wymaga instalacji emulatora”

KONFLIKT:
- Pokaż grę, emulator, rdzeń, typ bundle, czas i urządzenie zdalnej zmiany.
- Przyciski:
  - „Użyj zapisu z tego urządzenia”
  - „Użyj zapisu z serwera”
  - „Pomiń synchronizację tej gry”
- Przed nadpisaniem zawsze twórz backup.

TESTY:
- zmiana tylko lokalnie,
- zmiana tylko na serwerze,
- zmiany po obu stronach,
- upload ze starym base_revision,
- uszkodzony ZIP lub zły SHA-256,
- przerwana synchronizacja,
- brak sieci,
- dwa urządzenia tego samego konta,
- wygaśnięcie upload/download URL,
- RetroArch Snes9x vs bsnes,
- DuckStation memcard per gra i współdzielony,
- PCSX2/ARMSX2 memcard i .p2s,
- Android offline, restart urządzenia, przerwanie aplikacji,
- test SAF i DIRECT_DIRECTORY.

PLAN IMPLEMENTACJI:
1. Serwer: model savesync, manifest, upload/init, upload/commit,
   krótkotrwałe URL-e, revision, SHA-256, backupy.
2. Klient Windows: sync state, pobieranie, rozpakowywanie, pakowanie, upload,
   integracja przed/po grze, UI.
3. Android Core Edition: frontend libretro, rdzenie .so, prywatne katalogi.
4. Android External Emulator Mode: adaptery i UI konfiguracji.
5. Testy.

Najpierw zaimplementuj:
- RetroArch saves per rdzeń,
- RetroArch states,
- DuckStation memcards,
- PCSX2/ARMSX2 memcards.

Dopiero potem:
- automatyczną synchronizację .p2s,
- eksport/import dla emulatorów bez dostępu do katalogów,
- pełny frontend libretro na Androidzie.

Nie implementuj wszystkiego naraz.
Najpierw przedstaw plan zmian, listę nowych plików, zmiany w istniejących modułach
oraz schemat API. Potem czekaj na moją akceptację.
```
