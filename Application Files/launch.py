"""Application-bundle discovery and relaunching, adapted from Mac Engine Room."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPOSITORY = "DigitalN/SwitchPlayer"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"

RELAUNCH_SCRIPT = (
    'pid=$1; exe=$2; shift 2; tries=0\n'
    'while kill -0 "$pid" 2>/dev/null && [ $tries -lt 100 ]; do sleep 0.1; tries=$((tries + 1)); done\n'
    'for attempt in 1 2; do\n'
    '  /usr/bin/open -n "$@"\n'
    '  tries=0\n'
    '  while [ $tries -lt 50 ]; do\n'
    '    /bin/ps -axo comm= | /usr/bin/grep -qxF -- "$exe" && exit 0\n'
    '    sleep 0.1; tries=$((tries + 1))\n'
    '  done\n'
    'done\n'
)


def bundle_path():
    if not getattr(sys, "frozen", False):
        return None
    from Foundation import NSBundle
    return Path(NSBundle.mainBundle().bundlePath())


def in_applications_folder(path):
    resolved = str(Path(path).resolve())
    return resolved.startswith(("/Applications/", str(Path.home() / "Applications") + "/"))


def activate_app():
    import AppKit
    app = AppKit.NSApp
    if hasattr(app, "activate"):
        app.activate()
    else:
        app.activateIgnoringOtherApps_(True)


def relaunch(app, background=False):
    """Open the replacement after this process exits and releases the instance lock."""
    arguments = ["-g"] if background else []
    for name in ("SWITCHPLAYER_DATA_DIR", "SWITCHPLAYER_UPDATE_FEED"):
        if os.environ.get(name):
            arguments += ["--env", f"{name}={os.environ[name]}"]
    arguments.append(str(app))
    subprocess.Popen(["/bin/sh", "-c", RELAUNCH_SCRIPT, "sh", str(os.getpid()), executable(app), *arguments],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def executable(app):
    try:
        with open(Path(app) / "Contents" / "Info.plist", "rb") as f:
            name = plistlib.load(f).get("CFBundleExecutable")
    except (OSError, plistlib.InvalidFileException):
        name = None
    return str(Path(app) / "Contents" / "MacOS" / (name or Path(app).stem))
