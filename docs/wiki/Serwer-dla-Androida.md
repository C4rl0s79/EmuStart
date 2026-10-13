# Serwer dla Androida

EmuStart może działać na komputerze z grami (NAS) jako **serwer** dla aplikacji
EmuStart na Androida i dla EmuStart na innych komputerach z Windows (szybsze pobieranie
gier niż przez SMB — patrz [Uruchamianie gier](Uruchamianie-gier-NAS-i-pamięć-podręczna)).
Serwer udostępnia przez sieć (Tailscale):

- listę systemów i gier z metadanymi, opisami, okładkami, zrzutami i logo — telefon
  nic nie skanuje ani nie pobiera z baz grafik,
- pliki gier we fragmentach (pobieranie kilkoma strumieniami naraz, wznawianie),
- później także zapisy i ustawienia profili.

To ten sam program (`EmuStart.exe`), uruchamiany bez okna z parametrem `--server`.

## Instalacja na komputerze z grami

1. Skopiuj EmuStart (rozpakowane wydanie) na komputer z grami, np. `C:\EmuStart`.
   Jeśli zip był pobrany z internetu, odblokuj pliki (inaczej okno EmuStart się nie
   uruchomi — błąd „Failed to resolve Python.Runtime.Loader.Initialize”):
   ```
   Get-ChildItem -Recurse 'C:\EmuStart' | Unblock-File
   ```
   (albo przed rozpakowaniem: Właściwości zipa → „Odblokuj”). Gdy okno mimo to się nie
   uruchomi, EmuStart otworzy interfejs w przeglądarce — ustawienia działają tak samo.
2. Uruchom raz zwykłe `EmuStart.exe`: w Ustawieniach wskaż **foldery z grami tak,
   jak widzi je ten komputer** (np. `D:\ROMS\No-Intro`), zeskanuj kolekcję i w Grafikach
   pobierz bazę LaunchBox, grafiki i opisy. Zamknij program.
3. Uruchom **jako administrator** wiersz poleceń w folderze EmuStart i wpisz:
   ```
   EmuStart.exe --install-server
   ```
   Dodaje to zadanie w Harmonogramie zadań („EmuStart Server”, start razem z Windows)
   i reguły zapory dla portów 8740 (panel) i 8741 (szyfrowany).
4. Uruchom serwer od razu (albo zrestartuj komputer):
   ```
   schtasks /Run /TN "EmuStart Server"
   ```
5. Nowe urządzenie: panel serwera → **Dodaj urządzenie** (kod QR) — patrz
   [Logowanie urządzeń](#logowanie-urządzeń-043). Bez okna panelu: `EmuStart.exe --server-key`
   kopiuje do schowka link logowania (to samo co kod QR).

## Panel serwera

Serwer działa bez okna (zadanie Harmonogramu na koncie SYSTEM). Do podglądu i obsługi
służy osobne okienko:

```
EmuStart.exe --server-panel
```

(`--install-server` dodaje też skrót **EmuStart Server** w menu Start). Panel pokazuje
wersję i czas pracy serwera, adres dla telefonu, liczbę gier, urządzenia, które się łączyły,
i gry pobierane w tej chwili, a przyciski:

- **Przeładuj listę gier** — skan folderów z grami (nowe i usunięte gry) z paskiem postępu,
- **Restart serwera** — nowy proces serwera, stary kończy się; pobieranie na telefonie
  wznowi się samo,
- **Folder logów**.

Gdy serwer nie odpowiada, panel najpierw uruchamia zadanie „EmuStart Server”, a jeśli go
nie ma — serwer na koncie użytkownika. Panel działa tylko na tym komputerze (127.0.0.1).

Po aktualizacji do 0.25.12 uruchom ponownie `EmuStart.exe --install-server` (jako
administrator): zadanie dostaje brak limitu czasu pracy (schtasks domyślnie zatrzymuje
zadanie po 72 h) i ponowne uruchomienie po awarii.

## Logowanie urządzeń (0.43)

Jedyna droga urządzenia do serwera: **konto i hasło** + **klucz urządzenia** (para kluczy
EC P-256 tworzona na urządzeniu: Windows — TPM, bez niego DPAPI; Android — Android
Keystore; klucz prywatny nie opuszcza urządzenia) i logowanie podpisem (token sesji na
godzinę). W Ustawieniach aplikacji są tylko dwa wiersze:

- **Zaloguj (kod QR)** (telefon) — panel serwera → **Dodaj urządzenie** → zeskanuj kod. Kod
  niesie adresy serwera, odcisk jego certyfikatu i jednorazowy kod (10 minut): połączenie
  od pierwszego zapytania szyfrowane i przypięte, **bez czekania na zatwierdzenie** (kto
  zeskanował kod z ekranu serwera, ma do niego dostęp). Komputer bez aparatu: w panelu
  **Kopiuj link** i wklej go w **Zaloguj**.
- **Zaloguj (adres serwera)** — adres tunelu, np. `emustart.emucloud.org` (prawdziwy
  certyfikat). Prośba czeka na zatwierdzenie; adres IP bez kodu QR nie jest przyjmowany
  (nie ma czym sprawdzić certyfikatu).

Potem **Mam konto** / **Nowe konto** → nazwa → hasło (nowe konto: co najmniej 10 znaków,
wielka litera, cyfra, znak specjalny). Urządzenie pokazuje **kod weryfikacyjny**, np.
`K7Q2-9FMX`.

**Zatwierdzanie** (logowanie bez kodu QR): prośbę widzi inne urządzenie konta — okienko przy
otwarciu aplikacji (i co minutę) — albo panel serwera (**Proszą o zalogowanie**): czas,
adres, nazwa i rodzaj urządzenia, konto, kod. **Kod musi się zgadzać** z kodem na ekranie
nowego urządzenia. Zatwierdzenie *z grami z serwera* albo *tylko zapisy i profil*; albo
*Odrzuć*. Prośba czeka 24 godziny (przetrwa zamknięcie aplikacji i restart serwera).

- **nowe konto** — zatwierdza administrator;
- **Mam konto** — inne urządzenie tego konta; gdy konto nie ma jeszcze żadnego — administrator.

**Administrator** to cecha **konta** (panel → **Konta** → *Administrator*): wszystkie
urządzenia konta administratora widzą wszystkie konta i zatwierdzają nowe. Pierwsze konto
zalogowane kodem QR z panelu, gdy żadne nie jest administratorem, zostaje nim samo.

**Wyloguj z serwera** (Ustawienia): serwer unieważnia to urządzenie, klucz i zapamiętane
adresy znikają; gry, zapisy i profile na urządzeniu zostają. Panel pokazuje urządzenia
z kontem, wersją EmuStart i ostatnim kontaktem; **Unieważnij** odcina jedno urządzenie,
**Nazwa** zmienia nazwę, *Z grami / Bez gier* — pobieranie gier z serwera.

Rejestracje sprzed 0.43 bez konta (parowanie kodem, stary klucz-hasło) są unieważniane przy
starcie serwera — takie urządzenie loguje się ponownie.

## Konta i hasła (0.40)

Panel serwera → **Konta**: każde konto (profil), czy ma hasło, czy jest administratorem
i ile urządzeń. **Ustaw hasło** / **Nowe hasło** — gdy użytkownik zapomniał hasła (działa
tylko w panelu na tym komputerze). Skrót hasła (scrypt) w `accounts.json` w katalogu
serwera. Szczegóły: [Profile graczy](Profile-graczy).

## Adresy serwera i szyfrowanie

Urządzenia łączą się z serwerem **tylko szyfrowanie** (od 0.43): port szyfrowany `8741`
(sieć lokalna, Tailscale), port publiczny `8742` i tunel. Zwykły port `8740` obsługuje API
tylko z samego serwera (panel). Certyfikat serwer tworzy sam przy pierwszym starcie
(`server\tls-cert.pem`, ważny 20 lat); urządzenia porównują jego odcisk z zapamiętanym przy
logowaniu („przypięcie”) — podszycie się pod serwer wymagałoby jego klucza prywatnego.
Odpowiedź, która nie przyszła połączeniem zaufanym, nie zmienia zapamiętanego odcisku ani
adresów.

Serwer podaje urządzeniom listę swoich adresów (sieć lokalna, Tailscale, port publiczny,
tunel). Urządzenie sprawdza je równolegle i wybiera najlepszy działający: najpierw sieć
lokalna, potem Tailscale, port publiczny, na końcu tunel. Wybór jest odświeżany co 5 minut
i po błędzie połączenia — telefon przechodzący z domowego Wi-Fi na sieć komórkową sam
przełącza się na kolejny adres. Komputer trzyma nauczone adresy w `data\serwer-adresy.json`,
telefon — w ustawieniach aplikacji.

## Dostęp z internetu (0.35)

Panel serwera → **Dostęp z internetu** → **Włącz**. Serwer otwiera osobny port szyfrowany
(`8742`) i prosi router (UPnP) o przekierowanie go; odczytuje adres zewnętrzny i podaje go
urządzeniom jako adres „publiczny”. Urządzenie używa go tylko wtedy, gdy sieć lokalna
i Tailscale nie odpowiadają — np. telefon na danych komórkowych bez Tailscale.

- Port publiczny przyjmuje tylko API urządzeń i grafiki, **zawsze z tokenem urządzenia**
  (logowanie i podpis — bez tokenu), bez panelu i interfejsu.
- Seria złych kluczy z jednego adresu = blokada tego adresu na 30 minut; najwyżej 32
  połączenia naraz z jednego adresu.
- Przekierowanie jest odnawiane co 20 minut i wygasa samo po wyłączeniu serwera.
- **CGNAT**: gdy router ma adres za NAT-em operatora, panel to pokazuje — z internetu nie
  da się wtedy dojść do serwera (zostaje Tailscale; od 0.36 tunel).
- **Osiągalność**: panel pokazuje „działa”, gdy serwer połączył się sam ze sobą przez adres
  zewnętrzny albo gdy przyszło połączenie z internetu. Najprościej: otwórz EmuStart na
  telefonie na danych komórkowych z wyłączonym Tailscale.

## Tunel Cloudflare (0.36)

Gdy port publiczny nie wystarcza (np. sieć, w której telefon jest, blokuje nietypowe porty),
urządzenia mogą łączyć się przez **Cloudflare Tunnel** na własnej domenie:

1. Cloudflare Zero Trust → tunel na komputerze z serwerem (`cloudflared`) → *Published
   application*: np. `emustart.twojadomena.org`, usługa **`https://localhost:8742`**
   (port publiczny), *Additional application settings → TLS → Disable TLS certificate
   verification* — włączone (połączenie `cloudflared` → serwer nie wychodzi poza komputer).
   Nie kieruj tunelu na 8740 (zwykły port — ruch z tunelu wygląda na lokalny).
2. Panel serwera → *Dostęp z internetu* → **Adres tunelu** → *Zapisz*. Panel sprawdza, czy
   tunel działa.
3. Urządzenia dostają adres tunelu razem z innymi adresami serwera i biorą go **na końcu**
   (sieć lokalna → Tailscale → port publiczny → tunel) — gry idą bezpośrednio, gdy się da.
   Nowe urządzenie loguje się, wpisując sam adres tunelu (**Zaloguj (adres serwera)**).

Przez tunel obowiązują te same zasady co na porcie publicznym (tylko z tokenem urządzenia);
blokada za złe klucze działa po prawdziwym adresie klienta (nagłówek
`CF-Connecting-IP`). W Cloudflare nie włączaj *Bot Fight Mode* ani wyzwań dla tej
subdomeny — aplikacje nie są przeglądarkami.

## Komputer bez folderów z grami — wszystko z serwera (0.38)

Komputer zalogowany na serwerze, który nie ma ustawionych
folderów z grami, **niczego nie skanuje** — bierze wszystko od serwera:

- **biblioteka** — systemy i gry z serwera, przy starcie i z menu Start → *Odśwież
  bibliotekę z serwera* (pobierane są tylko systemy zmienione na serwerze);
- **gry** — uruchamiane w całości przez serwer (pobieranie wieloma strumieniami, pamięć
  podręczna, granie w trakcie pobierania jak dotąd);
- **grafiki** — okładki, zrzuty, logo gier i własne logo systemów pobierane z serwera przy
  pierwszym wyświetleniu i zapamiętywane;
- **BIOS-y** — przed grą w RetroArchu brakujące BIOS-y rdzenia z serwera (gdy komputer
  nie ma własnego folderu BIOS-ów);
- **emulator dla systemu** — po wskazaniu folderu emulatorów EmuStart wybiera ten sam
  emulator i rdzeń, którego używa komputer administratora (stany zgodne).

Emulatory trzeba na razie mieć na tym komputerze (Ustawienia → *Emulatory* → folder).
Konto bez uprawnienia do gier (zatwierdzone „tylko zapisy i profil”) nie dostaje biblioteki.

## Katalog serwera (0.32)

Serwer trzyma wszystko poza grami w podkatalogu `server\` obok `EmuStart.exe`: ustawienia
(`config.json`), bibliotekę, grafiki, rejestr urządzeń, a po przeniesieniu — także profile.
Skopiowanie katalogu programu przenosi serwer na inny komputer. Przy pierwszym starcie
serwera 0.32 `config.json` i `data\` z wcześniejszych wersji trafiają do `server\` same.
Zwykły EmuStart uruchomiony na komputerze z serwerem pracuje na tych samych danych.

**Profile w katalogu serwera:** panel → **Folder profili** → **Przenieś profile do katalogu
serwera**. Najpierw plan (ile plików, ile danych), po potwierdzeniu kopiowanie w tle — każdy
plik sprawdzony (rozmiar i SHA-1), dopiero potem serwer przełącza się na nowy folder. Stary
folder na udziale zostaje nietknięty (z plikiem `.emustart-moved`) — jako zapas.

Klienci z ustawionym serwerem zapisują profile **tylko przez serwer** (bez SMB); gdy serwer
chwilowo nie odpowiada — tryb offline, zapisy czekają w kolejce.

## Administracja

Na komputerze z serwerem pełny interfejs EmuStart jest dostępny w przeglądarce:
`http://127.0.0.1:8740/index.html?dev` (ustawienia, skanowanie, grafiki). Interfejs, API
programu i panel odpowiadają tylko połączeniom z tego komputera (decyduje adres połączenia,
nie nagłówek `Host`).

Nie uruchamiaj jednocześnie serwera i zwykłego EmuStart z tego samego folderu.

## API (dla aplikacji)

Każde zapytanie z tokenem sesji w nagłówku `Authorization: Bearer <token>` (tylko
połączeniem szyfrowanym; token z logowania podpisem).

| Adres | Zawartość |
|---|---|
| `POST /v1/enroll/request` | prośba o zalogowanie: `{mode, account, password, name, class, pubkey[, login_code]}` (bez tokenu) |
| `POST /v1/enroll/poll` | stan prośby; po zatwierdzeniu id urządzenia, adresy serwera i odcisk certyfikatu |
| `POST /v1/auth/challenge`, `/v1/auth/token` | logowanie podpisem kluczem urządzenia → token sesji (1 h) |
| `POST /v1/auth/logout` | urządzenie wylogowuje się (unieważnienie) |
| `POST /v1/enroll/pending`, `/v1/enroll/decide` | prośby do zatwierdzenia przez to urządzenie / decyzja |
| `GET /v1/info` | nazwa komputera, wersja, obsługiwane funkcje (`features`) |
| `GET /v1/find?es=<system>&rel=<ścieżka>` | gra po systemie i ścieżce względnej: `id` i pliki (EmuStart na Windows) |
| `GET /v1/systems` | systemy (`es`, `display`, `games`, `logo`, `kind`) |
| `GET /v1/systems/<es>/games` | gry systemu (tytuł, tagi, rozmiar, gatunek, rok, gracze, producent, adresy grafik) |
| `GET /v1/games/<id>` | szczegóły gry, opis, lista plików |
| `GET /v1/games/<id>/file/<ścieżka>` | plik gry, obsługa `Range` |
| `GET /media/...` | grafiki |
| `GET /v1/nas/scan\|list\|stat\|file?p=…` | folder profili: lista plików, zawartość, plik (zstd) |
| `PUT /v1/nas/file?p=…&mtime=…&backup=…` | zapis pliku profilu; poprzednia wersja do kopii zapasowej |
| `DELETE /v1/nas/file?p=…` | usunięcie pliku profilu |

Folder profili serwera: `profiles_nas` z jego ustawień, a gdy tam go nie ma —
`emustart\Profiles` w którymś folderze nadrzędnym folderów z grami (np. udział
`EMU_ROMS` → `EMU_ROMS\emustart\Profiles`, czyli `Z:\emustart\Profiles` na klientach).
Serwer zakłada w nim znacznik `.emustart-root`; klient korzysta z serwera tylko wtedy,
gdy przez SMB widzi ten sam znacznik (albo SMB jest niedostępny).

## Bezpieczeństwo

- Urządzenia logują się podpisem kluczem urządzenia; klucz prywatny nie opuszcza urządzenia.
- Hasła kont: tylko skrót (scrypt, sól) w `accounts.json` poza folderem profili; hasło
  w treści żądania (nigdy w adresie), rosnące opóźnienie po złych próbach.
  Wspólny klucz serwera (`config.json` → `server_token`) działa tylko lokalnie.
- Serwer wydaje wyłącznie pliki gier z biblioteki (ścieżka spoza gry → 404).
- Zapis tylko w folderze profili (ścieżki z `..`, dyskiem albo bezwzględne → 400).
- Urządzenia tylko połączeniem szyfrowanym (przypięty certyfikat albo tunel); nie wystawiaj
  portu 8740 do internetu (z sieci i tak odrzuca API urządzeń).
