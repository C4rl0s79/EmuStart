# EmuStart

**Frontend do gier na emulatorach dla Windows, sterowany padem, w stylu EmulationStation.**
Zbudowany pod kolekcję ROM-ów na NAS-ie: biblioteka i grafiki leżą lokalnie, gry
na serwerze, a ostatnio grane trafiają do lokalnej pamięci podręcznej. Działa tak
samo w domu (LAN) i zdalnie (np. przez Tailscale), na kilku komputerach naraz.

📖 **Pełna dokumentacja: [Wiki](../../wiki)** · 📦 **Pobieranie: [Releases](../../releases)** · 📝 **Zmiany: [CHANGELOG.md](CHANGELOG.md)**

## Co potrafi

- **Karuzela systemów i lista gier** z okładkami, zrzutami, logo tytułów (Clear Logo),
  opisami i metadanymi (LaunchBox, baza RetroArcha, Wikipedia, IGDB…).
- **Gry prosto z NAS-a**: w LAN gra płytowa startuje od razu, zdalnie najpierw się
  pobiera (z postępem i czasem do końca). Ostatnie gry + przypięte trzymane lokalnie.
- **Pobieranie emulatorów jednym przyciskiem** (samodzielne albo RetroArch + rdzeń).
- **Menu w grze** (A + Y przez 2 s): zapis/wczytanie stanu, „quicksave i wyjdź”.
- **Profile graczy**: osobne save'y, ustawienia emulatorów i EmuStart, konto
  RetroAchievements — zsynchronizowane przez NAS na wszystkich komputerach.
- **Kolejność padów** niezależna od tego, jak wykrył je Windows.
- **Edytor wyglądu** na żywo: wielkość logo, czcionek, układ, kolory.
- **Filtry i ukrywanie** wersji Beta/Proto/Demo/pirackich, ujednolicone gatunki.
- **Aplikacja na Androida** (ten sam interfejs, pad) i **tryb serwera** na komputerze
  z grami — zob. [Aplikacja na Androida](../../wiki/Aplikacja-na-Androida).

Obsługiwane wprost (zapisy, stany, pady, RetroAchievements): **RetroArch, DuckStation,
PCSX2**; zapisy i stany także Dolphin i PPSSPP, zapisy RPCS3. Pozostałe emulatory
uruchamia jak zwykły frontend.

## Szybki start

1. Pobierz `EmuStart-<wersja>-win64.zip` z [Releases](../../releases) i rozpakuj
   w dowolne miejsce, np. `D:\emustart`.
2. Uruchom `EmuStart.exe` (wymaga Microsoft Edge WebView2 Runtime — jest w Windows 10/11).
3. Przy pierwszym starcie otworzą się **Ustawienia**:
   - dodaj **foldery z grami** (np. `Z:\ROMS\No-Intro`, `Z:\ROMS\REDUMP`) —
     podfoldery rozpoznawane są po nazwach EmulationStation, No-Intro/Redump i libretro,
   - wskaż **folder emulatorów** (np. `D:\emu\emulatory`),
   - wybierz **Wykryj emulatory i skanuj**, a potem **Pobierz brakujące emulatory**.
4. Wybierz, **kto gra na tym komputerze** (profil), i graj.

Szczegóły: [Instalacja i pierwsze uruchomienie](../../wiki/Instalacja-i-pierwsze-uruchomienie).

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
| **A + Y przez 2 s** (w grze) | — | menu w grze |

Pełna tabela: [Sterowanie](../../wiki/Sterowanie).

## Dane i prywatność

Wszystko, co dotyczy użytkownika (`config.json`, `data\`, `cache\`, `profiles\`,
`logs\`), powstaje obok programu i **nie jest częścią repozytorium**. Klucze API
są szyfrowane (TPM/DPAPI), hasło RetroAchievements nie jest nigdzie zapisywane,
a kopie ustawień emulatorów na NAS-ie nie zawierają haseł ani tokenów. Więcej:
[Bezpieczeństwo i prywatność](../../wiki/Bezpieczeństwo-i-prywatność).

## Uruchomienie ze źródeł

```bash
pip install -r requirements.txt
python main.py              # pełny ekran (wg ustawień)
python main.py --window     # w oknie
python main.py --debug      # w oknie + DevTools
python -m pytest -q tests   # testy
powershell -ExecutionPolicy Bypass -File build.ps1   # → dist\EmuStart-<wersja>-win64.zip
```

Program jest dystrybuowany jako katalog, nie jeden exe — jednoplikowa wersja
PyInstallera była błędnie oznaczana przez Windows Defender. Więcej:
[Budowa ze źródeł](../../wiki/Budowa-ze-źródeł).
