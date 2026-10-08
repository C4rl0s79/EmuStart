"""systems — znane systemy, kluczowane nazwą folderu z EmulationStation.

Pola:
  display  – nazwa na ekranie
  plat     – kod platformy do wykrywania emulatorów (zgodny z PyLinks) i logo
  libretro – nazwa systemu w libretro (bazy rdzeni i serwer miniatur)
  exts     – rozszerzenia gier
  kind     – cart | disc | arcade | mixed (decyduje o strategii cache)
"""

from __future__ import annotations

import re

_CART = "zip,7z"
_DISC = "m3u,chd,cue,gdi,cdi,iso,rvz,cso,pbp"


def _s(display, plat, libretro, exts, kind="cart"):
    return {"display": display, "plat": plat, "libretro": libretro,
            "exts": exts, "kind": kind}


SYSTEMS: dict = {
    # ── Sony ──
    "psx": _s("PlayStation", "PS1", "Sony - PlayStation", _DISC, "disc"),
    "ps2": _s("PlayStation 2", "PS2", "Sony - PlayStation 2", _DISC, "disc"),
    "ps3": _s("PlayStation 3", "PS3", "Sony - PlayStation 3", "iso,folder", "disc"),
    "psp": _s("PlayStation Portable", "PSP", "Sony - PlayStation Portable",
              "iso,cso,pbp,chd," + _CART, "disc"),
    "psvita": _s("PlayStation Vita", "PSVITA", "Sony - PlayStation Vita",
                 "vpk,zip,folder", "mixed"),
    # ── Nintendo ──
    "nes": _s("Nintendo Entertainment System", "NES",
              "Nintendo - Nintendo Entertainment System", "nes,unf," + _CART),
    "fds": _s("Famicom Disk System", "NES", "Nintendo - Family Computer Disk System",
              "fds," + _CART),
    "snes": _s("Super Nintendo", "SNES", "Nintendo - Super Nintendo Entertainment System",
               "sfc,smc," + _CART),
    "SNESMSU1": _s("SNES MSU-1", "SNESMSU1", "Nintendo - Super Nintendo Entertainment System",
                   "sfc,smc,zip", "mixed"),
    "satellaview": _s("Satellaview", "SNES", "Nintendo - Satellaview", "bs," + _CART),
    "sufami": _s("Sufami Turbo", "SNES", "Nintendo - Sufami Turbo", "st," + _CART),
    "n64": _s("Nintendo 64", "N64", "Nintendo - Nintendo 64", "z64,n64,v64," + _CART),
    "n64dd": _s("Nintendo 64DD", "N64", "Nintendo - Nintendo 64DD", "ndd," + _CART),
    "gb": _s("Game Boy", "GB", "Nintendo - Game Boy", "gb," + _CART),
    "gbc": _s("Game Boy Color", "GBC", "Nintendo - Game Boy Color", "gbc," + _CART),
    "gba": _s("Game Boy Advance", "GBA", "Nintendo - Game Boy Advance", "gba," + _CART),
    "nds": _s("Nintendo DS", "NDS", "Nintendo - Nintendo DS", "nds," + _CART),
    "gameandwatch": _s("Game & Watch", "GW", "Handheld Electronic Game", "mgw," + _CART),
    "gamecube": _s("GameCube", "GCN", "Nintendo - GameCube", "rvz,iso,gcz,ciso", "disc"),
    "wii": _s("Wii", "WII", "Nintendo - Wii", "rvz,iso,wbfs,gcz", "disc"),
    # ── Sega ──
    "mastersystem": _s("Master System", "SMS", "Sega - Master System - Mark III", "sms," + _CART),
    "megadrive": _s("Mega Drive", "MD", "Sega - Mega Drive - Genesis", "md,gen,bin," + _CART),
    "gamegear": _s("Game Gear", "GG", "Sega - Game Gear", "gg," + _CART),
    "sega32x": _s("Sega 32X", "32X", "Sega - 32X", "32x," + _CART),
    "segacd": _s("Sega CD", "SEGACD", "Sega - Mega-CD - Sega CD", _DISC, "disc"),
    "saturn": _s("Saturn", "SATURN", "Sega - Saturn", _DISC, "disc"),
    "dreamcast": _s("Dreamcast", "DC", "Sega - Dreamcast", _DISC, "disc"),
    # ── NEC ──
    "pcengine": _s("PC Engine", "PCENGINE", "NEC - PC Engine - TurboGrafx 16",
                   "pce," + _CART + "," + _DISC, "mixed"),
    "tg16": _s("TurboGrafx-16", "PCENGINE", "NEC - PC Engine - TurboGrafx 16",
               "pce," + _CART, "cart"),
    "supergrafx": _s("SuperGrafx", "SUPERGRAFX", "NEC - PC Engine SuperGrafx",
                     "sgx,pce," + _CART),
    "pc88": _s("PC-8801", "PC88", "NEC - PC-8001 - PC-8801", "d88,m3u," + _CART, "mixed"),
    "pc98": _s("PC-9801", "PC98", "NEC - PC-98", "hdi,fdi,d88,m3u," + _CART, "mixed"),
    # ── Atari ──
    "atari2600": _s("Atari 2600", "ATARI2600", "Atari - 2600", "a26,bin," + _CART),
    "atari5200": _s("Atari 5200", "ATARI5200", "Atari - 5200", "a52,bin," + _CART),
    "atari7800": _s("Atari 7800", "ATARI7800", "Atari - 7800", "a78,bin," + _CART),
    "atarijaguar": _s("Atari Jaguar", "JAGUAR", "Atari - Jaguar", "j64,jag," + _CART),
    "lynx": _s("Atari Lynx", "LYNX", "Atari - Lynx", "lnx," + _CART),
    # ── inne ──
    "3do": _s("3DO", "3DO", "The 3DO Company - 3DO", _DISC, "disc"),
    "amiga": _s("Amiga", "AMIGA", "Commodore - Amiga", "ipf,adf,adz,dms,hdf,lha,m3u," + _CART, "mixed"),
    # WHDLoad: gry i dema zainstalowane na „dysk twardy” (paczki .lha, np. Retroplay)
    "amigawhdgames": _s("Amiga WHDLoad — gry", "AMIGA", "Commodore - Amiga", "lha,lzh,lzx," + _CART, "mixed"),
    "amigawhddemos": _s("Amiga WHDLoad — dema", "AMIGA", "Commodore - Amiga", "lha,lzh,lzx," + _CART, "mixed"),
    "c64": _s("Commodore 64", "C64", "Commodore - 64", "d64,t64,prg,crt,m3u," + _CART),
    "msx": _s("MSX", "MSX", "Microsoft - MSX", "rom,mx1,dsk," + _CART),
    "msx2": _s("MSX2", "MSX2", "Microsoft - MSX2", "rom,mx2,dsk," + _CART),
    "ngp": _s("Neo Geo Pocket", "NGP", "SNK - Neo Geo Pocket", "ngp," + _CART),
    "ngpc": _s("Neo Geo Pocket Color", "NGP", "SNK - Neo Geo Pocket Color", "ngc," + _CART),
    "wswan": _s("WonderSwan", "WSWAN", "Bandai - WonderSwan", "ws," + _CART),
    "wswanc": _s("WonderSwan Color", "WSWAN", "Bandai - WonderSwan Color", "wsc," + _CART),
    "odyssey2": _s("Odyssey 2", "ODYSSEY2", "Magnavox - Odyssey2", "bin," + _CART),
    "zxspectrum": _s("ZX Spectrum", "ZX", "Sinclair - ZX Spectrum", "tzx,tap,z80,dsk," + _CART),
    "x360": _s("Xbox 360", "X360", "Microsoft - Xbox 360", "iso,xex,zar,folder", "disc"),
    "xbox360": _s("Xbox 360", "X360", "Microsoft - Xbox 360", "iso,xex,zar,folder", "disc"),
    "atari800": _s("Atari 8-bit", "ATARI800", "Atari - 8-bit", "atr,xex,xfd,car,bin," + _CART),
    "atarist": _s("Atari ST", "ATARIST", "Atari - ST", "st,msa,stx,dim," + _CART),
    "colecovision": _s("ColecoVision", "COLECO", "Coleco - ColecoVision", "col,rom," + _CART),
    "intellivision": _s("Intellivision", "INTV", "Mattel - Intellivision", "int,bin,rom," + _CART),
    "vectrex": _s("Vectrex", "VECTREX", "GCE - Vectrex", "vec,gam,bin," + _CART),
    "virtualboy": _s("Virtual Boy", "VB", "Nintendo - Virtual Boy", "vb,vboy," + _CART),
    "pokemini": _s("Pokémon Mini", "POKEMINI", "Nintendo - Pokemon Mini", "min," + _CART),
    "channelf": _s("Channel F", "CHANNELF", "Fairchild - Channel F", "chf,bin," + _CART),
    "supervision": _s("Supervision", "SUPERVISION", "Watara - Supervision", "sv,bin," + _CART),
    "megaduck": _s("Mega Duck", "MEGADUCK", "Welback - Mega Duck", "bin," + _CART),
    "pico": _s("Sega Pico", "PICO", "Sega - PICO", "md,bin," + _CART),
    "arcadia": _s("Arcadia 2001", "ARCADIA", "Emerson - Arcadia 2001", "bin," + _CART),
    "scv": _s("Super Cassette Vision", "SCV", "Epoch - Super Cassette Vision", "bin,0," + _CART),
    "gamecom": _s("Game.com", "GAMECOM", "Tiger - Game.com", "tgc,bin," + _CART),
    "vic20": _s("Commodore VIC-20", "VIC20", "Commodore - VIC-20", "prg,crt,d64,tap," + _CART),
    "plus4": _s("Commodore Plus/4", "PLUS4", "Commodore - Plus-4", "prg,d64,tap," + _CART),
    "gamepock": _s("Game Pocket Computer", "GAMEPOCK", "Epoch - Game Pocket Computer", "bin," + _CART),
    # ── arcade ──
    "fbneo": _s("FinalBurn Neo", "FBNEO", "FBNeo - Arcade Games", "zip,7z", "arcade"),
    "mame": _s("MAME", "MAME", "MAME", "zip,7z", "arcade"),
    "arcade": _s("Arcade", "MAME", "MAME", "zip,7z", "arcade"),
    "neogeo": _s("Neo Geo", "NEOGEO", "SNK - Neo Geo", "zip,7z", "arcade"),
}

# rozszerzenia, które nigdy nie są grą (pliki towarzyszące w folderach ROM)
JUNK_EXTS = {"txt", "nfo", "jpg", "jpeg", "png", "gif", "xml", "dat", "db",
             "ini", "cfg", "sav", "srm", "state", "lnk", "url", "md", "pdf",
             "sbi", "bak", "part", "tmp", "ds_store", "sub", "bin", "img"}


def info(es_name: str) -> dict:
    """Opis systemu; nieznany folder dostaje rozsądne domyślne wartości."""
    hit = SYSTEMS.get(es_name) or SYSTEMS.get(es_name.lower())
    if hit:
        return dict(hit, es=es_name)
    return {"es": es_name, "display": es_name, "plat": _plat_guess(es_name),
            "libretro": es_name, "exts": "", "kind": "mixed"}


def _plat_guess(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", name.upper())[:16]


def ext_set(sysinfo: dict) -> set:
    return {e.strip().lower() for e in (sysinfo.get("exts") or "").split(",") if e.strip()}


# ── miniatury libretro ──
LIBRETRO_THUMBS = "https://thumbnails.libretro.com"
_THUMB_BAD = re.compile(r'[&*/:`<>?\\|"]')


def thumb_name(title: str) -> str:
    """Nazwa pliku miniatury wg reguł libretro (znaki specjalne → '_')."""
    return _THUMB_BAD.sub("_", title)


# ── nazwy folderów No-Intro / Redump ──
# Kolekcje DAT-owe nazywają foldery pełną nazwą systemu, często z dopiskiem
# formatu: „Atari - Atari 7800 (BIN)”, „Nintendo - Wii - NKit RVZ [zstd-19-128k]”.
# Klucz: nazwa po zdjęciu dopisku w [] i „ - NKit …”; wartość: folder ES.
FOLDER_ALIASES = {
    # WHDLoad: osobno gry i dema (także jako podfoldery Games/Demos folderu WHDLoad)
    "WHDLoad Games": "amigawhdgames", "Amiga WHDLoad Games": "amigawhdgames",
    "Commodore - Amiga - WHDLoad Games": "amigawhdgames", "Commodore Amiga - WHDLoad - Games": "amigawhdgames",
    "amiga-whdload-games": "amigawhdgames", "whdload_games": "amigawhdgames",
    "WHDLoad Demos": "amigawhddemos", "Amiga WHDLoad Demos": "amigawhddemos",
    "Commodore - Amiga - WHDLoad Demos": "amigawhddemos", "Commodore Amiga - WHDLoad - Demos": "amigawhddemos",
    "amiga-whdload-demos": "amigawhddemos", "whdload_demos": "amigawhddemos",
    "3DO Interactive Multiplayer": "3do", "Panasonic - 3DO Interactive Multiplayer": "3do",
    "Microsoft - Xbox 360": "x360", "Nintendo - GameCube": "gamecube", "Nintendo - Wii": "wii",
    "Sega - Dreamcast": "dreamcast", "Sega - Mega CD & Sega CD": "segacd", "Sega - Saturn": "saturn",
    "Sony - PlayStation": "psx", "Sony - PlayStation 2": "ps2", "Sony - PlayStation 3": "ps3",
    "Sony - PlayStation Portable": "psp", "Sony - PlayStation Portable (PSN) (Decrypted)": "psp",
    "Sony - PlayStation Portable (PSN) (Minis) (Decrypted)": "psp",
    "Atari - Atari 2600": "atari2600", "Atari - Atari 5200": "atari5200",
    "Atari - Atari 7800 (BIN)": "atari7800", "Atari - Atari Jaguar (J64)": "atarijaguar",
    "Atari - Atari Lynx (LYX)": "lynx", "Atari - 8-bit Family": "atari800", "Atari - Atari ST": "atarist",
    "Bandai - WonderSwan": "wswan", "Bandai - WonderSwan Color": "wswanc",
    "Coleco - ColecoVision": "colecovision", "Commodore - Amiga": "amiga",
    "Commodore - Commodore 64": "c64", "Commodore - VIC-20": "vic20", "Commodore - Plus-4": "plus4",
    "Emerson - Arcadia 2001": "arcadia", "Epoch - Super Cassette Vision": "scv",
    "Epoch - Game Pocket Computer": "gamepock", "Fairchild - Channel F": "channelf",
    "FinalBurn Neo - Arcade Games": "fbneo", "GCE - Vectrex": "vectrex",
    "Magnavox - Odyssey 2": "odyssey2", "Mattel - Intellivision": "intellivision",
    "Microsoft - MSX": "msx", "Microsoft - MSX2": "msx2",
    "NEC - PC Engine - TurboGrafx-16": "pcengine", "NEC - PC Engine SuperGrafx": "supergrafx",
    "NEC - PC-98": "pc98", "Nintendo - Family Computer Disk System (FDS)": "fds",
    "Nintendo - Game & Watch": "gameandwatch", "Nintendo - Game Boy": "gb",
    "Nintendo - Game Boy Advance": "gba", "Nintendo - Game Boy Color": "gbc",
    "Nintendo - Nintendo 64 (BigEndian)": "n64", "Nintendo - Nintendo 64DD": "n64dd",
    "Nintendo - Nintendo DS (Decrypted)": "nds",
    "Nintendo - Nintendo Entertainment System (Headered)": "nes",
    "Nintendo - Pokemon Mini": "pokemini", "Nintendo - Satellaview": "satellaview",
    "Nintendo - Sufami Turbo": "sufami", "Nintendo - Super Nintendo Entertainment System": "snes",
    "Nintendo - Virtual Boy": "virtualboy", "SNESMSU1": "SNESMSU1",
    "SNK - NeoGeo Pocket": "ngp", "SNK - NeoGeo Pocket Color": "ngpc",
    "Sega - 32X": "sega32x", "Sega - Game Gear": "gamegear",
    "Sega - Master System - Mark III": "mastersystem", "Sega - Mega Drive - Genesis": "megadrive",
    "Sega - PICO": "pico", "Sinclair - ZX Spectrum +3": "zxspectrum",
    "Tiger - Game.com": "gamecom", "Watara - Supervision": "supervision",
    "Welback - Mega Duck": "megaduck",
}
_ALIAS_NORM = {}


def _fold(name: str) -> str:
    n = re.sub(r"\s*\[[^\]]*\]", "", name)               # [zstd-19-128k]
    n = re.sub(r"\s+-\s+NKit.*$", "", n, flags=re.I)       # - NKit RVZ
    n = re.sub(r"\s*\((Retool|1G1R)\)", "", n, flags=re.I)
    return re.sub(r"[^a-z0-9+&]+", "", n.lower())


def match_folder(name: str) -> str | None:
    """Folder kolekcji → klucz systemu (folder ES) albo None (nieznany/archiwalny).

    Kolejno: nazwa ES („psx”), alias No-Intro/Redump, nazwa systemu libretro."""
    if name in SYSTEMS:
        return name
    if name.lower() in SYSTEMS:
        return name.lower()
    if not _ALIAS_NORM:
        for k, v in FOLDER_ALIASES.items():
            _ALIAS_NORM[_fold(k)] = v
        for es, info_ in SYSTEMS.items():
            _ALIAS_NORM.setdefault(_fold(info_["libretro"]), es)
    return _ALIAS_NORM.get(_fold(name))


WHD_SYSTEMS = ("amigawhdgames", "amigawhddemos")
_WHD_TAGS = {"aga": "AGA", "ecs": "ECS", "ocs": "OCS", "cd32": "CD32", "cdtv": "CDTV", "ntsc": "NTSC",
             "pal": "PAL", "fast": "Fast", "slow": "Slow", "chip": "Chip", "files": "Files",
             "image": "Image", "lowmem": "LowMem", "demo": "Demo", "preview": "Preview",
             "beta": "Beta", "alt": "Alt", "music": "Music", "2disk": "2 Disk", "1disk": "1 Disk"}
_WHD_LANGS = {"de", "fr", "it", "es", "pl", "se", "dk", "fi", "nl", "cz", "gr", "hu", "no", "pt", "en"}


def _split_camel(s: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Z][a-z])", " ", s)
    return re.sub(r"\s+", " ", s.replace("&", " & ")).strip()


def whd_title(stem: str, demo: bool = False) -> tuple:
    """Nazwa paczki WHDLoad → (tytuł, oznaczenia):
    „1869_v1.0_De_AGA_1653” → („1869”, „(v1.0) (De) (AGA)”),
    „AlienBreedSE_v1.4” → („Alien Breed SE”, „(v1.4)”),
    „242_v1.2_Fairlight&VirtualDreams” (demo) → („242”, „(v1.2) (Fairlight & Virtual Dreams)”)."""
    parts = stem.split("_")
    title, tags = _split_camel(parts[0]), []
    for p in parts[1:]:
        low = p.lower()
        if re.fullmatch(r"v\d[\d.]*[a-z]?", low):
            tags.append(p)
        elif re.fullmatch(r"\d{3,5}", p):
            continue                      # numer paczki w zestawie (np. 1653)
        elif low in _WHD_TAGS:
            tags.append(_WHD_TAGS[low])
        elif low in _WHD_LANGS:
            tags.append(p[:1].upper() + p[1:].lower())
        elif demo:
            tags.append(_split_camel(p))  # grupa demoscenowa
        else:
            tags.append(_split_camel(p))
    return title, " ".join(f"({t})" for t in tags)


# Komputery: gry sterowane klawiaturą (Shift, F1, Esc, strzałki…) — w RetroArchu
# klawiatura idzie wtedy w całości do emulowanego komputera (launcher/ingame).
COMPUTER_PLATS = {"AMIGA", "C64", "VIC20", "PLUS4", "MSX", "MSX2", "ATARIST", "ATARI800",
                  "ZX", "PC88", "PC98"}
