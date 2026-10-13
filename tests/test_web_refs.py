"""Interfejs (web/*.js) nie woła funkcji, których nie ma — po podziale serwer/klient zniknęły
saveSetting, changeSystem i startEdit, a Ustawienia kończyły się „Błąd interfejsu”."""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
# skrypty jednej strony (index.html: app.js + options.js + look.js; server.html: server.js)
PAGES = {"index": ["app.js", "options.js", "look.js"], "server": ["server.js"]}
BROWSER = set("""setTimeout setInterval clearInterval clearTimeout requestAnimationFrame cancelAnimationFrame
parseInt parseFloat isNaN isFinite Number String Boolean Array Object Promise JSON Math Date Set Map WeakMap
fetch encodeURIComponent decodeURIComponent confirm alert prompt getComputedStyle structuredClone atob btoa
URL URLSearchParams Image Error Event CustomEvent Proxy CSS queueMicrotask""".split())
KEYWORDS = set("if for while switch catch function return typeof await new async super import".split())


def _code(src: str) -> str:
    """Kod bez komentarzy i treści napisów (wyrażenia ${…} w szablonach zostają, też zagnieżdżone)."""
    out: list = []
    _scan(src, 0, out, top=True)
    return "".join(out)


def _scan(src: str, i: int, out: list, top: bool = False) -> int:
    """Kod od i do zamykającej klamry wyrażenia ${…} (albo do końca pliku)."""
    n, depth = len(src), 0
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            i = src.find("*/", i + 2) + 2
        elif c == "/" and re.search(r"(^|[(,=:\[!&|?{};]|return)\s*$", "".join(out[-12:])):
            j, cls = i + 1, False                      # literał wyrażenia regularnego, np. /[&<>"]/g
            while j < n and (src[j] != "/" or cls):
                if src[j] == "\\":
                    j += 1
                elif src[j] in "[]":
                    cls = src[j] == "["
                j += 1
            out.append("/r/")
            i = j + 1
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append('""')
            i = j + 1
        elif c == "`":
            i += 1
            while i < n and src[i] != "`":
                if src[i] == "\\":
                    i += 2
                elif src.startswith("${", i):
                    out.append(" (")
                    i = _scan(src, i + 2, out)
                    out.append(") ")
                else:
                    i += 1
            i += 1
        elif c == "{":
            depth += 1
            out.append(c)
            i += 1
        elif c == "}":
            if depth == 0 and not top:
                return i + 1
            depth -= 1
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return i


def test_web_calls_only_defined_functions():
    for page, files in PAGES.items():
        code = "\n".join(_code((WEB / f).read_text(encoding="utf-8")) for f in files)
        defined = set(re.findall(r"function\s*\*?\s*([A-Za-z_$][\w$]*)", code))
        defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)", code))
        for grp in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}", code):
            defined |= {x.split(":")[-1].split("=")[0].strip() for x in grp.split(",")}
        for grp in re.findall(r"\(([\w$,\s=]*)\)\s*=>", code) + re.findall(r"function\s*[\w$]*\s*\(([^)]*)\)", code):
            defined |= {x.split("=")[0].strip() for x in grp.split(",")}
        defined |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*=>", code))
        calls = set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code))
        missing = sorted(calls - defined - KEYWORDS - BROWSER)
        assert not missing, f"{page}: wywołania bez definicji: {missing}"


def test_web_api_calls_exist_in_client_api():
    from emustart import api
    have = {n for n in dir(api.Api) if not n.startswith("_")}
    for f in PAGES["index"]:
        calls = set(re.findall(r"api\(\)\??\.([A-Za-z_]\w*)", (WEB / f).read_text(encoding="utf-8")))
        assert calls <= have, f"{f}: brak w api.Api: {sorted(calls - have)}"
