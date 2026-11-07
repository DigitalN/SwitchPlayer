"""A Game app's Game settings, for Switch Player's Settings panel.

Only what nxnative documents for front ends (docs/frontends.md: The settings list, Saved choices,
Starting a game; ADR 0001): the settings list each Game app carries
(`Contents/Resources/dm/launcher.json`, schema 1), the player's saved choices (`launcher.<env>`
strings in the app's preferences domain) and the `env.txt` files that set a variable for every
start. Saved choices are written as nxnative's own Settings window writes them
(`dm/ios/Sources/LauncherSettings.swift`): only those that differ from the tested default, a
range as "%.2f", so both views of the one store always agree.
"""

import json
import logging
import math

log = logging.getLogger("switchplayer")

PREFIX = "launcher."  # a saved choice's key: launcher.<env>
KINDS = ("choice", "range", "toggle")  # schema 1's; a new kind comes with schema 2


class Setting:
    """One row of the settings list."""

    def __init__(self, raw):
        self.raw = raw
        self.env = raw["env"]
        self.kind = raw["kind"]
        self.default = raw["default"]
        self.options = [option[0] for option in raw.get("options") or []]
        self.min, self.max = raw.get("min"), raw.get("max")
        self.on, self.off = raw.get("on") or "1", raw.get("off") or "0"

    @property
    def key(self):
        return PREFIX + self.env

    def accepts(self, value):
        """A value this build still offers, as the launcher checks a saved choice: one of a
        choice's options, a toggle's on or off, a number in a range (no hex, no leading space)."""
        if not isinstance(value, str):
            return False
        if self.kind == "choice":
            return value in self.options
        if self.kind == "toggle":
            return value in (self.on, self.off)
        if value != value.lstrip() or "x" in value.lower():
            return False
        try:
            number = float(value)
        except ValueError:
            return False
        return (math.isfinite(number) and isinstance(self.min, (int, float)) and isinstance(self.max, (int, float))
                and self.min <= number <= self.max)

    def is_default(self, value):
        """A range compares as numbers: the window writes 1.00 for a default of 1.0."""
        if self.kind == "range":
            try:
                return abs(float(value) - float(self.default)) < 1e-6
            except ValueError:
                return False
        return value == self.default

    def stored(self, value):
        """The string to save: a range as the window's slider writes it."""
        return f"{float(value):.2f}" if self.kind == "range" else value


def settings_list(app):
    """("ok", [Setting]) for a list the panel can draw; ("none", []) for a Game app with no
    settings to offer; ("unsupported", []) for one it can't (a newer schema, a kind it doesn't
    know, or a file it can't read), which nxnative's own Settings window may still show."""
    path = app / "Contents" / "Resources" / "dm" / "launcher.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "none", []
    except (OSError, ValueError) as e:
        log.warning("Couldn't read %s: %s", path, e)
        return "unsupported", []
    if not isinstance(raw, dict) or raw.get("schema", 1) != 1 or not isinstance(raw.get("settings"), list):
        return "unsupported", []
    settings = []
    for row in raw["settings"]:
        if not (isinstance(row, dict) and row.get("kind") in KINDS
                and all(isinstance(row.get(field), str) for field in ("env", "label", "default"))):
            return "unsupported", []
        settings.append(Setting(row))
    return ("ok", settings) if settings else ("none", [])


def env_file(path):
    """{KEY: value} from an env.txt, read as the Game app's launcher reads it: KEY=VALUE lines,
    # starts a comment, spaces around the line and before the = don't count."""
    found = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.lstrip(" \t").rstrip(" \t\r\n")
        key, eq, value = line.partition("=")
        key = key.rstrip(" \t")
        if line.startswith("#") or not eq or not key:
            continue
        found.setdefault(key, value)  # the first one wins, as a variable already set stays
    return found


def fixed_values(app, player_env):
    """The variables the env.txt files set for every start, which the Settings window shows
    fixed: the player's (`<Application Support>/nxnative/<title ID>/env.txt`) before the Game
    app's own (`Contents/Resources/dm/env.txt`)."""
    fixed = env_file(player_env)
    for key, value in env_file(app / "Contents" / "Resources" / "dm" / "env.txt").items():
        fixed.setdefault(key, value)
    return fixed


def rows(settings, saved, fixed):
    """The panel's rows: each setting as the list gives it, with the value it has now (a fixed
    one's from its env.txt, else the saved choice the build still offers, else the tested
    default) and whether an env.txt fixes it."""
    result = []
    for setting in settings:
        if setting.env in fixed:
            value, is_fixed = fixed[setting.env], True
        else:
            choice = saved.get(setting.key)
            value, is_fixed = (choice if setting.accepts(choice) else setting.default), False
        result.append({**setting.raw, "value": value, "fixed": is_fixed})
    return result


def changes(settings, values, fixed):
    """{key: string to save, or None to remove} for the panel's Save: every setting an env.txt
    doesn't fix and the panel sent a value it accepts for. A default is removed, so a later
    build's tested default reaches the player; a fixed setting's saved choice stays as it is."""
    result = {}
    for setting in settings:
        if setting.env in fixed or setting.env not in values:
            continue
        value = values[setting.env]
        if not setting.accepts(value):
            log.warning("Ignored %r for %s: not a value this build offers", value, setting.env)
            continue
        result[setting.key] = None if setting.is_default(value) else setting.stored(value)
    return result
