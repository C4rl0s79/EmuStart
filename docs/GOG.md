# Gry GOG z serwera EmuStart

Serwer EmuStart udostępnia gry GOG z **istniejącego archiwum depotów** GOG Managera (`D:\GIT\GogGui`, tryb
„lokalne depoty”). Silnik EmuStart (klient Windows / silnik Lumen) instaluje je **wprost z serwera** do katalogu
gry. Interfejs jest w Lumen (pulpit i TV); samodzielny interfejs EmuStart (`web/`) nie ma ekranu GOG w tej wersji —
metody `Api.gog_*` są dostępne, ale nic ich nie wywołuje. APK: nie dotyczy (gry PC Windows).

Serwer **nie loguje się do GOG i niczego z GOG nie pobiera**. Klient pobiera wyłącznie z serwera EmuStart.

## 1. Format archiwum (wejście, tylko odczyt)

Kontrakt ustalony na prawdziwym archiwum (`D:\GOGInstall\_depots`, 2 gry, 6519 chunków, 44 GB) i kodzie
`GogGui/depot_archive.py` (format `goggui-depot-archive`, wersja 1):

| Plik | Zawartość |
|---|---|
| `archive.json` | `{"format": "goggui-depot-archive", "version": 1}` — inny format / nowsza wersja = odmowa |
| `products/<pid>/<build>.json` | rekord: `product_id`, `build_id`, `version_name`, `os`, `generation` (2), `install_directory`, `products` (baza + DLC z `has_files`), `depots` (manifest, productId, języki `en-US`/`*`), `dependencies` (`id`, manifest), `scope` (zarchiwizowane języki — krótkie kody — i DLC), `state` |
| `products/<pid>/<build>.meta.json` | meta buildu Galaxy — **zawiera `clientId`/`clientSecret` gry**: serwer ich nie wysyła |
| `manifests/depot/ab/cd/<id>.json` | manifest depotu: `depot.items` (`DepotFile` z `chunks` [`compressedMd5`, `compressedSize`, `md5`, `size`], `md5`/`sha256` pliku — nie zawsze, `sfcRef`, `flags`; `DepotDirectory`), `depot.smallFilesContainer` |
| `manifests/dep/ab/cd/<id>.json` | zależności (`__redist/MSVC20xx/…`, `__redist/DirectX/DXSETUP.exe` + `.cab`) |
| `chunks/ab/cd/<compressedMd5>` | chunk zlib dokładnie jak z CDN; nazwa = MD5 skompresowanych danych |

Ustalenia z prawdziwych danych: pliki z kontenera małych plików (`sfcRef`) **mają też własne chunki** w archiwum
(190/190) — instalator używa ich bez pliku tymczasowego; kontener SFC (plik tymczasowy jednego depotu, liczony
w wolnym miejscu) tylko dla plików bez własnych chunków. Języki: depoty mają kody `pl-PL`, zakres archiwum krótkie
`pl`. Uruchamianie: `goggame-<pid>.info` (`playTasks`) jest plikiem w depocie; finalizacja: `goggame-<pid>.script`
(`supportData`, `setIni`, `setRegistry` HKCU/HKLM, `savePath`). Wing Commander Prophecy: 26 wpisów HKCU, launcher
wyboru części; Dying Light: 21 DLC, 9 zależności.

**Kompletność** = `state: complete` **i** obecność wszystkich manifestów **i** każdego chunka z właściwym
rozmiarem (`deep` w panelu: także MD5) **i** bezpieczne ścieżki **i** co najmniej jeden kompletny język. Język jest
kompletny, gdy gra bazowa ma depot w tym języku (albo tylko depoty neutralne). Niekompletny build nie jest
publikowany (panel pokazuje powód).

## 2. Serwer

**Konfiguracja:** panel serwera → Konfiguracja → **„Archiwum depotów GOG (opcjonalnie)”** — folder `_depots`
GOG Managera (konto SYSTEM nie widzi dysków sieciowych z literą — ścieżka UNC). Przycisk **„Odśwież indeks”**
(albo ponowny zapis ścieżki) odczytuje archiwum od nowa (np. po migracji kolejnych gier w GOG Managerze). Indeks
(`emustart/gogarchive.py`) jest w pamięci, odtwarzany przy starcie serwera; archiwum nie jest zmieniane. Zmiana
albo wyczyszczenie ścieżki w trakcie indeksowania nie przepada: bieżący wynik jest odrzucany i indeks buduje się
dla ostatniej ścieżki (pusta ścieżka = od razu nic nie jest udostępniane). Ponowny zapis rekordu przez GOG Managera
(np. ponowna weryfikacja — zmienia tylko daty) nie przerywa instalacji: serwer porównuje hash zawartości.

**Opisy i grafiki** (`emustart/gogmeta.py`): foldery gier GOG Managera w katalogu nadrzędnym `_depots`
(`<katalog GOG>/<slug>/`): `product_<slug>.json` (identyfikator, opis), `galaxy_<slug>.json` (producent, wydawca,
gatunki, data wydania), `boxart_<slug>.jpg` (okładka), `background_<slug>.jpg` (tło), `titlelogo_<slug>.png` /
`logo_<slug>.jpg`, `iconsquare_<slug>.png` / `icon_<slug>.png`. Czytane przy każdym indeksowaniu; gra przypisana po
identyfikatorze z `product_*.json`. Opis (HTML → tekst) trafia do katalogu (`info`), grafiki — tylko gier z archiwum,
tylko z ich folderu i stałych wzorców nazw. `game-details.json` (klucze CD) nie jest czytany.

**API** (styl `/v1/…`, `Authorization: Bearer <token urządzenia>`, tylko konta z prawem pobierania gier):

| Zapytanie | Odpowiedź |
|---|---|
| `GET /v1/gog/games` | gry z kompletnym buildem: buildy, kompletne języki, DLC, zależności, rozmiary, `hash` |
| `GET /v1/gog/games/<pid>` | jedna gra (z `info`: opis, data wydania, producent, wydawca, gatunki, dostępne grafiki) |
| `GET /v1/gog/games/<pid>/art/<grid\|hero\|logo\|icon>` | grafika gry z folderu GOG Managera (klient trzyma kopię — biblioteka działa bez serwera) |
| `GET /v1/gog/games/<pid>/builds/<build>/manifest` | rekord + meta bez sekretów + manifesty depotów i zależności, `hash` buildu |
| `GET /v1/gog/chunks/<md5>?pid=&build=&hash=` | skompresowany chunk — tylko gdy należy do tego buildu, hash się zgadza, MD5 pliku jest poprawne |

`/v1/info` → `features` zawiera `gog`, gdy indeks ma gry. Błędy: 401 (brak/zły token), 403 (konto bez gier),
404 (zły identyfikator, obiekt spoza buildu), **409 `changed`** — build zmienił się/zniknął od przypięcia albo chunk
na serwerze jest uszkodzony (instalacja kończy się błędem, nigdy mieszanką buildów).

**Bezpieczeństwo:** identyfikatory walidowane wyrażeniami (`pid`, `build` — cyfry, `md5` — 32 hex, `hash` — 64 hex);
ścieżki do plików budowane tylko z nich (żadnej ścieżki od klienta); `clientId`, `clientSecret`, tokeny usuwane
z odpowiedzi; build z niebezpieczną ścieżką w manifeście (`..`, litera dysku, UNC, `:`, nazwy urządzeń, spacja/kropka
na końcu) nie jest publikowany; hash buildu = SHA-256 rekordu (bez dat), meta bez sekretów i wszystkich manifestów.

## 3. Silnik (klient)

Moduły: `gogarchive.py` (format, ścieżki), `goginstall.py` (źródło serwera, plan, zapis, weryfikacja,
wznowienie, DLC, naprawa, odinstalowanie), `gogfinal.py` (finalizacja, rejestr, uruchamianie, procesy),
`goglib.py` (kolejka zadań, katalog z kopią offline, uruchamianie i śledzenie). Metody `Api.gog_*`.

Przepływ: manifest buildu (przypięty hashem) + języki + DLC → plan (depoty neutralne + wybrane języki; plik
w kilku depotach: wariant języka głównego, potem neutralny, wariant tylko innych języków pominięty) → sprawdzenie
miejsca (pliki do pobrania + plik SFC + 64 MB) → znacznik instalacji w rejestrze **przed** pierwszym plikiem →
chunki z serwera (4 wątki × 2 chunki w pamięci) → MD5 skompresowanych → dekompresja z limitem rozmiaru z planu →
MD5 danych → zapis pod offset w pliku docelowym → weryfikacja wszystkich plików (MD5 każdego fragmentu) →
finalizacja → stan `installed`.

**Przerwana instalacja:** ten sam wybór (build, języki, język główny, DLC, katalog) — wznowienie od ukończonych
plików; inny wybór — pliki przerwanej instalacji są usuwane (jak przy odinstalowaniu) i instalacja zaczyna się
od nowa. **Naprawa** (stan `damaged`) pobiera tylko niezgodne pliki i nie zmienia reszty rejestru instalacji
(finalizacja, zapisy, wartości HKCU, wykonany krok administratora); nieudana naprawa zostawia stan `damaged`.

**Rejestr lokalny:** `<dane>/gog/installs/<pid>.json` — productId, buildId, hash manifestu, katalog, języki
(i główny), DLC, stan (`installing`, `installed`, `needs_admin`, `failed`, `cancelled`, `damaged`), ukończone pliki
(wznowienie), lista plików instalacji, kroki finalizacji, foldery zapisów, wartości HKCU, sposób uruchamiania.
Kopia manifestu: `<dane>/gog/manifests/<pid>/<build>.json` (z sumą SHA-256 w rejestrze) — weryfikacja i usuwanie
bez serwera. Katalog serwera (offline): `<dane>/gog/catalog.json`.

**Finalizacja (tylko znane akcje, nic z manifestu nie jest uruchamiane):** `supportData` (kopia wewnątrz katalogu
gry), `setIni` (katalog gry albo Dokumenty), `setRegistry` HKCU `Software\…` (typy string/dword/binary/expandstring,
`{app}` = katalog gry; złe dane dword/binary = błąd finalizacji z komunikatem), HKLM → stan `needs_admin`: gra
**nie jest** zainstalowana, dopóki gracz nie zatwierdzi kroku (plik `.reg` + `regedit /s` z oknem UAC; po nim
sprawdzenie wartości). Na 64-bitowym Windows wpis HKLM trafia do obu widoków rejestru (natywnego
i `Software\WOW6432Node`, który czytają gry 32-bitowe); `expandstring` jako `REG_EXPAND_SZ`. Biblioteki z zależności (Visual C++,
DirectX) — osobny, jawny krok „Biblioteki systemowe” (każdy instalator z UAC, znane parametry ciche); pliki
zależności są zawsze instalowane i weryfikowane. Brak `goggame-<pid>.info` albo błąd akcji = `failed`.

**Uruchamianie (bez serwera):** zadanie gry z `goggame-<pid>.info` przed launcherem zewnętrznym (reguła GOG
Managera — Wiedźmin 3); w Lumen „Uruchom…” pozwala wybrać inne zadanie (np. launcher Wing Commandera / Secret Ops).
Tylko `.exe` wewnątrz katalogu gry, bez powłoki. Gra „działa”, dopóki żyje jej proces **albo dowolny proces z plikiem
w katalogu gry** (launcher, który uruchamia grę i się zamyka, nie kończy sesji). Ograniczenie: gra, która uruchamia
program spoza swojego katalogu i sama się kończy, jest uznana za zakończoną.

**Odinstalowanie:** tylko pliki z listy instalacji (także niedokończonego DLC) i utworzone przy finalizacji (kopie
`supportData`, nowe pliki `setIni` w katalogu gry), pliki tymczasowe SFC, puste katalogi, pusty katalog gry; wartości HKCU ustawione przez finalizację (klucz gry,
gdy pusty; klucze nadrzędne wydawcy zostają). Zostają: zapisy (`savePath`), pliki dodane przez grę lub gracza,
pliki `setIni` w Dokumentach, wszystko poza katalogiem gry; dowiązania i punkty połączenia nie są śledzone.
Archiwum na serwerze nie jest dotykane. Cache gier emulowanych (`cache.py`) nie zna gier GOG; katalog gier GOG
w cache jest odrzucany.

## 3a. Zapisy i ustawienia gier (synchronizacja przez serwer EmuStart)

Bez chmury GOG i bez GOG Galaxy: GOG daje tylko identyfikator gry (productId). Kopie, wersje i konflikty — wyłącznie
serwer EmuStart, tym samym mechanizmem co zapisy gier emulowanych (`savesync.py` / `syncclient.py`).

**Reguły (co należy do gry i gdzie to jest).** Pierwszeństwo:
1. ręczne reguły gracza (to urządzenie: `<dane>/gog/saves/<pid>.json`, okno „Zapisy i ustawienia” w Lumen),
2. poprawka serwera: `<dane serwera>/saverules/overrides.json` — `{"<productId>": {"title": "…", "files":
   {"<ścieżka>": {"tags": ["save"]}}, "registry": {…}}}` (pola jak w ludusavi-manifest; aktualizacja manifestu
   ich nie zmienia),
3. [ludusavi-manifest](https://github.com/mtkennerly/ludusavi-manifest) — licencja MIT, © Matthew T. Kennerly,
   dane zebrane z PCGamingWiki. Kopia: `<dane serwera>/saverules/manifest.yaml` + indeks gier GOG
   `gog-index.json` (poprzedni: `gog-index.prev.json`).

Dopasowanie wyłącznie po productId: wpis z `gog.id` == productId, bez takiego — jedyny wpis z productId w
`id.gogExtra`. Kilka kandydatów → „niejednoznaczne”: gracz wybiera wpis z listy kandydatów (nigdy automatycznie po
tytule). Brak wpisu → „brak reguł”, gracz może wskazać ścieżkę ręcznie. `goggame-<pid>.info` nie jest źródłem
lokalizacji zapisów.

**Aktualizacja bazy reguł** (serwer, `saverules.py`): w tle przy starcie serwera (najwyżej raz na tydzień) i przy
„Odśwież indeks” w panelu (wymusza sprawdzenie). GET z `If-None-Match` (ETag) → plik w pamięci → parsowanie YAML
(PyYAML) i walidacja → podmiana indeksu; błąd sieci / uszkodzony plik = dalej działa poprzednia kopia. Nigdy przed
startem gry. Status w panelu: „reguły zapisów: <wersja> (<n> gier GOG)”. API: `GET /v1/gog/games/<pid>/saverules
[?title=<kandydat>]`. Klient trzyma kopię reguł gry (`<dane>/gog/saverules/<pid>.json`, odświeżaną raz na dobę i
przy synchronizacji ręcznej) — bez serwera działa na ostatniej znanej wersji.

**Rozwijanie ścieżek** (`gogsaves.py`): `<base>` = katalog instalacji gry na tym komputerze, `<root>` / `<game>` —
jego rodzic i nazwa, `<home>`, `<winAppData>`, `<winLocalAppData>`, `<winLocalAppDataLow>`, `<winDocuments>`,
`<winPublic>`, `<winProgramData>`, `<winDir>` — z Windows (`SHGetKnownFolderPath`, także przekierowane Dokumenty,
np. OneDrive), `<storeUserId>` = `*` (dowolny użytkownik sklepu), `<storeGameId>` = productId, `<osUserName>`.
Warunki: reguła bez `when` — zawsze; inaczej któryś warunek pasuje do `os` windows / dos (gry DOS przez DOSBox z
paczki GOG) i `store` gog albo bez sklepu. Tagi: `save` (także bez tagów i z oboma) → zapisy, tylko `config` →
ustawienia. Ścieżka = wzorzec (`*`, `?`, `**`); folder pasujący do wzorca — w całości. Reguła obejmująca cały folder
systemu (profil, Dokumenty, AppData, katalog gier…) jest pomijana z powodem.

**Model danych.** Każda reguła = zbiór plików: folder bazowy (stała część ścieżki po rozwinięciu) + wzorzec względny.
Na serwer idą wyłącznie ścieżki względne wewnątrz zbioru. Klucz paczki: konto (z tokenu, nie z zapytania) + platforma
`gog` + gra = productId + emulator `gog` + rdzeń = identyfikator zbioru (`s` + skrót szablonu reguły — ten sam na
każdym urządzeniu) + rodzaj `saves` / `config`. Wersja (revision), SHA-256 paczki i treści, urządzenie, historia (10
poprzednich wersji na serwerze) — jak w `savesync.py`. Drugi komputer rozwija ten sam szablon do swojej ścieżki.

**Cykl.** Przed grą (`goglib.launch`): reguły → zbiory → `syncclient` (tylko na serwerze → pobranie do pliku
tymczasowego, SHA-256 i ZIP sprawdzone, limit plików / rozmiaru po rozpakowaniu, kopia zapasowa bieżących plików,
podmiana plik po pliku przez plik tymczasowy; tylko tutaj → wysłanie; tu i tam → konflikt). Konflikt: gra nie startuje,
Lumen pyta: wersja z tego urządzenia (serwerowa zostaje w historii serwera) / z serwera (bieżące do kopii zapasowej) /
na razie bez synchronizacji (konflikt wraca przy następnej grze, po grze nic nie jest wysyłane). Po faktycznym końcu
gry (żaden proces z katalogu gry — zamknięty launcher nie kończy sesji): ponowny skan i wysłanie zmienionych zbiorów;
serwer przyjmuje wersję dopiero po pełnym przesłaniu (init → plik → commit z `base_revision`; inna wersja na serwerze
= 409 → konflikt przy następnej grze). Bez połączenia: zmiana oznaczona jako oczekująca, wysyła ją ponawianie w tle
albo następna gra. Pusty skan (folder zniknął, dysk odłączony) niczego nie usuwa na serwerze. Synchronizacja ręczna i
przywrócenie kopii — nie podczas gry.

**Ustawienia gracza** (Lumen → szczegóły gry → „Zapisy…”, w TV: opcje gry → „Zapisy i ustawienia”): synchronizacja
zapisów (domyślnie włączona), ustawień (osobno, domyślnie wyłączona — bywają zależne od urządzenia), synchronizacja
teraz, wybór wpisu przy niejednoznacznych regułach, ręczne reguły, przywrócenie kopii zapasowej (bieżące pliki najpierw
do nowej kopii), lokalizacje rozwiązane na tym komputerze, źródło i wersja reguł, ostatnia synchronizacja, zmiany do
wysłania.

**Rejestr Windows.** Reguły z wpisami rejestru (u Ciebie ~62 gry z archiwum) są pokazywane jako „wymaga obsługi
rejestru — wpisy nie są synchronizowane”; pliki tej gry synchronizują się normalnie. Brak importu plików `.reg`.

**Ograniczenia.** Rejestr (wyżej). Zmiana szablonu reguły w nowej wersji manifestu = nowy zbiór (stary zostaje na
serwerze, bez automatycznej migracji). Wine/Proton — nie dotyczy (klient GOG jest tylko dla Windows). Sloty zapisów
(jak w grach emulowanych) — nie dla gier GOG.

## 4. Ograniczenia tej wersji

- **Aktualizacja** zainstalowanej gry do nowego buildu: nieobsługiwana — Lumen pokazuje, że serwer ma nowszy build;
  przycisku „Aktualizuj” nie ma. DLC dogrywa się tylko z tego samego buildu (ten sam hash).
- Tylko Galaxy content-system v2, Windows (`os: windows`). Gry bez depotów v2 GOG Manager pomija sam.
- Opisów i grafik archiwum nie ma: opis z bazy LaunchBox (Windows), grafiki ze SteamGridDB po nazwie.
- Zapisy gier GOG: synchronizacja przez serwer EmuStart (sekcja 3a); rejestr Windows jeszcze bez synchronizacji.
- `setRegistry` HKLM wymaga zgody administratora (UAC) — bez obchodzenia. Odinstalowanie nie usuwa wpisów HKLM
  (wymagałoby ponownego UAC).
- Pełnej instalacji prawdziwej gry nie wykonano (testy: syntetyczne archiwum, prawdziwe archiwum — plan obu gier
  i 589 małych plików z prawdziwych chunków).

## 5. Testy

Automatyczne: `tests/test_gog_server.py` (indeks, kompletność, ścieżki, sekrety, autoryzacja, przypięcie, 409),
`tests/test_gog_install.py` (pionowy przepływ przez HTTP, języki, DLC, zależności, finalizacja, HKLM, bufor,
przerwanie/wznowienie, zmieniony build, uszkodzony/brakujący obiekt, miejsce, naprawa, uruchamianie bez serwera,
launcher, odinstalowanie, punkty połączenia, cache), `tests/test_platform_family.py`. Dane: `tests/goghelp.py`
(kilka KB). Testy nigdy nie piszą do prawdziwego rejestru.

### Checklista ręczna (prawdziwe gry)

- [ ] panel serwera: ścieżka `_depots`, status „gry GOG: N”, „Odśwież indeks” po dodaniu gry w GOG Managerze
- [ ] mała gra jednojęzyczna: instalacja w Lumen (pulpit), pasek postępu w zadaniach, „Graj”, gra startuje
- [ ] gra PL+EN (Wiedźmin 3): PL główny, gra po polsku, `setIni` w Dokumentach
- [ ] gra z DLC: instalacja z DLC; „Dodaj DLC” do istniejącej instalacji
- [ ] Wing Commander Prophecy: wpisy HKCU, „Uruchom…” → launcher i Secret Ops
- [ ] gra DOS (DOSBox w zależnościach): start po odłączeniu serwera
- [ ] przerwanie w trakcie (zadania → Anuluj), restart Lumen, „Wznów instalację” — bez ponownego pobierania gotowych plików
- [ ] „Sprawdź pliki” po ręcznym uszkodzeniu pliku → „Napraw pliki”
- [ ] „Biblioteki systemowe” (UAC), gra z HKLM → „Dokończ instalację” (UAC)
- [ ] usunięcie gry: zapisy zostają, katalog bez plików gry, archiwum serwera bez zmian
- [ ] tryb TV padem: PC → GOG → gra → Graj / instalacja; przypięcie GOG (Y), powrót B
