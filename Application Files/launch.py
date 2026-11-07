"""Application-bundle discovery and relaunching, adapted from Mac Engine Room, and the icon
refresh a new version's first launch makes."""

import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("switchplayer")

REPOSITORY = "DigitalN/SwitchPlayer"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
              "/Support/lsregister")
LAUNCHED_VERSION = "launched-version"  # in the data folder: the version that last launched

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


def is_new_version(data_dir, version):
    """This version hasn't launched before: an App update (or the first install) put it here."""
    try:
        return (Path(data_dir) / LAUNCHED_VERSION).read_text(encoding="utf-8").strip() != version
    except OSError:
        return True


def refresh_icon_cache(app, data_dir, version, run=subprocess.run):
    """An App update replaces the bundle in place, and macOS can go on showing the icon it cached
    for that path from an earlier version: the Dock kept 0.1.0's after updates to 0.1.2. So the
    first launch of a version registers the bundle with Launch Services again and marks it
    changed, then remembers the version. A failure is tried again at the next launch."""
    try:
        result = run([LSREGISTER, "-f", str(app)], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log.info("Couldn't refresh the app's icon: %s", e)
        return False
    if result.returncode != 0:
        log.info("Couldn't refresh the app's icon: lsregister exited with %s", result.returncode)
        return False
    try:
        os.utime(app)  # the bundle's date, which the icon cache goes by; not a signed file
    except OSError:
        pass
    try:
        (Path(data_dir) / LAUNCHED_VERSION).write_text(version, encoding="utf-8")
    except OSError:
        return False
    log.info("Refreshed the app's icon for %s", version)
    return True


def icon_file(app):
    """The bundle's own icon file, from CFBundleIconFile."""
    try:
        with open(Path(app) / "Contents" / "Info.plist", "rb") as f:
            name = plistlib.load(f).get("CFBundleIconFile")
    except (OSError, plistlib.InvalidFileException):
        return None
    if not isinstance(name, str) or not name:
        return None
    path = Path(app) / "Contents" / "Resources" / (name if name.endswith(".icns") else f"{name}.icns")
    return path if path.is_file() else None


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
