# EmuStart

**Frontend do gier na emulatorach sterowany padem, w stylu EmulationStation — dla Windows
i Androida.** Kolekcję gier, grafiki, opisy, konta graczy i ich zapisy trzyma
**EmuStart Server** na komputerze z grami. EmuStart na PC, aplikacja na telefonie
i [Lumen](https://github.com/C4rl0s79/Launcher) biorą wszystko od serwera, pobierają gry na
urządzenie i synchronizują zapisy — zaczynasz na PC, kończysz na telefonie. Działa tak samo
w domu i zdalnie (Tailscale, port publiczny, tunel Cloudflare).

📖 **Pełna dokumentacja: [Wiki](../../wiki)** · 📦 **Pobieranie: [Releases](../../releases)** · 📝 **Zmiany: [CHANGELOG.md](CHANGELOG.md)**

## Programy

| Program | Gdzie | Plik w [Releases](../../releases) |
|---|---|---|
| **EmuStart Server** | komputer z grami (Windows) — biblioteka, grafiki, konta, zapisy, emulatory, panel | `EmuStart-Server-<wersja>-win64.zip` |
| **EmuStart** | komputer, na którym się gra (Windows 10/11) | `EmuStart-<wersja>-win64.zip` |
| **EmuStart na Androida** | telefon, tablet, TV | `EmuStart-<wersja>-android.apk` |
| **Lumen** | launcher na kanapę: Steam + gry EmuStart w jednej bibliotece | [C4rl0s79/Launcher](https://github.com/C4rl0s79/Launcher/releases) |

Każdy plik wydania ma podpis (`.sig`); programy aktualizują się same i instalują tylko
wydanie z poprawnym podpisem. Zaproszeni gracze pobierają programy prosto ze
[strony serwera](../../wiki/EmuStart-Server#strona-serwera-i-zaproszenia).

## Co potrafi

- **Karuzela systemów i lista gier** z okładkami, zrzutami, logo tytułów (Clear Logo),
  opisami i metadanymi (LaunchBox, baza RetroArcha, Wikipedia, IGDB…). Grafiki i opisy
  ustawia serwer — zmiana dociera do wszystkich urządzeń.
- **Gry z serwera**: pobierane na urządzenie (z postępem; przerwane pobieranie wznawia się),
  ostatnio grane i przypięte zostają lokalnie i działają bez serwera. Gry wielopłytowe jako
  jedna pozycja, tłumaczenia jako wersje gier.
- **Emulatory z serwera**: serwer trzyma emulatory i wszystkie rdzenie RetroArcha,
  urządzenia instalują i aktualizują je same; wymagane ustawienia emulatorów ustala serwer.
  Aktualizacja PCSX2 czeka na zgodę administratora, gdy stany PS2 są wspólne z telefonem.
- **Zapisy jak w Steam Cloud**: zapisy w grze, stany i karty pamięci synchronizują się
  z serwerem przed grą i po niej; rozbieżne zmiany z dwóch urządzeń rozstrzyga gracz,
  poprzednie wersje zostają na serwerze. Jedna gra naraz na profil (blokada na serwerze).
- **Sloty zapisów i stany**: 4 osobne „przejścia” każdej gry, „Wczytaj zapis” z podglądem
  stanu, zmiana numeru, kopiowanie i przenoszenie stanów między slotami, kosz.
- **Skróty w grze** (przytrzymany LB + A menu, Y/X zapis, ↑/↓ slot, RB przyspieszenie, R3 zrzut, L3 nagrywanie) i **menu w grze**: zapis / wczytanie stanu, „quicksave i wyjdź”, reset
  gry, shader ekranu.
- **Konta graczy** na serwerze: zapisy, ustawienia emulatorów (z historią zmian), wygląd
  i konto RetroAchievements — te same na każdym urządzeniu konta.
- **Kolejność padów** niezależna od tego, jak wykrył je Windows.
- **Edytor wyglądu** na żywo: wielkość logo, czcionek, układ, kolory.
- **Filtry i ukrywanie** wersji Beta/Proto/Demo/pirackich, ujednolicone gatunki.

Obsługiwane wprost (zapisy, stany, pady, RetroAchievements): **RetroArch, DuckStation,
PCSX2**; zapisy i stany także Dolphin i PPSSPP, zapisy RPCS3; na telefonie RetroArch i ArmSX2.
Pozostałe emulatory uruchamia jak zwykły frontend.

## Szybki start

1. **Serwer** — na komputerze z grami pobierz `EmuStart-Server-<wersja>-win64.zip`,
   rozpakuj (np. `E:\EmuStart`) i jako administrator uruchom `EmuStartServer.exe --install`.
   W panelu serwera dodaj foldery z grami (Konfiguracja → Foldery) — podfoldery rozpoznawane
   są po nazwach EmulationStation, No-Intro/Redump i libretro. Szczegóły:
   [EmuStart Server](../../wiki/EmuStart-Server).
2. **Gracze** — panel → Urządzenia i konta → **Zaproszenia**: adres Google i konto → link
   do strony serwera. Zaproszony loguje się kontem Google, pobiera programy i bierze **kod
   logowania**. Bez strony serwera: panel → **Dodaj urządzenie** (kod dla wybranego konta).
3. **EmuStart na PC** — rozpakuj `EmuStart-<wersja>-win64.zip` (np. `D:\emustart`), uruchom
   `EmuStart.exe` (wymaga Microsoft Edge WebView2 — jest w Windows 10/11) → **Zaloguj** →
   wklej link ze strony serwera albo wpisz adres serwera i kod. Biblioteka i emulatory
   przychodzą z serwera.
4. **Telefon** — zainstaluj APK → Ustawienia → **Zaloguj (kod QR)** → zeskanuj kod ze strony
   serwera.

Szczegóły: [Instalacja i pierwsze uruchomienie](../../wiki/Instalacja-i-pierwsze-uruchomienie),
[Aplikacja na Androida](../../wiki/Aplikacja-na-Androida).

## Sterowanie (skrót)

| Pad | Klawiatura | Działanie |
|---|---|---|
| D-pad / gałka | strzałki | nawigacja |
| A | Enter | wybierz / graj · **przytrzymaj**: opcje gry lub systemu |
| B | Esc | wstecz |
| X | X | filtry listy gier |
| Y | Y | przypnij grę (zostaje lokalnie) |
| LB / RB · LT / RT | PgUp / PgDn · Home / End | strona · litera |
| Start | F2 / Tab | menu |
| **LB + A** (w grze) | — | menu w grze (własne menu emulatora albo EmuStart) |

Pełna tabela: [Sterowanie](../../wiki/Sterowanie).

## Bezpieczeństwo i dane

- Urządzenie łączy się z serwerem tylko szyfrowanym połączeniem i loguje **kluczem
  urządzenia** (TPM / DPAPI na Windows, Android Keystore) — klucz prywatny nie opuszcza
  urządzenia. Nowe konto powstaje tylko z zaproszenia albo w panelu serwera; hasło konta
  jest opcjonalne, serwer trzyma tylko jego skrót.
- Dane użytkownika (`config.json`, `data\`, `cache\`, `profiles\`, `logs\`; na serwerze
  `server\`) powstają obok programu i **nie są częścią repozytorium**. Klucze API są
  szyfrowane, hasło RetroAchievements nie jest nigdzie zapisywane, a kopie ustawień
  emulatorów nie zawierają haseł ani tokenów.

Więcej: [Bezpieczeństwo i prywatność](../../wiki/Bezpieczeństwo-i-prywatność).

## Uruchomienie ze źródeł

```bash
pip install -r requirements.txt
python main.py              # EmuStart: pełny ekran (wg ustawień)
python main.py --window     # w oknie
python main.py --debug      # w oknie + DevTools
python server_main.py       # EmuStart Server: panel (uruchamia serwer, gdy nie działa)
python -m pytest -q tests   # testy
powershell -ExecutionPolicy Bypass -File build.ps1   # → dist\EmuStart-<wersja>-win64.zip i dist\EmuStart-Server-<wersja>-win64.zip
```

Aplikacja na Androida: `android/` (Gradle; APK buduje CI). Programy są dystrybuowane jako
katalogi, nie pojedyncze exe — jednoplikowa wersja PyInstallera była błędnie oznaczana przez
Windows Defender. Więcej: [Budowa ze źródeł](../../wiki/Budowa-ze-źródeł).
