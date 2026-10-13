"""qr — kod QR logowania urządzeń (panel serwera → Dodaj urządzenie).

Rysowany przez bibliotekę `segno` (czysty Python, licencja BSD) jako SVG. Bez niej panel
pokazuje link logowania do skopiowania — logowanie działa tak samo.
"""

from __future__ import annotations

import io


def svg(text: str, scale: int = 6) -> str:
    """Kod QR jako SVG ('' — brak biblioteki)."""
    try:
        import segno
    except ImportError:
        return ""
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=scale, border=2, dark="#000", light="#fff",
                                     xmldecl=False, svgns=True)
    return out.getvalue().decode("utf-8")
