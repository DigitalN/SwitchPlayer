"""What Switch Player remembers about each Game's last finished Build, in its own file in its data
folder (nothing is written to the Library folder): the game files the Game folder held when that
Build started, and the nxnative commit it ran with.

Out of date uses it to tell that the Game folder has gained a file since. A file's modification
date can't: copying in Finder, `cp -c`, `ditto` and `mv` all keep the old one, so an update or
DLC dropped in after the Build looked older than the Game app. What the folder held at the Build
is what that Build used, and a Build that reuses its Game app (leaving its Info.plist as it was)
still records it.

The commit is for the same reason: nxnative's packer writes `DMCommit` only when it packs a Game
app anew, so a Build whose changes didn't reach the game's packed files leaves the old one (#20).
The changes since a Build are counted from whichever is newer, so a Rebuild always clears them.
"""

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("switchplayer")


class Records:
    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as e:
            log.warning("Build records unreadable (%s); starting again", e)
            raw = {}
        games = raw.get("games") if isinstance(raw, dict) else None
        self._games = {}
        for k, v in (games if isinstance(games, dict) else {}).items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            record = {}
            if _files(v.get("files")) is not None:
                record["files"] = v["files"]
            if isinstance(v.get("commit"), str) and v["commit"]:
                record["commit"] = v["commit"]
            if record:
                self._games[k] = record

    def files(self, game_id):
        """{file name: size} of the Game folder's game files at its last finished Build, or None."""
        with self._lock:
            record = self._games.get(game_id)
            return dict(record["files"]) if record and "files" in record else None

    def commit(self, game_id):
        """The nxnative commit its last finished Build ran with (`+dirty` with uncommitted edits),
        or None."""
        with self._lock:
            return self._games.get(game_id, {}).get("commit")

    def set_build(self, game_id, files=None, commit=None):
        """A finished Build: what it used. A detail it couldn't tell (None) keeps the last one."""
        with self._lock:
            record = dict(self._games.get(game_id, {}))
            if files is not None:
                record["files"] = dict(files)
            if commit:
                record["commit"] = commit
            if record:
                self._games[game_id] = record
                self._save()

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        part = self._path.with_name(self._path.name + ".partial")
        part.write_text(json.dumps({"games": self._games}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(part, self._path)


def _files(value):
    if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, int) for k, v in value.items()):
        return value
    return None
