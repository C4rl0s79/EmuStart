"""Pomiar odczytu z serwera EmuStart (HTTP, osobne połączenie TCP na strumień).
Uruchom: python tools/httpbench.py <adres serwera> [port]  — zapyta o klucz (nie jest nigdzie zapisywany)."""
import getpass, http.client, json, sys, threading, time, urllib.parse

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8740
GAME = "2002 FIFA World Cup"  # inna gra niż w teście SMB — bez pamięci podręcznej NAS
SIZE = 384 << 20
key = getpass.getpass("Klucz serwera (EmuStart.exe --server-key): ").strip()
H = {"Authorization": f"Bearer {key}"}


def get_json(path):
    c = http.client.HTTPConnection(HOST, PORT, timeout=30)
    c.request("GET", path, headers=H)
    r = c.getresponse()
    body = r.read()
    if r.status != 200:
        raise SystemExit(f"{path}: {r.status} {body[:200]!r}")
    return json.loads(body)


games = get_json("/v1/systems/ps2/games")
g = next((x for x in games if (x.get("name") or "").startswith(GAME)), None) or max(games, key=lambda x: x.get("size") or 0)
d = get_json(f"/v1/games/{g['id']}")
f = max(d["files"], key=lambda x: x["size"])
url = f"/v1/games/{g['id']}/file/" + "/".join(urllib.parse.quote(p) for p in f["path"].replace("\\", "/").split("/"))
print(f"plik: {f['path']} ({f['size'] / 1e9:.1f} GB)")


def run(streams, block, base):
    blocks = list(range(base, base + SIZE, block))
    lock = threading.Lock()

    def worker():
        c = http.client.HTTPConnection(HOST, PORT, timeout=60)
        while True:
            with lock:
                if not blocks:
                    return
                off = blocks.pop(0)
            c.request("GET", url, headers={**H, "Range": f"bytes={off}-{off + block - 1}"})
            r = c.getresponse()
            while r.read(1 << 20):
                pass

    t = time.perf_counter()
    th = [threading.Thread(target=worker) for _ in range(streams)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    return SIZE / (time.perf_counter() - t) / 1e6



for i, (s, b) in enumerate([(1, 4 << 20), (4, 1 << 20), (8, 1 << 20), (8, 4 << 20)]):
    base = i * SIZE
    mbs = run(s, b, base)
    print(f"{s} strum., blok {b >> 20} MB: {mbs:6.1f} MB/s = {mbs * 8:5.0f} Mb/s", flush=True)
