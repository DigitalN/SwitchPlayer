"""NX Player's own settings, stored as JSON in its data folder.

Every value is validated on load and on save, so a stale or hand-edited file can't put the
app into a bad state. The first launch fills in the Library folder and keys file from NX
Play's preferences (com.varyform.nxplay), which are only read.
"""

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("nxplayer")

NXPLAY_DOMAIN = "com.varyform.nxplay"

DEFAULTS = {
    "library_folder": None,  # the folder holding the Game folders
    "keys_file": None,  # prod.keys, for Game folders of .nsp and .xci files
    "nxnative_checkout": None,  # another nxnative checkout to build with, which NX Player never changes
    "build_options": {},  # {game id: options as typed}, only for games that have some
}


def _path(value):
    return str(Path(value).expanduser()) if isinstance(value, str) and value.strip() else None


def validate(raw):
    """A complete, valid settings dict built from `raw` (unknown keys dropped)."""
    s = {**DEFAULTS, "build_options": {}}
    if not isinstance(raw, dict):
        return s
    for key in ("library_folder", "keys_file", "nxnative_checkout"):
        s[key] = _path(raw.get(key))
    options = raw.get("build_options")
    if isinstance(options, dict):
        s["build_options"] = {k: v.strip() for k, v in options.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}
    return s


def nxplay_preferences():
    """NX Play's Library folder and keys file, if it has them."""
    try:
        from Foundation import CFPreferencesCopyAppValue
    except ImportError:
        return {}
    found = {}
    for ours, theirs in (("library_folder", "gamesRoot"), ("keys_file", "keysPath")):
        value = CFPreferencesCopyAppValue(theirs, NXPLAY_DOMAIN)
        if isinstance(value, str) and Path(value).expanduser().exists():
            found[ours] = str(value)
    return found


class Settings:
    def __init__(self, path, first_launch=nxplay_preferences):
        self._path = Path(path)
        self._lock = threading.Lock()
        try:
            with open(self._path, encoding="utf-8") as f:
                self._values = validate(json.load(f))
        except FileNotFoundError:
            found = first_launch()
            self._values = validate(found)
            if found:
                log.info("Took NX Play's %s", " and ".join(f"{k.replace('_', ' ')} {v}" for k, v in found.items()))
            self._save()
        except (OSError, ValueError) as e:
            log.warning("Settings unreadable (%s); starting from the defaults", e)
            self._values = validate(None)

    def get(self):
        with self._lock:
            return {**self._values, "build_options": dict(self._values["build_options"])}

    def update(self, changes):
        with self._lock:
            self._values = validate({**self._values, **changes})
            self._save()
            return {**self._values, "build_options": dict(self._values["build_options"])}

    def set_build_options(self, game_id, text):
        with self._lock:
            options = dict(self._values["build_options"])
            if text and text.strip():
                options[game_id] = text.strip()
            else:
                options.pop(game_id, None)
            self._values = validate({**self._values, "build_options": options})
            self._save()

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        part = self._path.with_name(self._path.name + ".partial")
        part.write_text(json.dumps(self._values, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(part, self._path)
