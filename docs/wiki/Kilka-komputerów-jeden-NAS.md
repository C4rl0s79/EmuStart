# Kilka komputerów, jeden NAS

EmuStart może działać na kilku komputerach korzystających z tych samych gier
i profili na NAS-ie. Każdy komputer ma własną bibliotekę, grafiki i pamięć
podręczną (lokalnie), a wspólne są profile.

## Pierwsze uruchomienie na kolejnym komputerze

1. Zainstaluj EmuStart, ustaw te same foldery z grami (np. `Z:\ROMS\…`).
2. EmuStart sam dodaje na listę profile znalezione w `Z:\emustart\Profiles`.
3. Pytanie **„Kto gra na tym komputerze?”** — wybierz swój profil albo utwórz nowy.
   Save'y i konto RetroAchievements zastane w emulatorach tego komputera trafią
   do wybranego profilu (a nie do profilu kogoś innego z NAS-a).
4. Przy pierwszej grze save'y, ustawienia i konto RA profilu pobiorą się z NAS-a.

Emulatory zainstalowane od nowa nie wymagają żadnego przenoszenia save'ów —
wracają z NAS-a przy pierwszej grze. Pusta karta pamięci założona przez świeży
emulator trafia do kopii zapasowej i nie zasłania save'a.

## Co jest na NAS-ie

```
Z:\emustart\Profiles\<profil>\
  save\<emulator>\<folder>\…      save'y i stany
  settings\<emulator>\…           ustawienia emulatorów (bez haseł i tokenów)
  resume\                         stany „wznowienia” (quicksave i wyjdź)
  emustart.json                   wygląd i ustawienia EmuStart profilu
  retroachievements.json          konto RA: nazwa + token (bez hasła)
  _backup\<czas>-<komputer>\      nadpisane wersje plików (10 ostatnich)
  lock                            blokada na czas gry
```

## Granie na zmianę

Grasz na komputerze A, potem siadasz do B — przed grą B pobiera nowsze save'y.
Wygrywa zawsze nowszy plik; nadpisywany trafia do kopii zapasowej.

## Bez dostępu do NAS-a

- Gra działa normalnie (z pamięci podręcznej), save'y zapisują się lokalnie
  i wysyłają przy następnej okazji.
- Blokada profilu nie działa (nie ma gdzie jej zapisać) — jeśli ten sam profil
  zagra wtedy na dwóch komputerach i zmieni ten sam plik, to **konflikt**: zostaje
  nowsza wersja, druga ląduje w kopii zapasowej (`_backup\…\konflikt`), a EmuStart
  pokazuje komunikat. Szczególnie ważne w PCSX2, gdzie jedna karta pamięci
  (`Mcd001.ps2`) trzyma save'y wszystkich gier.

## Wygląd na różnych ekranach

Edytor wyglądu → **Wygląd zapisywany**: dla profilu (taki sam wszędzie) albo dla
tego komputera (np. inny na telewizorze 4K, inny na laptopie).
