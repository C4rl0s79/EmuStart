"""qr — kod QR logowania urządzeń (panel serwera → Dodaj urządzenie).

Rysowany przez bibliotekę `segno` (czysty Python, licencja BSD) jako SVG. Bez niej panel
pokazuje link logowania do skopiowania — logowanie działa tak samo.
"""

from __future__ import annotations

import io


def svg(text: str, scale: int = 6) -> str:
    """Kod QR jako SVG ('' — brak biblioteki). Z `viewBox` — strona może go zmniejszyć (CSS)
    i skaluje się cały; bez niego zmniejszony SVG był przycinany (strona serwera pokazywała
    część kodu, której aparat nie odczytał)."""
    try:
        import segno
    except ImportError:
        return ""
    out = io.BytesIO()
    code = segno.make(text, error="m")
    code.save(out, kind="svg", scale=scale, border=2, dark="#000", light="#fff", xmldecl=False, svgns=True)
    size = code.symbol_size(scale=scale, border=2)[0]
    return out.getvalue().decode("utf-8").replace("<svg ", f'<svg viewBox="0 0 {size} {size}" ', 1)
