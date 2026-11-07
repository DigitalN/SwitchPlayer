"""Serve the library page in a browser, with a stand-in for pywebview's API (stub.js).

    python3 tools/preview/serve.py [port]

Then open http://127.0.0.1:8766/ (the `page-preview` entry in .claude/launch.json does both
in Claude's browser pane). The page is rebuilt from Application Files/index.html and stub.js
on every load, so a reload shows the latest edit. Query options are listed at the top of
stub.js.

The games are the Library folder's (SWITCHPLAYER_PREVIEW_LIBRARY, or the maintainer's
~/Switch Games), found and named by the app's own library.py and titledb.py, so
they can't drift from it. The names and art titledb.py downloads (the eShop's title list once,
then each game's icon from Nintendo's CDN) are kept in SWITCHPLAYER_DATA_DIR, or
tools/preview/.data, not in the app's own data folder. The Library folder is only read: nothing
there or in nxnative's folders is written, and nothing is opened or built. This is a
development tool on localhost only; nothing here ships in the app.
"""

import base64
import importlib
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent.parent / "Application Files"
LIBRARY = Path(os.environ.get("SWITCHPLAYER_PREVIEW_LIBRARY", "~/Switch Games")).expanduser()
DATA_DIR = Path(os.environ.get("SWITCHPLAYER_DATA_DIR", HERE / ".data")).expanduser()
sys.path.insert(0, str(APP_DIR))

import library  # noqa: E402  (pure: no AppKit)
import titledb  # noqa: E402

MARKER = "<script>\n'use strict';"


def art_data(path):
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii") if path else None


def games():
    importlib.reload(library)  # an edit to them shows on the next load, like one to the page
    importlib.reload(titledb)
    found = library.scan(LIBRARY)
    db = titledb.TitleDB(DATA_DIR / "titledb")
    db.update([g.title_id for g in found if not db.art(g.title_id) or not db.name(g.title_id)])
    games = [{"id": g.id, "name": g.name(db.name(g.title_id)), "title_id": g.title_id,
              "art_data": art_data(db.art(g.title_id)), "nxplay_save": g.nxplay_save,
              "nxplay_build": g.nxplay_build} for g in found]
    return sorted(games, key=lambda g: library.sort_key(g["name"]))


def build():
    page = (APP_DIR / "index.html").read_text(encoding="utf-8")
    if MARKER not in page:
        raise SystemExit("index.html's script no longer starts with 'use strict'; update MARKER in serve.py")
    stub = (HERE / "stub.js").read_text(encoding="utf-8")
    facts = json.dumps({"library": {"name": LIBRARY.name}, "games": games()})
    page = page.replace(MARKER, f"<script>\nwindow.__app = {facts};\n{stub}\n</script>\n{MARKER}", 1)
    return page.replace("</body>", "<script>window.__preview.start();</script>\n</body>", 1)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = build().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    build()  # fail now, not on the first load
    print(f"Serving the library page at http://127.0.0.1:{port}/ with the games in {LIBRARY}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
