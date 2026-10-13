# Kilka urządzeń, jeden serwer

Wszystkie urządzenia — komputery z EmuStart i telefony — korzystają z jednego
[EmuStart Server](EmuStart-Server). Wspólne są: biblioteka, grafiki, opisy, logo systemów
i konta graczy z zapisami. Każde urządzenie ma własną pamięć podręczną gier i lokalną kopię
biblioteki.

## Kolejne urządzenie

1. Zainstaluj EmuStart i zaloguj go do serwera ([logowanie urządzeń](EmuStart-Server#logowanie-urządzeń)).
2. Konta z serwera są od razu na liście.
3. Pytanie **„Kto gra na tym komputerze?”** — wybierz swoje konto albo utwórz nowe.
   Save'y i konto RetroAchievements zastane w emulatorach tego komputera trafią do
   wybranego konta.
4. Przy pierwszej grze save'y, ustawienia i konto RA pobiorą się z serwera.

Emulatory zainstalowane od nowa nie wymagają przenoszenia save'ów — wracają z serwera przy
pierwszej grze. Pusta karta pamięci założona przez świeży emulator trafia do kopii zapasowej
i nie zasłania save'a.

## Co jest na serwerze

```
server\profiles\<konto>\
  save\<emulator>\<folder>\…      save'y i stany
  settings\<emulator>\…           ustawienia emulatorów (bez haseł i tokenów)
  resume\                         stany „wznowienia” (quicksave i wyjdź)
  emustart.json                   wygląd i ustawienia EmuStart konta
  retroachievements.json          konto RA: nazwa + token (bez hasła)
  variants.json                   wybrane wersje gier
  _backup\<czas>-<urządzenie>\    nadpisane wersje plików
  lock                            blokada na czas gry
```

Do tego **kopie całych kont** (ZIP, raz dziennie) — panel serwera → Konta → Pliki i kopie.

## Granie na zmianę

Grasz na komputerze A, potem siadasz do B albo bierzesz telefon — przed grą urządzenie
pobiera nowsze save'y. Wygrywa zawsze nowszy plik; nadpisywany trafia do kopii zapasowej.

## Bez połączenia z serwerem

- Gry pobrane na urządzenie działają, save'y zapisują się lokalnie i wysyłają przy
  następnej okazji.
- Blokada konta nie działa (nie ma gdzie jej zapisać) — jeśli to samo konto zagra wtedy na
  dwóch urządzeniach i zmieni ten sam plik, to **konflikt**: zostaje nowsza wersja, druga
  ląduje w kopii zapasowej (`_backup\…\konflikt`), a EmuStart pokazuje komunikat.
  Szczególnie ważne w PCSX2, gdzie jedna karta pamięci (`Mcd001.ps2`) trzyma save'y
  wszystkich gier.

## Wygląd na różnych ekranach

Edytor wyglądu → **Wygląd zapisywany**: dla konta (taki sam wszędzie) albo dla tego
komputera (np. inny na telewizorze 4K, inny na laptopie).
