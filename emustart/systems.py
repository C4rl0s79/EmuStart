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
    "amiga": _s("Amiga", "AMIGA", "Commodore - Amiga", "lha,adf,hdf,m3u," + _CART, "mixed"),
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
