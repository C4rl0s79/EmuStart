# EmuStart 2.0 — koncepcja

Stan: **wersja robocza 3** (2026-10-12) — dopisane: stany jak w Steam (5.5), nowy stan kombinacją
pada (5.6), „Kontynuuj” jako najnowszy niezmienny stan (5.3). Wcześniej: decyzje z rozmowy i odpowiedzi na otwarte sprawy. Zastępuje podejście
z `CONCEPT.md` (klient z dostępem SMB do NAS-a) — tamten dokument zostaje jako historia 0.x.

---

## 1. Zasada główna

**Serwer EmuStart jest jedynym źródłem prawdy.** Trzyma wszystko poza samymi grami —
w jednym, przenośnym katalogu. Klienci (Windows, Android) są cienkimi aplikacjami:
lokalnie tylko pamięć podręczna gier i to, co dotyczy danego sprzętu.

| Decyzja | Ustalenie |
|---|---|
| SMB na klientach | **usunięte** — klient rozmawia tylko z serwerem (HTTP przez Tailscale), cała uwaga na szybkość relacji klient ↔ serwer |
| Folder z grami | **tylko na serwerze**; klienci ich nie znają |
| Dostępność serwera | komputer z serwerem ma prawie 100 % czasu pracy — klient może na nim polegać; bez serwera: tryb offline (gry pobrane, kolejka zapisów) |
| Rdzeń RetroArcha dla platformy | **zalecany i ostrzegany**, nie wymuszany (różny rdzeń = stany się nie przenoszą — komunikat) |
| Konflikt autozapisu | **pokazać oba** (urządzenie, czas, zrzut ekranu, wersja rdzenia/emulatora) i dać wybrać, który zostaje; drugi trafia do historii |
| Własne grafiki na kliencie | wybór dla **klasy urządzeń** (wszystkie telefony / wszystkie komputery) |
| Parowanie urządzeń | **kod QR** z panelu serwera (zamiast przepisywania klucza) |
| Aktualizacje | **z serwera**: program (PC, APK) i **emulatory** — z pilnowaniem zgodności formatów stanów; **emulator aktualizowany dopiero po potwierdzeniu gracza** |
| Tożsamość gry | sumy kontrolne z plików DAT (No-Intro / Redump) — pliki na serwerze są sprawdzone względem DAT-ów, sumy z nagłówków (CHD) są zgodne; bez liczenia sum całych plików |
| Wersja formatu stanu | **odczytywana ze stanu** (nagłówek pliku stanu) |
| Instalacja emulatorów na Androidzie | serwer podaje APK i **prosi o instalację**, gracz potwierdza |

---

## 2. Katalog serwera (przenośny)

```
<serwer>\                       np. E:\EmuStart — skopiowanie katalogu = przeniesienie serwera
  EmuStart.exe, _internal\
  server\
    config.json                 ustawienia serwera; JEDYNE ścieżki zewnętrzne: foldery z grami
    library.sqlite              biblioteka, metadane, tożsamość gier
    media\                      grafiki: oryginały + wersje dla klas urządzeń (WebP)
    bios\                       BIOS-y (sprawdzone MD5)
    emu\<emulator>\<warstwa>\    ustawienia emulatorów (patrz 4.)
    builds\                     wersje EmuStart (PC zip, APK) i emulatorów do rozdania klientom
    profiles\<profil>\          zapisy, stany, historia, ustawienia profilu
    devices.json                zarejestrowane urządzenia (klucz, klasa, wersje, układy)
    backups\                    automatyczne kopie katalogu serwera
  logs\
```

Migracja z 0.x (automatycznie przy pierwszym starcie serwera 2.0): profile z udziału
(`<udział>\emustart\Profiles`) → `server\profiles`, `data\` → `server\`, konfiguracja
klienta z PC (foldery z grami, wybory logo) → serwer.

---

## 3. Klient

Trzyma lokalnie tylko:
- pamięć podręczną gier (pobranych / przypiętych),
- kopię biblioteki i grafik do pracy offline (synchronizowaną różnicowo),
- **ustawienia sprzętu**: ścieżki i instalacje emulatorów, pady, GPU, audio, okno,
- kolejkę zapisów do wysłania.

Nie ma: folderów z grami, SMB, własnej bazy metadanych, pobierania grafik z internetu.

**Parowanie:** panel serwera → „Dodaj urządzenie” → kod QR (adres + jednorazowy kod).
Telefon skanuje aparatem, komputer — wkleja kod. Każde urządzenie dostaje **własny klucz**
(widoczny w panelu, do unieważnienia), nazwę i klasę (`pc` / `phone`).

---

## 4. Ustawienia warstwowe

Każde ustawienie ma jedną warstwę jako „dom”; czytane od ogólnej do szczegółowej:

| Warstwa | Przykłady |
|---|---|
| **globalna** | rdzeń zalecany dla platformy, opcje rdzeni (`.opt`), remapy (`.rmp`), BIOS-y |
| **platforma** (windows / android) | sterownik obrazu, shadery, przyciski ekranowe |
| **urządzenie** | GPU, audio, ścieżki, folder stanów, wybory nadpisane tylko tu |
| **profil** | konto RetroAchievements, wygląd, ulubione, PIN |

Ustawienia emulatorów synchronizują się **w obrębie tej samej rodziny i formatu**
(PCSX2 ↔ PCSX2, RetroArch ↔ RetroArch). Między PC a telefonem przenoszą się tylko rzeczy
o wspólnym formacie (opcje rdzeni, remapy, nadpisania RetroArcha). Ograniczenia Androida:
ARMSX2 ma inny format ustawień niż PCSX2; DuckStation trzyma dane w niedostępnym
`Android/data`.

---

## 5. Zapisy i stany 2.0

### 5.1 Tożsamość gry
Gra rozpoznawana po **identyfikatorze z pliku DAT** (No-Intro / Redump: CRC/SHA-1 wpisu DAT),
nie po nazwie pliku. Pliki na serwerze są sprawdzone względem DAT-ów, więc sumy zapisane
w nagłówkach (CHD: SHA-1 danych) i w ZIP-ach (CRC) są zgodne z DAT-em — serwer odczytuje je
raz, bez liczenia sum całych, dużych plików. Klient pyta serwer: *„gdzie na tym urządzeniu ma leżeć zapis /
stan gry X dla emulatora/rdzenia Y?”* — serwer odpowiada wg układu zgłoszonego przez
urządzenie (płasko, folder rdzenia, folder gry…; jak `rastates` w 0.31.1, dla wszystkich
emulatorów). Koniec problemów ZIP vs ROM w środku, `_` vs spacja, folder rdzenia.

### 5.2 Magazyn na serwerze
`profiles\<profil>\saves\<gra>\` — zapisy w grze (wspólne) i stany wg `<emulator lub rdzeń>`,
każdy plik z opisem: urządzenie, czas, **wersja rdzenia/emulatora**, **wersja formatu stanu
odczytana ze stanu** (PCSX2/ARMSX2: `Savestate Version.id` w pliku `.p2s`; DuckStation: wersja
w nagłówku `.sav`; RetroArch: nagłówek stanu rdzenia — gdy rdzeń jej nie zapisuje, wersja
rdzenia zgłoszona przez urządzenie), zrzut ekranu. **Historia**: ostatnie N wersji każdego
pliku, przywracanie z interfejsu („cofnij do wczoraj”).

### 5.3 „Kontynuuj” — najnowszy stan wczytuje się sam
- Przy wyjściu z gry autozapis (`.state.auto`, ARMSX2 `.autosave.p2s`, PCSX2 `.resume.p2s`)
  idzie na serwer **jako nowy, niezmienny stan** (5.5) — nie nadpisuje poprzedniego.
- Przy starcie gry na dowolnym urządzeniu **najnowszy zgodny stan** (ręczny albo z wyjścia)
  wczytuje się sam — urządzenie podkłada go w miejsce autozapisu emulatora.
- W podglądzie gry: **„Kontynuuj z telefonu · bsnes v115 · 12 min temu”**.
- Jednoczesna gra na jednym koncie w dwóch miejscach jest niemożliwa (blokada profilu),
  więc kolejność stanów jest jednoznaczna. Gra bez serwera: stany dochodzą później jako
  nowe wpisy — nic nie ginie i nic się nie nadpisuje; „Kontynuuj” pokazuje oba najnowsze
  (urządzenie, czas, zrzut), gracz wybiera.
- Niezgodność wersji (inny rdzeń, inny format stanu) — ostrzeżenie zamiast cichej porażki.

### 5.4 Rdzeń dla platformy
Serwer trzyma **zalecany rdzeń RetroArcha dla platformy** (np. SNES → bsnes) i wyjątki dla
gier. Klient z innym rdzeniem dostaje ostrzeżenie („stany z bsnes się nie wczytają”).
Brak rdzenia: PC instaluje go sam, telefon pokazuje „pobierz bsnes w RetroArchu”.

### 5.5 Stany jak w Steam — tylko dochodzą, nigdy się nie nadpisują
**Zasada:** każdy zapis stanu to **nowy, niezmienny wpis na serwerze** (urządzenie, czas,
wersja emulatora / formatu, zrzut). Przed grą urządzenie **wypełnia sloty emulatora
najnowszymi zgodnymi stanami** tej gry (ze wszystkich urządzeń konta). Po grze każdy plik
stanu o zawartości nieznanej serwerowi trafia na serwer jako nowy wpis.

- **RetroArch** — natywnie: `savestate_auto_index = true` (każdy zapis = kolejny numer,
  `.state13`, `.state14`…), wczytanie najnowszego przy starcie. Numer jest **globalny dla
  konta**: przed grą urządzenie ma wszystkie stany, więc RetroArch bierze następny wolny.
  Kolizja po grze bez serwera (dwa urządzenia zrobiły `.state13`) — **serwer przy wysyłce
  nadaje pierwszy wolny numer**, gdy taki już ma z inną zawartością. Limit:
  `savestate_max_keep` na urządzeniu + serwer trzyma ostatnie N stanów gry (np. 30), starsze
  w historii.
- **PCSX2 / ARMSX2** — 10 stałych slotów (`<serial> (<crc>).<slot>.p2s`), bez automatycznego
  numeru → **warstwa EmuStart**: przed grą sloty 1–9 = 9 najnowszych **zgodnych** stanów
  (wg `Savestate Version.id` odczytanego ze stanu), slot 0 / `.resume` = najnowszy („Kontynuuj”);
  po grze nowe i zmienione sloty → nowe wpisy. Stany ze starszej wersji formatu zostają
  w historii i nie są podsuwane nowszemu emulatorowi (spójne z §6). PCSX2 ↔ ARMSX2: ten sam
  format, zgodność wg wersji — mechanizm sam pokaże, które stany przechodzą.
- **Dolphin** (sloty 1–10, ma „zapis do najstarszego slotu”), **PPSSPP** (5 slotów),
  **DuckStation na PC** — ta sama warstwa slotów.
- **Koszt:** numer slotu nie jest stały (sloty posortowane od najnowszego) — w menu EmuStart
  w grze i w menedżerze zapisów przy każdym slocie data, urządzenie i miniatura.
- **Zapisy w grze** (karty pamięci, `.srm`, `.mcd`) — jeden plik na grę, nie da się ich
  „dokładać”: zostają przy blokadzie profilu, porównaniu trzech wersji i kopii przy konflikcie.
- **Miejsce:** stany PS2 5–50 MB — limit N na grę i kompresja historii; przed grą
  pobierane są tylko stany tej gry i tego emulatora / rdzenia, których urządzenie nie ma.

### 5.6 Nowy stan jedną kombinacją pada — na każdej platformie
Globalna kombinacja (domyślnie **LB + Y**, do zmiany w ustawieniach konta) robi **nowy stan**
(5.5) w trakcie gry, bez otwierania menu — razem z automatycznym numerem i wczytaniem
najnowszego przy starcie daje to „zapisz i wyjdź” wszędzie tak samo.

- **Windows:** EmuStart czyta pada sam (XInput, także gdy emulator jest na wierzchu — jak menu
  A+Y) → zapis w emulatorze: RetroArch komendą sieciową `SAVE_STATE`, pozostałe skrótem
  klawiszowym emulatora (jak menu w grze dziś); krótki komunikat nad grą „Zapisano stan #14”.
- **Android — RetroArch:** bez dodatkowych uprawnień — EmuStart ustawia w pliku ustawień
  profilu (już podawanym RetroArchowi) skrót zapisu stanu na tę kombinację (przycisk
  „hotkey” + przycisk zapisu) i `savestate_auto_index`; RetroArch sam pokazuje komunikat.
- **Android — inne emulatory (ARMSX2…):** *„wyświetlanie nad innymi aplikacjami”* pozwala
  tylko **rysować** nad emulatorem (komunikat), **nie czytać pada**. Odczyt kombinacji
  przy emulatorze na wierzchu wymaga **usługi ułatwień dostępu** (filtr klawiszy — przyciski
  pada to zdarzenia klawiszy), a i ona **nie może nacisnąć nic w emulatorze** — zapis musi
  wywołać sam emulator. Dlatego: najpierw własne skróty emulatora (ARMSX2 — do sprawdzenia,
  czy pozwala przypisać zapis stanu do kombinacji pada), usługa ułatwień dostępu tylko jako
  opcja (gracz włącza ją sam w ustawieniach Androida), gdy emulator ma inny sposób wywołania
  zapisu.
- **Czy wtedy rezygnujemy z autozapisu?** Nie całkiem — autozapis przy wyjściu zostaje jako
  **zabezpieczenie** (awaria, rozładowana bateria, zapomniany zapis), ale nie jest już
  wyjątkiem: staje się kolejnym niezmiennym stanem (5.3). Nic nie jest nadpisywane, a gracz
  może grać wyłącznie na kombinacji.


---

## 6. Aktualizacje z serwera — program i emulatory

Serwer trzyma w `server\builds\` wersje rozdawane klientom:
- **EmuStart**: zip PC i APK — klient aktualizuje się z serwera (zawsze zgodny z nim);
  serwer pobiera nowe wydania z GitHuba.
- **Emulatory na PC** (RetroArch, PCSX2, DuckStation…): wersje **przypięte** na serwerze —
  klienci instalują / aktualizują dokładnie tę wersję.

### Zgodność stanów PCSX2 ↔ ARMSX2
Format stanu PCSX2/ARMSX2 ma numer wersji (`Savestate Version.id`). Serwer zna:
- wersję formatu stanów **ARMSX2 na każdym telefonie** (z jego stanów i z wersji aplikacji
  zgłaszanej przez telefon),
- wersję formatu **PCSX2**, który rozdaje komputerom.

Zasady:
- **Emulator nie aktualizuje się bez potwierdzenia gracza.** Serwer sprawdza nowe wydania,
  ale instalacja następuje dopiero po zatwierdzeniu.
- **Nowa wersja ze zmienionym formatem stanów** (np. PCSX2) → ostrzeżenie w panelu serwera
  i w EmuStart na urządzeniach: *„Wyszedł PCSX2 X. Jego stany są niezgodne z obecnymi —
  po aktualizacji obecne stany się nie wczytają.”* Razem z listą gier, które mają stany.
  Zanim gracz potwierdzi aktualizację, ma **wczytać obecne stany i zapisać grę normalnie na
  karcie pamięci** (zapisy w grze przechodzą między wersjami, stany nie). EmuStart pomaga:
  przy każdej grze z listy „Uruchom ze stanem → zapisz na karcie”, odhaczanie zrobionych.
  Dopiero potwierdzenie gracza uruchamia aktualizację; stare stany trafiają do historii
  (nie są kasowane — wrócą, gdyby wrócić do starej wersji).
- **PCSX2 ↔ ARMSX2**: serwer pokazuje, czy po aktualizacji formaty na PC i telefonie będą
  zgodne (format X ≠ Y → stany nie przejdą między urządzeniami, dopóki drugi emulator się
  nie zrówna). Zalecenie: aktualizować, gdy oba mają ten sam format.
- ARMSX2 na telefonie aktualizuje się sam (sklep / APK) — serwer go nie wstrzyma, ale widzi
  jego wersję (format odczytany ze stanów) i ostrzega o niezgodności tak samo.

To samo dla rdzeni RetroArcha: serwer pamięta wersję rdzenia, z którą powstał stan.

---

## 7. Grafiki

- Oryginały na serwerze; serwer generuje wersje dla klas urządzeń (telefon: mniejsze WebP).
- Klient nie pobiera grafik z internetu — tylko z serwera (różnicowo, ETag).
- Zmiana grafiki na kliencie (okładka, logo, zrzut) → zapis na serwerze **dla klasy urządzeń**;
  domyślnie wspólna dla wszystkich.

---

## 8. Dodatkowo

1. **Panel zdrowia** (panel serwera): brakujące BIOS-y, brakujące rdzenie na urządzeniach,
   gry bez grafik, konflikty zapisów, ostatnio widziane urządzenia i ich wersje.
2. **„Ostatnio grane” i statystyki** wspólne dla wszystkich urządzeń, z „Kontynuuj”.
3. **Tryb offline** klienta: biblioteka i grafiki z kopii, gry pobrane, kolejka zapisów.
4. **Automatyczna kopia katalogu serwera** (bez pamięci podręcznej) — harmonogram, rotacja.
5. **Test integracyjny „trzech urządzeń”**: serwer + symulowany PC + symulowany telefon —
   zapis na jednym, wczytanie na drugim, konflikt, niezgodna wersja. Każda zmiana zapisów
   musi przez niego przejść.

---

## 9. Etapy

Każdy etap zostawia działający program; migracja automatyczna.

| Etap | Zakres |
|---|---|
| **1. Serwer przenośny** | układ `server\`, migracja profili z udziału i danych, rejestr urządzeń, **parowanie QR**, klucz na urządzenie |
| **2. Cienki klient** | biblioteka i grafiki z serwera (różnicowo), bez SMB i folderów z grami na klientach, tryb offline |
| **3. Aktualizacje z serwera** | EmuStart PC/APK z serwera; emulatory PC przypięte na serwerze; zgodność PCSX2 ↔ ARMSX2 |
| **4. Ustawienia warstwowe** | ustawienia emulatorów, BIOS-y, rdzeń zalecany dla platformy, grafiki dla klas urządzeń |
| **5. Zapisy 2.0** | tożsamość gry, magazyn z historią, **stany jak w Steam** (RetroArch: auto-indeks + numer na serwerze; PCSX2/ARMSX2/Dolphin/PPSSPP: warstwa slotów), „Kontynuuj” = najnowszy stan, **nowy stan kombinacją pada** (Windows, RetroArch na Androidzie), wybór przy konflikcie |
| **6. Dopracowanie** | panel zdrowia, statystyki, kopie katalogu serwera |

Test integracyjny „trzech urządzeń” powstaje w etapie 1 i rośnie z każdym kolejnym.

---

## 10. Ustalenia z otwartych spraw

- **Tożsamość dużych plików** (CHD, RVZ): sumy z DAT-ów i z nagłówków — pliki na serwerze
  były sprawdzone względem DAT-ów, więc sumy w nagłówkach są zgodne (bez liczenia sum
  całych plików).
- **Wersja rdzenia / formatu na Androidzie**: odczytywana ze stanu.
- **Instalacja emulatorów na Androidzie**: serwer podaje APK, EmuStart prosi o instalację,
  gracz potwierdza (Android nie pozwala instalować bez udziału użytkownika).
