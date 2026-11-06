"""Names and art for the Library's games, from the eShop's listings in blawar/titledb.

Adapted from NX Play's ArtworkStore (Packages/NXPlayKit/Sources/NXPlayKit/Model/Artwork.swift
in varyform/nx-native-wrapper; MIT, see THIRD-PARTY-NOTICES): the same list, the same
choice when a title is listed twice, and images from Nintendo's CDN only.

The list (US.en.json: about 26 MB as downloaded, 90 MB unpacked) is fetched only when a game
appears that the saved index doesn't know, at most once a launch, and read one entry at a
time: parsed whole, it took 650 MB. Only the Library's titles are kept, in index.json. A title
the list lacks is asked about again once the index is a week old, as new games are added.
Each game's art, the eShop's 1024×1024 icon, is downloaded once and kept.

Downloads go through /usr/bin/curl, which uses the system's certificates (the bundled
Python has none of its own).
"""

import io
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("switchplayer")

LIST_URL = "https://raw.githubusercontent.com/blawar/titledb/master/US.en.json"
IMAGE_HOST = "img-eshop.cdn.nintendo.net"  # the list is third-party data: images only from here
MISSING_LIFETIME = 7 * 86400
TITLE_ID = re.compile(r"^0100[0-9A-F]{12}$")


def clean_name(name):
    """The eShop's name without its trademark signs: 'The Legend of Zelda™: Echoes of Wisdom'
    → 'The Legend of Zelda: Echoes of Wisdom'."""
    if not isinstance(name, str):
        return None
    name = re.sub(r"\s+", " ", re.sub(r"[™®©]", "", name)).strip()
    return name or None


def entries(stream, chunk=1 << 20):
    """(key, value) for each member of the JSON object in a binary stream, one at a time, so
    the whole list is never in memory."""
    decoder = json.JSONDecoder()
    text = io.TextIOWrapper(stream, encoding="utf-8")
    buf, pos, eof = "", 0, False

    def fill():
        nonlocal buf, pos, eof
        data = text.read(chunk)
        eof = not data
        buf, pos = buf[pos:] + data, 0

    def skip():  # whitespace, reading on at the end of the buffer
        nonlocal pos
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n":
                pos += 1
            if pos < len(buf) or eof:
                return
            fill()

    def value():
        nonlocal pos
        while True:
            try:
                v, pos = decoder.raw_decode(buf, pos)
                return v
            except json.JSONDecodeError:
                if eof:
                    raise
                fill()  # the value runs past the buffer

    def expect(chars):
        nonlocal pos
        skip()
        if pos >= len(buf) or buf[pos] not in chars:
            raise ValueError(f"expected one of {chars!r} in the title list")
        pos += 1
        return buf[pos - 1]

    fill()
    expect("{")
    skip()
    if buf.startswith("}", pos):
        return
    while True:
        skip()
        key = value()
        expect(":")
        skip()
        yield key, value()
        if expect(",}") == "}":
            return


def titles_in(stream, wanted):
    """{title ID: {"name", "icon"}} for the wanted titles. A title listed twice takes its lowest
    nsuId's entry (the original listing, not a later edition's), the same one every time."""
    found = {}
    for nsu_id, entry in entries(stream):
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("id") or "").upper()
        if tid not in wanted:
            continue
        key = (len(str(nsu_id)), str(nsu_id))  # nsuIds are numbers written as text
        if tid in found and found[tid][0] <= key:
            continue
        icon = entry.get("iconUrl")
        found[tid] = (key, {"name": clean_name(entry.get("name")),
                            "icon": icon if isinstance(icon, str) and icon.strip() else None})
    return {tid: info for tid, (_, info) in found.items()}


def allowed_image(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname == IMAGE_HOST


def curl(url, output, max_time):
    """Downloads `url` to `output`. True when it came back with HTTP 200."""
    try:
        result = subprocess.run(
            ["/usr/bin/curl", "--silent", "--show-error", "--location", "--compressed", "--proto", "=https", "--proto-redir", "=https",
             "--max-time", str(max_time), "--output", str(output), "--write-out", "%{http_code}", url],
            capture_output=True, text=True, timeout=max_time + 10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("Couldn't download %s: %s", url, e)
        return False
    if result.returncode != 0 or result.stdout.strip() != "200":
        log.warning("Couldn't download %s: %s", url, result.stderr.strip() or f"HTTP {result.stdout.strip()}")
        return False
    return True


class TitleDB:
    """The names and art kept in `folder` (index.json, art/<title ID>.jpg)."""

    def __init__(self, folder, list_url=LIST_URL, download=curl):
        self.folder = Path(folder)
        self.art_folder = self.folder / "art"
        self._list_url = list_url
        self._download = download
        self._list_tried = False  # one download of the list per launch, even when it fails
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.folder / "index.json", encoding="utf-8") as f:
                index = json.load(f)
        except (OSError, ValueError):
            return {"fetched": 0, "titles": {}, "missing": []}
        titles = index.get("titles") if isinstance(index.get("titles"), dict) else {}
        missing = index.get("missing") if isinstance(index.get("missing"), list) else []
        fetched = index.get("fetched") if isinstance(index.get("fetched"), (int, float)) else 0
        return {"fetched": fetched, "titles": {k: v for k, v in titles.items() if isinstance(v, dict)},
                "missing": [m for m in missing if isinstance(m, str)]}

    def _save(self, index):
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.folder / "index.json.partial"
        part.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(part, self.folder / "index.json")

    def name(self, tid):
        """The eShop's name for a title, if the saved index has one."""
        info = self._load()["titles"].get(tid)
        return info.get("name") if info else None

    def art(self, tid):
        """The title's art on disk, if it's been downloaded."""
        path = self.art_folder / f"{tid}.jpg"
        return path if tid and path.is_file() else None

    def update(self, tids):
        """Looks up the titles the index doesn't know yet and downloads the art they lack.
        Slow (the list, then each image): run it off the main thread. Returns the title IDs
        whose name or art is new."""
        tids = sorted({t for t in tids if t and TITLE_ID.match(t)})
        with self._lock:
            index = self._load()
            stale = time.time() - index["fetched"] > MISSING_LIFETIME
            unknown = [t for t in tids if t not in index["titles"] and (stale or t not in index["missing"])]
            changed = set()
            if unknown and not self._list_tried:
                self._list_tried = True
                found = self._fetch_list(set(tids) | set(index["titles"]))
                if found is not None:
                    changed |= {t for t in found if found[t] != index["titles"].get(t)}
                    asked = set(tids) | set(index["titles"]) | set(index["missing"])
                    index = {"fetched": time.time(), "titles": found, "missing": sorted(asked - set(found))}
                    self._save(index)
            for tid in tids:
                icon = (index["titles"].get(tid) or {}).get("icon")
                if icon and not self.art(tid) and self._fetch_image(icon, tid):
                    changed.add(tid)
            return changed

    def _fetch_list(self, wanted):
        self.folder.mkdir(parents=True, exist_ok=True)
        download = self.folder / "US.en.json.download"
        try:
            log.info("Downloading the eShop title list for %d games", len(wanted))
            if not self._download(self._list_url, download, 600):
                return None
            with open(download, "rb") as f:
                found = titles_in(f, wanted)
            log.info("The title list has %d of them", len(found))
            return found
        except (OSError, ValueError) as e:
            log.warning("Couldn't read the title list: %s", e)
            return None
        finally:
            download.unlink(missing_ok=True)  # only the Library's titles are kept

    def _fetch_image(self, url, tid):
        if not allowed_image(url):
            log.warning("Not downloading art for %s from outside Nintendo's CDN: %s", tid, url)
            return False
        self.art_folder.mkdir(parents=True, exist_ok=True)
        part = self.art_folder / f"{tid}.jpg.partial"
        try:
            if not self._download(url, part, 60):
                return False
            with open(part, "rb") as f:
                if f.read(3) != b"\xff\xd8\xff":  # a JPEG, not an error page
                    log.warning("The art for %s isn't a JPEG", tid)
                    return False
            os.replace(part, self.art_folder / f"{tid}.jpg")
            return True
        finally:
            part.unlink(missing_ok=True)
