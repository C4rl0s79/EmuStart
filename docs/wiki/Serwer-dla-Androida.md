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
   i regułę zapory dla portu 8740.
4. Uruchom serwer od razu (albo zrestartuj komputer):
   ```
   schtasks /Run /TN "EmuStart Server"
   ```
5. Klucz serwera (potrzebny w aplikacji na Androida):
   ```
   EmuStart.exe --server-key
   ```

Adres serwera dla telefonu: `http://<adres Tailscale komputera>:8740`.

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
- **Pokaż klucz** — klucz serwera (kopiowany do schowka),
- **Folder logów**.

Gdy serwer nie odpowiada, panel najpierw uruchamia zadanie „EmuStart Server”, a jeśli go
nie ma — serwer na koncie użytkownika. Panel działa tylko na tym komputerze (127.0.0.1).

Po aktualizacji do 0.25.12 uruchom ponownie `EmuStart.exe --install-server` (jako
administrator): zadanie dostaje brak limitu czasu pracy (schtasks domyślnie zatrzymuje
zadanie po 72 h) i ponowne uruchomienie po awarii.

## Urządzenia i parowanie (0.32)

Każdy komputer i telefon ma **własny klucz** — zamiast przepisywać wspólny klucz serwera:

1. Panel serwera → **Sparowane urządzenia** → **Dodaj urządzenie** — kod jednorazowy (ważny
   10 minut) jako tekst i kod QR.
2. **Telefon**: Ustawienia → **Połącz z serwerem (kod QR)** → zeskanuj kod (skaner Google
   Play, bez uprawnienia do aparatu w EmuStart). Bez Google Play: **Połącz kodem** i wpisz kod.
3. **Komputer**: Ustawienia → Serwer EmuStart → **Połącz kodem** → wpisz kod (albo wklej link
   `emustart://pair?…` z kodu QR).

**Nowy komputer bez przepisywania kodu (0.33):** na nowym komputerze Ustawienia → Serwer
EmuStart → **Dołącz do serwera (zatwierdź telefonem)** → adres serwera. Komputer pokazuje kod
i QR; na sparowanym telefonie Ustawienia → **Zatwierdź nowe urządzenie (kod QR)** → zeskanuj →
**Zatwierdź** (telefon pokazuje nazwę i adres nowego urządzenia). Można też zatwierdzić kodem
na innym sparowanym komputerze albo w panelu serwera (**Proszą o dołączenie**). Kod ważny
10 minut, jednorazowy; zatwierdzać może tylko sparowane urządzenie.

Panel pokazuje urządzenia z rodzajem, wersją EmuStart i ostatnim kontaktem; **Unieważnij**
odcina tylko to jedno urządzenie, **Nazwa** zmienia nazwę. Serwer trzyma tylko skróty kluczy
(SHA-256). Wspólny klucz serwera działa dalej — dla urządzeń sprzed parowania.

## Adresy serwera i szyfrowanie (0.34)

Serwer ma dwa porty: zwykły (`8740`) i **szyfrowany** (`8741`, HTTPS). Certyfikat serwer
tworzy sam przy pierwszym starcie (`server\tls-cert.pem`); urządzenia nie sprawdzają go
w urzędzie certyfikacji, tylko porównują jego odcisk z zapamiętanym przy parowaniu
(„przypięcie”) — podszycie się pod serwer wymagałoby jego klucza prywatnego.

Serwer podaje urządzeniom listę swoich adresów (sieć lokalna, Tailscale; zwykły port
i szyfrowany). Urządzenie sprawdza je równolegle i wybiera najlepszy działający: najpierw
sieć lokalna, potem Tailscale, na końcu adres publiczny; w obrębie rodzaju zwykły port przed
szyfrowanym. Wybór jest odświeżany co 5 minut i po błędzie połączenia — telefon przechodzący
z domowego Wi-Fi na sieć komórkową sam przełącza się na Tailscale.

Komputer trzyma nauczone adresy w `data\serwer-adresy.json`, telefon — w ustawieniach
aplikacji. Port publiczny (otwierany przez UPnP) i dostęp spoza Tailscale — kolejne wersje.

## Dostęp z internetu (0.35)

Panel serwera → **Dostęp z internetu** → **Włącz**. Serwer otwiera osobny port szyfrowany
(`8742`) i prosi router (UPnP) o przekierowanie go; odczytuje adres zewnętrzny i podaje go
urządzeniom jako adres „publiczny”. Urządzenie używa go tylko wtedy, gdy sieć lokalna
i Tailscale nie odpowiadają — np. telefon na danych komórkowych bez Tailscale.

- Port publiczny przyjmuje tylko API urządzeń i grafiki, **zawsze z kluczem urządzenia**;
  bez parowania (to tylko w sieci lokalnej / Tailscale), bez panelu i interfejsu.
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
   Nowe urządzenie można też połączyć, wpisując sam adres tunelu jako adres serwera.

Przez tunel obowiązują te same zasady co na porcie publicznym (tylko z kluczem urządzenia,
bez parowania); blokada za złe klucze działa po prawdziwym adresie klienta (nagłówek
`CF-Connecting-IP`). W Cloudflare nie włączaj *Bot Fight Mode* ani wyzwań dla tej
subdomeny — aplikacje nie są przeglądarkami.

## Nowe urządzenie przez internet, konta i role (0.37)

Nowa instalacja (komputer albo telefon) potrzebuje **tylko adresu serwera** — np. adresu
tunelu `emustart.twojadomena.org`:

1. Ustawienia → **Dołącz przez internet (nowa instalacja)** → adres serwera → **Mam konto**
   albo **Nowe konto** → nazwa konta.
2. Urządzenie tworzy **własny klucz** (Windows: w TPM, a bez TPM — zaszyfrowany DPAPI;
   Android: Android Keystore / StrongBox; klucza prywatnego nie da się skopiować na inny
   sprzęt) i pokazuje **kod weryfikacyjny**, np. `K7Q2-9FMX`.
3. Prośbę widzi zatwierdzający — w aplikacji EmuStart przy jej otwarciu (i co minutę) albo
   w panelu serwera: czas, adres IP, nazwa i rodzaj urządzenia, konto, kod. **Kod musi się
   zgadzać** z kodem na ekranie nowego urządzenia. Zatwierdzenie: *z grami z serwera* albo
   *tylko zapisy i profil*; albo *Odrzuć*.
4. Po zatwierdzeniu urządzenie loguje się samo (podpis kluczem urządzenia → token sesji na
   godzinę, odnawiany) i od razu widzi swoje konto.

Prośba czeka **24 godziny** (można zamknąć aplikację — przy następnym uruchomieniu sprawdzi
stan sama). Do zatwierdzenia nowe urządzenie nie ma dostępu do niczego.

**Kto zatwierdza:**
- **nowe konto** — tylko administrator;
- **Mam konto** — inne urządzenie tego konta; gdy konto nie ma jeszcze żadnego — administrator.

**Role:** *administrator* — zatwierdza wszystko, widzi wszystkie konta (urządzenie
sparowane kodem z panelu serwera = urządzenie zero; rolę nadaje / odbiera panel → *Sparowane
urządzenia*). *Użytkownik* — tylko swoje konto (zapisy, stany, ustawienia), zatwierdza nowe
urządzenia swojego konta; gry z serwera — jeśli zatwierdzający na to pozwolił. Urządzenia
sparowane przed 0.37 działają jak dotąd (pełny dostęp) — przejście na klucze urządzeń: 0.38.

## Komputer bez folderów z grami — wszystko z serwera (0.38)

Komputer połączony z serwerem (np. *Dołącz przez internet*), który nie ma ustawionych
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
`http://127.0.0.1:8740/index.html?dev` (ustawienia, skanowanie, grafiki). Z innych
komputerów interfejs nie jest dostępny — tylko API z kluczem.

Nie uruchamiaj jednocześnie serwera i zwykłego EmuStart z tego samego folderu.

## API (dla aplikacji)

Każde zapytanie z kluczem: nagłówek `Authorization: Bearer <klucz>` albo `?k=<klucz>`.

| Adres | Zawartość |
|---|---|
| `POST /v1/pair` | parowanie: `{code, name, class, version}` → klucz urządzenia (jednorazowy kod z panelu) |
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

- Klucz serwera jest losowy (`config.json` → `server_token`), porównywany w stałym czasie.
- Serwer wydaje wyłącznie pliki gier z biblioteki (ścieżka spoza gry → 404).
- Zapis tylko w folderze profili (ścieżki z `..`, dyskiem albo bezwzględne → 400).
- Połączenie przez Tailscale jest szyfrowane; nie wystawiaj portu 8740 do internetu
  bez Tailscale.
