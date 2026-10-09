# Rozwiązywanie problemów

Najpierw zajrzyj do `logs\emustart.log` obok programu.

## Pad

**Pad przestaje działać po zamknięciu EmuStart / nie działa w innych programach.**
Naprawione w 0.11.0 (EmuStart nie korzysta z Gamepad API i czyta pady tylko, gdy
jest na wierzchu). Jeśli problem wróci, sprawdź po restarcie Windows kolejno
`EmuStart.exe --pady=brak` i `--pady=przegladarka` — tryb zapisuje się w logu.

**Pad PlayStation nie działa** — musi być widoczny jako XInput (Steam Input, DS4Windows).

**Menu w grze się nie otwiera** — A + Y trzeba trzymać 2 s; nie działa przy `--pady=brak`.

## Gra

**Gra się nie uruchamia** — sprawdź emulator systemu (Ustawienia → Systemy) i log.
Archiwum bez pliku gry (np. sama muzyka MSU-1) daje komunikat o brakującym ROM-ie.

**Gra MSU-1 bez muzyki** — emulator/rdzeń musi obsługiwać MSU-1 (np. RetroArch
z rdzeniem bsnes lub Snes9x), a ROM, `.msu` i `.pcm` muszą mieć w archiwum tę samą nazwę.

**„Profil gra teraz na komputerze …”** — ten sam profil gra na innym komputerze.
Blokada starsza niż 12 godzin (np. po awarii) jest ignorowana.

**Gra Amigi (.ipf) się nie uruchamia** — potrzebny `capsimg.dll` w folderze `system`
RetroArcha (EmuStart proponuje pobranie przy pierwszej grze). Gry WHDLoad
i niektóre dyskietki potrzebują Kickstartu — zob.
[Foldery z grami → Amiga](Foldery-z-grami-i-systemy#amiga).

**WHDLoad: „DOS-Error #205 … devs:kickstarts/kick34005.a500”** — brak Kickstartu na
emulowanym dysku WHDLoad. Od 0.17.1 EmuStart kopiuje Kickstarty do
`saves\PUAE\WHDLoad\Devs\Kickstarts` przed startem gry — wystarczy, że są w folderze
`system` RetroArcha albo w folderze BIOS-ów.

## Profile i NAS

**Komunikat o konflikcie save'ów** — ten sam save zmienił się na dwóch komputerach
(gra bez NAS-a). Została nowsza wersja; starsza jest w
`Z:\emustart\Profiles\<profil>\_backup\…\konflikt` (albo w `profiles\<id>\_backup`).

**Folder profilu na NAS-ie ma inną nazwę niż profil** — profil przemianowany po
pierwszej synchronizacji. Opcje profilu (Start) → „Folder na NAS: … → zmień na …”.

## RetroAchievements

**DuckStation prosi o zalogowanie** — od 0.16.3 EmuStart zapisuje token
w formacie DuckStation. Jeśli to się powtarza, zaloguj się raz w DuckStation
i zgłoś problem.

## Grafiki

**Brak miejsca na dysku** — Grafiki i metadane → „Zmniejsz zapisane grafiki”.
Pobieranie samo zatrzymuje się przy mniej niż 2 GB wolnego miejsca.

## Okno EmuStart się nie uruchamia

Błąd „Failed to resolve Python.Runtime.Loader.Initialize … Python.Runtime.dll”: Windows
zablokował pliki rozpakowane z pobranego zipa. W PowerShellu:
```
Get-ChildItem -Recurse '<folder EmuStart>' | Unblock-File
```
Od 0.22.1 EmuStart w takiej sytuacji otwiera interfejs w przeglądarce. Inne przyczyny:
brak .NET Framework 4.8 albo Microsoft Edge WebView2 Runtime.

## Windows Defender

Zobacz [Instalacja](Instalacja-i-pierwsze-uruchomienie#windows-defender).
