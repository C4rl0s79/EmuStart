"""server — lokalny serwer HTTP dla UI: /web, /media (okładki), /assets (loga).

Okładki leżą poza katalogiem UI, a przesyłanie ich przez most pywebview jako
data-URI byłoby wolne przy przewijaniu tysięcy gier. Serwer słucha tylko na
127.0.0.1, na losowym porcie.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from emustart import paths

ROUTES = {"/media/": paths.MEDIA, "/assets/": paths.ASSETS}

# dodatkowe foldery tylko do odczytu (np. paczka logo, ikony RetroArcha) —
# udostępniane jako /local/<n>/<ścieżka>, wyłącznie zarejestrowane
LOCAL_ROOTS: list = []


def local_url(root: Path, file: Path) -> str:
    root = Path(root).resolve()
    if root not in LOCAL_ROOTS:
        LOCAL_ROOTS.append(root)
    n = LOCAL_ROOTS.index(root)
    return f"/local/{n}/" + urllib.parse.quote(str(Path(file).resolve().relative_to(root)).replace("\\", "/"))

# tryb deweloperski (main.py --browser): UI w zwykłej przeglądarce woła API przez
# POST /api/<metoda> zamiast mostu pywebview. Domyślnie wyłączony.
DEV_API = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        name = urllib.parse.urlsplit(self.path).path.removeprefix("/api/")
        fn = getattr(DEV_API, name, None) if DEV_API and not name.startswith("_") else None
        if not callable(fn):
            return self.send_error(404)
        n = int(self.headers.get("Content-Length") or 0)
        args = json.loads(self.rfile.read(n) or b"[]")
        body = json.dumps(fn(*args), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        base, rel = paths.WEB, path.lstrip("/") or "index.html"
        for prefix, root in ROUTES.items():
            if path.startswith(prefix):
                base, rel = root, path[len(prefix):]
                break
        if path.startswith("/local/"):
            n, _, rel = path[len("/local/"):].partition("/")
            if not n.isdigit() or int(n) >= len(LOCAL_ROOTS):
                return self.send_error(404)
            base = LOCAL_ROOTS[int(n)]
        target = (base / rel).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            return self.send_error(403)
        if not target.is_file():
            return self.send_error(404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0]
                         or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if base == paths.WEB else "max-age=86400")
        self.end_headers()
        self.wfile.write(data)


def start(port: int = 0, dev_api=None) -> str:
    global DEV_API
    DEV_API = dev_api
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
    return f"http://127.0.0.1:{srv.server_address[1]}"
