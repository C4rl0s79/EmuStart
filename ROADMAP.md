# Plan rozwoju

Kierunek: **serwer EmuStart jako centrum** — gry, zapisy, profile, ustawienia i biblioteka
idą przez serwer (HTTP z kluczem, przez Tailscale), wspólnie dla Windows i Androida.
Dostęp SMB do NAS-a zostaje jako zapas i do zarządzania.

Stan na 2026-10-09 (EmuStart 0.25.0). Kolejność może się zmienić; to, co już wydane,
opisuje [CHANGELOG.md](CHANGELOG.md).

## Dlaczego serwer (pomiary)

Tailscale, bezpośrednie połączenie (nie przez przekaźnik), ok. 1000 km, RTT 33 ms;
łącze przy NAS-ie: symetryczny światłowód 1 Gb/s. Odczyt 384 MB z gry PS2:

| | SMB (dysk Z:) | Serwer 0.22.4 (HTTP/1.0) | Serwer 0.22.5 (HTTP/1.1) |
|---|---|---|---|
| 1 strumień, blok 4 MB | 18,5 MB/s | 8,7 MB/s | 17,6 MB/s |
| 4 strumienie, blok 1 MB | 22,3 MB/s | 12,4 MB/s | 20,7 MB/s |
| 8 strumieni, blok 1 MB | 18,1 MB/s | 23,6 MB/s | 54,2 MB/s |
| 8 strumieni, blok 4 MB | 27,4 MB/s | 52,4 MB/s | **78,5 MB/s** |

- SMB nie rośnie z liczbą strumieni — Windows łączy się z jednym serwerem SMB jednym
  połączeniem TCP. HTTP: osobne połączenie na strumień.
- Listowanie dużego folderu (PS2) przez SMB: ponad 2 minuty. Małe operacje
  (zapisy, blokady, grafiki) kosztują po kilka wymian po 33 ms — serwer robi je jednym zapytaniem.

Narzędzie pomiaru: `tools/httpbench.py`.

## Etap A — gry na Windows przez serwer *(0.23.0 — do sprawdzenia na prawdziwym serwerze)*

- [x] Ustawienia Windows: adres i klucz serwera (podpowiedź adresu z udziału NAS-a,
      klucz wklejany ze schowka — `--server-key` na serwerze kopiuje go do schowka).
- [x] Serwer: plik gry po (system, ścieżka względna, rozmiar) — tylko pliki z biblioteki
      serwera; `/v1/info` podaje obsługiwane funkcje.
- [x] Pobieranie i granie w trakcie pobierania (WinFsp) przez HTTP: 8 strumieni,
      kolejne bloki łączone w zapytania do 4 MB, bloki na żądanie gry — 1 MB.
- [x] Zapas: serwer nie odpowiada, nie ma pliku albo jest starszy — SMB jak dotąd.
- [x] Postęp pobierania pokazuje źródło (serwer / NAS).

## Etap B — profile, zapisy i ustawienia przez serwer (Windows + Android)

Układ profilu na NAS-ie:

```
<Profil>\save\<emulator>\...           wspólne dla Windows i Androida
<Profil>\states\windows|android\...    stany gry osobno (inne wersje emulatorów)
<Profil>\settings\windows\<emulator>\  ustawienia emulatorów na PC
<Profil>\settings\android\<emulator>\  ustawienia emulatorów na telefonie
<Profil>\_backup\                      kopie nadpisanych plików, konflikty
```

- [ ] Migracja: obecne `settings\<emulator>` → `settings\windows\`.
- [ ] API zapisów: lista ze skrótami (co się zmieniło od wersji X), pobierz, wyślij —
      porównanie zawartości (skróty), nie dat (zegary urządzeń się różnią).
- [ ] Konflikty: plik zmieniony po obu stronach — zostaje nowszy, drugi do
      `_backup\konflikty\` z nazwą urządzenia; komunikat i „przywróć drugą wersję”.
- [ ] **Blokada profilu (dzierżawa)**: urządzenie w grze odzywa się co minutę; po awarii
      blokada wygasa sama; „przejmij profil” z potwierdzeniem.
- [ ] **PIN profilu**: na serwerze tylko skrót (scrypt), opóźnienie po błędnych próbach;
      bez PIN-u serwer nie wydaje zapisów ani ustawień profilu.
- [x] Windows: synchronizacja przez serwer, SMB jako zapas (0.24.0; kompresja zstd,
      kopie zapasowe po stronie serwera, wysyłka zaległych zapisów przy starcie).
- [x] Android: wybór profilu, zapisy tylko uruchamianej gry przed startem i po powrocie,
      kolejka wysyłek bez połączenia; RetroArch, DuckStation, ArmSX2/AetherSX2 (0.25.0).
- [ ] Android: PPSSPP, Dolphin.
- [x] Android: sprawdzenie folderów danych emulatorów (muszą być we wspólnej pamięci,
      nie w `Android/data`) i podpowiedź, gdzie przestawić (0.25.0).
- [ ] Wspólne zapisy między różnymi emulatorami tylko przy zgodnym formacie
      (np. karta PS1: DuckStation `.mcd` ↔ SwanStation `.srm`).

## Etap C — biblioteka z serwera

- [ ] Baza gier, opisy i grafiki pobierane z serwera (paczki, potem tylko zmiany, ETag).
- [ ] Nowa instalacja: adres + klucz → profil + PIN → wszystko gotowe; lokalnie tylko
      ścieżki emulatorów, pady, GPU.
- [ ] Grafiki i metadane pobierane raz, na serwerze; klient dostaje pomniejszone obrazki.
- [ ] Kompresja zstd (Python 3.14 ma ją wbudowaną) dla bazy i list (zapisy i stany: 0.24.0);
      nie dla CHD/RVZ/ZIP (już skompresowane).

## Etap D — serwer jako usługa

- [ ] Usługa Windows z autostartem i restartem; `--stop-server`.
- [ ] Aktualizacja serwera z Releases; wersjonowanie API, komunikat „zaktualizuj serwer”.
- [ ] Edycja biblioteki (grafiki, metadane, opcje systemów, skanowanie) z klienta —
      konto administratora z PIN-em.
- [ ] Osobny klucz na urządzenie (do unieważnienia).
- [ ] Serwer nasłuchuje tylko na adresie Tailscale (100.x), nie w całej sieci lokalnej —
      HTTP bez TLS jest bezpieczne tylko wewnątrz tunelu Tailscale (szyfruje go WireGuard).
      Ważne, zanim przez serwer pójdą PIN-y i zapisy.
- [ ] Kopia zapasowa bazy serwera (biblioteka, profile, skróty PIN-ów).

## Android — sprawy bieżące

- [x] **Stały klucz podpisu APK** (0.25.0) — bez niego każda aktualizacja wymaga odinstalowania
      (traci ustawienia aplikacji). Potrzebny przed etapem B (kolejka zapisów w telefonie).
- [ ] Test uruchamiania gier: RetroArch (oba warianty), ArmSX2, AetherSX2.
- [ ] Później, opcjonalnie: wbudowany silnik libretro dla starszych systemów.
