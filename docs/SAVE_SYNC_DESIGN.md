# Save Sync — projekt server-authoritative

## Cel

Synchronizacja zapisów i stanów gier między urządzeniami tego samego konta EmuStart.

Klucz logiczny:

```text
account_id + device_id + platform_id + game_id + emulator_id + core_id + bundle_kind
```

## Zasady

- Serwer EmuStart jest jedynym źródłem prawdy o plikach i metadanych.
- Klient nie zna ścieżek serwera, NAS ani wewnętrznego układu katalogów.
- Klient otrzymuje wyłącznie krótkotrwałe, podpisane URL-e do pobrania lub wysłania bundle.
- Serwer waliduje `base_revision`, SHA-256, rozmiar oraz własność `device_id`.
- Memcardy nigdy nie są nadpisywane po cichu.
- Save states mogą mieć politykę „nowszy wygrywa”, ale zawsze z backupem przegranego pakietu.
- ROM-y, BIOS-y, emulatory i cache nie są częścią synchronizacji zapisów.

## Klucze synchronizacji

### RetroArch

Dla RetroArch rdzeń jest obowiązkową częścią klucza:

```text
account_id + device_id + platform_id + game_id + emulator_id + core_id + bundle_kind
```

Przykłady:

```text
snes / chrono_trigger / retroarch / snes9x / saves
snes / chrono_trigger / retroarch / snes9x / states
snes / chrono_trigger / retroarch / bsnes / saves
snes / chrono_trigger / retroarch / bsnes / states
```

Nie wolno mieszać danych Snes9x i bsnes.

### DuckStation

```text
account_id + device_id + platform_id + game_id + emulator_id + bundle_kind
```

Bundle:

- `memcards` — pliki `.mcd`
- `states` — stany DuckStation

### PCSX2 / ARMSX2 / NetherSX2

```text
account_id + device_id + platform_id + game_id + emulator_id + bundle_kind
```

Bundle:

- `memcards` — pliki `.ps2`
- `states` — pliki `.p2s`

Dla `.p2s` serwer przechowuje wersję formatu i nie scala automatycznie stanów między emulatorami.

## Manifest

Serwer przechowuje manifest per:

```text
account_id + platform_id + game_id
```

Przykład:

```json
{
  "schema_version": 3,
  "platform_id": "snes",
  "game_id": "chrono_trigger",
  "revision": 31,
  "updated_at": "2026-10-15T20:00:00Z",
  "updated_by_device_id": "dev_pc_hull",
  "emulators": {
    "retroarch": {
      "cores": {
        "snes9x": {
          "bundles": {
            "saves": {
              "bundle_id": "bnd_01H...",
              "revision": 31,
              "sha256": "...",
              "size": 20480,
              "file_count": 1,
              "download_url": "https://server/v1/files/dl/..."
            },
            "states": {
              "bundle_id": "bnd_01H...",
              "revision": 29,
              "sha256": "...",
              "size": 524288,
              "file_count": 4,
              "download_url": "https://server/v1/files/dl/..."
            }
          }
        }
      }
    }
  }
}
```

`download_url` i `upload_url` są:

- krótkotrwałe,
- podpisane,
- powiązane z `account_id`, `device_id` i konkretnym `bundle_id`,
- niewidoczne dla klienta jako ścieżka fizyczna,
- niewpisowane do logów w pełnej postaci.

## API

```text
GET  /v1/savesync/{platform_id}/{game_id}/manifest
POST /v1/savesync/upload/init
PUT  <temporary_upload_url>
POST /v1/savesync/upload/commit
GET  /v1/savesync/{platform_id}/{game_id}/status
POST /v1/savesync/{platform_id}/{game_id}/resolve
```

### Upload

```text
1. POST /v1/savesync/upload/init
2. Serwer zwraca upload_id, upload_url i expires_at
3. Klient wysyła ZIP na upload_url
4. POST /v1/savesync/upload/commit
5. Serwer waliduje SHA-256, rozmiar i base_revision
6. Serwer tworzy nowy revision lub zwraca 409 CONFLICT
```

Commit akceptuje upload tylko wtedy, gdy:

```text
base_revision == current_revision
```

## Lokalny sync state

```text
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
```

## Windows

Windows używa pełnego kontenera EmuStart:

```text
<EmuStart>/
    roms/{platform_id}/{game_id}/
    bios/{platform_id}/
    emulators/
    configs/
    saves/{account_id}/{platform_id}/{game_id}/{emulator_id}/{core_id}/
    states/{account_id}/{platform_id}/{game_id}/{emulator_id}/{core_id}/
    cache/
    sync/
```

## Android

### Core Edition — pełny kontener

EmuStart Android wbudowuje własny frontend libretro.

```text
context.filesDir/
    roms/{platform_id}/{game_id}/
    bios/{platform_id}/
    cores/{abi}/{core_id}_libretro.so
    configs/
    saves/{account_id}/{platform_id}/{game_id}/{emulator_id}/{core_id}/
    states/{account_id}/{platform_id}/{game_id}/{emulator_id}/{core_id}/
    cache/
    sync/
```

W tym trybie ROM, BIOS, rdzeń, config, zapisy i stany należą do EmuStart.

### External Emulator Mode

DuckStation, ARMSX2, NetherSX2 i RetroArch jako osobne aplikacje nie mogą być w pełni konteneryzowane.

Adaptery:

- `DIRECT_DIRECTORY` — emulator pozwala wskazać katalog poza `Android/data`
- `SAF_DIRECTORY` — emulator wymaga SAF i persistable URI
- `EXPORT_IMPORT` — emulator ma tylko eksport/import danych
- `UNSUPPORTED` — brak synchronizacji

Zabronione:

- bezpośredni zapis do `Android/data/<package innego emulatora>`
- `MANAGE_EXTERNAL_STORAGE`
- root
- automatyczna instalacja emulatorów jako część synchronizacji

## Konflikty

| Sytuacja | Działanie |
|---|---|
| Zmiana tylko lokalnie | Upload lokalnego bundle |
| Zmiana tylko zdalnie | Download zdalnego bundle |
| Zmiana po obu stronach | Konflikt i wybór użytkownika |
| Memcard konflikt | Zawsze decyzja użytkownika |
| Save state konflikt | Domyślnie nowszy, ale z backupem |
| Brak sieci | `local_dirty`, synchronizacja później |

## Etapy implementacji

1. Serwer: model savesync, manifest, upload/init, upload/commit, krótkotrwałe URL-e, revision, SHA-256, backupy.
2. Windows: lokalny sync state, pobieranie, rozpakowywanie, pakowanie, upload, integracja przed/po grze.
3. Android Core Edition: własny frontend libretro i prywatne katalogi.
4. Android External Emulator Mode: adaptery DIRECT_DIRECTORY, SAF_DIRECTORY, EXPORT_IMPORT, UNSUPPORTED.
5. UI statusu i konfliktów.
6. Testy.
