"""Verified updates from Switch Player's GitHub releases, adapted from Mac Engine Room.

Once a day after a wake, a worker fetches the latest release through /usr/bin/curl. A
replacement must contain this bundle identifier, be signed by this copy's developer team,
and have a newer version. Automatic installation waits for a quiet moment with no Build
running or waiting. Source checkouts and ad-hoc copies never update themselves.
"""

import json
import logging
import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
from datetime import date
from pathlib import Path

from launch import REPOSITORY, RELEASES_URL, activate_app, in_applications_folder, relaunch

log = logging.getLogger("switchplayer")

IDLE, CHECKING, DOWNLOADING, READY = "idle", "checking", "downloading", "ready"
FEED_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
WAKE_DELAY = 60
QUIET_SECONDS = 10
INSTALL_RETRY = 30


class UpdateError(Exception):
    pass


def version_key(version):
    parts = [int(m.group()) if (m := re.match(r"\d+", p)) else 0
             for p in str(version).lstrip("v").split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(version, other):
    return version_key(version) > version_key(other)


def parse_release(data):
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise UpdateError("GitHub's answer didn't describe a release.")
    tag = data["tag_name"]
    version = tag[1:] if tag.startswith("v") else tag
    for asset in data.get("assets") or []:
        if str(asset.get("name", "")).lower().endswith(".dmg") and asset.get("browser_download_url"):
            return version, asset["browser_download_url"]
    raise UpdateError("The latest release has no disk image attached.")


def team_identifier(codesign_details):
    match = re.search(r"^TeamIdentifier=(\S+)$", codesign_details, re.MULTILINE)
    return match.group(1) if match and match.group(1) != "not" else None


def signing_requirement(bundle_id, team):
    return (f'=identifier "{bundle_id}" and anchor apple generic'
            f' and certificate leaf[subject.OU] = "{team}"')


def check_is_due(last_check, today):
    return last_check != today.isoformat()


def _run(arguments, **kwargs):
    return subprocess.run(arguments, capture_output=True, **kwargs)


def _curl(url, output, max_time, accept=None):
    arguments = ["/usr/bin/curl", "--silent", "--show-error", "--location", "--max-time", str(max_time),
                 "--output", str(output), "--write-out", "\n%{http_code}"]
    if accept:
        arguments += ["--header", f"Accept: {accept}"]
    result = _run([*arguments, url], text=True)
    if result.returncode != 0:
        raise UpdateError(f"Couldn't reach GitHub ({result.stderr.strip() or result.returncode}).")
    body, _, code = result.stdout.rpartition("\n")
    return int(code or 0), body


def own_team(app):
    result = _run(["/usr/bin/codesign", "-d", "--verbose=2", str(app)], text=True)
    return team_identifier(result.stderr)


def info_value(app, key):
    try:
        with open(Path(app) / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f).get(key)
    except (OSError, plistlib.InvalidFileException):
        return None


def _attach(image, attempts=3):
    for attempt in range(attempts):
        result = _run(["/usr/bin/hdiutil", "attach", str(image), "-nobrowse", "-readonly", "-noautoopen", "-plist"])
        if result.returncode == 0:
            entities = plistlib.loads(result.stdout).get("system-entities", [])
            mount = next((e["mount-point"] for e in entities if e.get("mount-point")), None)
            if mount:
                return mount
        _detach_image(image)
        if attempt < attempts - 1:
            time.sleep(3)
    raise UpdateError(f"hdiutil couldn't open the disk image ({result.returncode}).")


def _detach_image(image):
    try:
        images = plistlib.loads(_run(["/usr/bin/hdiutil", "info", "-plist"]).stdout).get("images", [])
    except (plistlib.InvalidFileException, ValueError):
        return
    for entry in images:
        if Path(entry.get("image-path", "")).resolve() != Path(image).resolve():
            continue
        devices = [e["dev-entry"] for e in entry.get("system-entities", []) if e.get("dev-entry")]
        if devices:
            _run(["/usr/bin/hdiutil", "detach", min(devices, key=len), "-force"])


def copy_app_from_image(image, destination, bundle_id):
    mount = _attach(image)
    try:
        apps = [p for p in Path(mount).iterdir() if p.suffix == ".app" and not p.is_symlink()]
        app = next((p for p in apps if info_value(p, "CFBundleIdentifier") == bundle_id), None)
        if app is None:
            raise UpdateError("The latest release's disk image doesn't contain Switch Player.")
        copied = _run(["/usr/bin/ditto", str(app), str(destination)])
        if copied.returncode != 0:
            raise UpdateError(f"ditto failed ({copied.returncode}).")
    finally:
        _run(["/usr/bin/hdiutil", "detach", mount, "-force"])


def verify(app, team, bundle_id):
    result = _run(["/usr/bin/codesign", "--verify", "--deep", "--strict",
                   "-R", signing_requirement(bundle_id, team), str(app)], text=True)
    if result.returncode != 0:
        log.warning("Rejected update: %s", result.stderr.strip())
        raise UpdateError("The downloaded app was changed or isn't signed by the Switch Player developer.")
    version = info_value(app, "CFBundleShortVersionString")
    if not version:
        raise UpdateError("The latest release's disk image doesn't contain Switch Player.")
    return version


class Updater:
    def __init__(self, app, version, data_dir, is_busy, on_status, quit_app):
        self.status = IDLE
        self.version = None
        self._app = app
        self._current = (info_value(app, "CFBundleShortVersionString") if app else None) or version
        self._bundle_id = info_value(app, "CFBundleIdentifier") if app else None
        self._last_check_file = Path(data_dir) / "last-update-check"
        self._is_busy = is_busy
        self._on_status = on_status
        self._quit_app = quit_app
        self._feed = os.environ.get("SWITCHPLAYER_UPDATE_FEED") or FEED_URL
        self._team = None
        self._staged = None
        self._wake_observer = None
        self._check_pending = False
        self._install_timer = None
        self.unavailable_reason = self._why_unavailable()

    def _why_unavailable(self):
        if self._app is None:
            return "running from source"
        self._team = own_team(self._app)
        if self._team is None:
            return "this copy isn't signed by a developer team, so updates can't be verified"
        if not in_applications_folder(self._app):
            return "Switch Player isn't in the Applications folder"
        if not (os.access(self._app, os.W_OK) and os.access(self._app.parent, os.W_OK)):
            return f"Switch Player can't replace itself in {self._app.parent}"
        return None

    def start(self):
        if self.unavailable_reason:
            log.info("Automatic updates off: %s", self.unavailable_reason)
            return
        from AppKit import NSWorkspace, NSWorkspaceDidWakeNotification
        from Foundation import NSOperationQueue
        center = NSWorkspace.sharedWorkspace().notificationCenter()
        self._wake_observer = center.addObserverForName_object_queue_usingBlock_(
            NSWorkspaceDidWakeNotification, None, NSOperationQueue.mainQueue(), self._did_wake)

    def _did_wake(self, notification):
        from PyObjCTools import AppHelper
        if not self._check_pending:
            self._check_pending = True
            AppHelper.callLater(WAKE_DELAY, self._check_if_due)

    def _check_if_due(self):
        self._check_pending = False
        if self.status != IDLE:
            return
        try:
            last = self._last_check_file.read_text(encoding="utf-8").strip()
        except OSError:
            last = None
        if check_is_due(last, date.today()):
            self._start_check(user_initiated=False)

    def check_now(self):
        if self.unavailable_reason:
            _open_releases_page()
        elif self.status == READY:
            # An update already waiting, asked for from the menu, which runs on a thread of its own.
            _on_main(self._install_if_quiet, True)
        elif self.status == IDLE:
            self._start_check(user_initiated=True)

    def _start_check(self, user_initiated):
        self._set_status(CHECKING)
        threading.Thread(target=self._check, args=(user_initiated,), name="update-check", daemon=True).start()

    def _set_status(self, status, version=None):
        from PyObjCTools import AppHelper
        self.status, self.version = status, version
        AppHelper.callAfter(self._on_status, status, version)

    def _check(self, user_initiated):
        from PyObjCTools import AppHelper
        try:
            release = self._latest_release()
            self._last_check_file.parent.mkdir(parents=True, exist_ok=True)
            self._last_check_file.write_text(date.today().isoformat(), encoding="utf-8")
            if release is None or not is_newer(release[0], self._current):
                self._set_status(IDLE)
                log.info("Up to date at %s", self._current)
                if user_initiated:
                    AppHelper.callAfter(_alert, "Switch Player is up to date",
                                        f"You have the latest version, {self._current}.")
                return
            version, url = release
            log.info("Downloading %s", version)
            self._set_status(DOWNLOADING, version)
            self._staged = self._download(url)
            self._set_status(READY, version)
            log.info("%s is ready to install", version)
            AppHelper.callAfter(self._install_if_quiet, user_initiated)
        except Exception as e:
            self._set_status(IDLE)
            log.error("Update check failed: %s", e)
            if user_initiated:
                AppHelper.callAfter(_alert_failure, str(e))

    def _latest_release(self):
        code, body = _curl(self._feed, "-", max_time=30, accept="application/vnd.github+json")
        if code == 404:
            return None
        if code not in (200, 0):
            raise UpdateError(f"GitHub answered with an error ({code}).")
        try:
            return parse_release(json.loads(body))
        except ValueError:
            raise UpdateError("GitHub's answer didn't describe a release.") from None

    def _download(self, url):
        from Foundation import NSURL, NSFileManager, NSItemReplacementDirectory, NSUserDomainMask
        folder_url, error = NSFileManager.defaultManager().URLForDirectory_inDomain_appropriateForURL_create_error_(
            NSItemReplacementDirectory, NSUserDomainMask, NSURL.fileURLWithPath_(str(self._app)), True, None)
        if folder_url is None:
            raise UpdateError(f"Couldn't make a folder for the download ({error}).")
        folder = Path(folder_url.path())
        try:
            image = folder / "update.dmg"
            code, _ = _curl(url, image, max_time=600)
            if code not in (200, 0):
                raise UpdateError(f"GitHub answered with an error ({code}).")
            app = folder / self._app.name
            copy_app_from_image(image, app, self._bundle_id)
            image.unlink(missing_ok=True)
            version = verify(app, self._team, self._bundle_id)
            if not is_newer(version, self._current):
                raise UpdateError(f"The latest release contains version {version}, which isn't newer than this one.")
            return version, app, folder
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def _install_if_quiet(self, user_initiated=False):
        """On the main thread. Never while a Build runs or waits. The player's own Check for
        Updates… doesn't also wait for a quiet keyboard and mouse: they just used it, and are told
        when the install is held for a Build, which then installs it once it ends."""
        import Quartz
        from AppKit import NSEvent
        from Foundation import NSTimer
        if self.status != READY:
            self._stop_install_timer()
            return
        busy = self._is_busy()
        idle = Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType)
        quiet = user_initiated or (idle >= QUIET_SECONDS and NSEvent.pressedMouseButtons() == 0)
        if quiet and not busy:
            self._install()
            return
        if self._install_timer is None:
            self._install_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                INSTALL_RETRY, True, lambda timer: self._install_if_quiet())
        if busy and user_initiated:
            _alert(f"Switch Player {self.version} is ready", "It will install once the Build ends.")

    def _stop_install_timer(self):
        if self._install_timer is not None:
            self._install_timer.invalidate()
            self._install_timer = None

    def _install(self):
        from Foundation import NSURL, NSFileManager, NSFileManagerItemReplacementUsingNewMetadataOnly
        self._stop_install_timer()
        if self._staged is None:
            return
        (version, app, folder), self._staged = self._staged, None
        ok, _, error = NSFileManager.defaultManager().replaceItemAtURL_withItemAtURL_backupItemName_options_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(self._app)), NSURL.fileURLWithPath_(str(app)), None,
            NSFileManagerItemReplacementUsingNewMetadataOnly, None, None)
        shutil.rmtree(folder, ignore_errors=True)
        if not ok:
            self._set_status(IDLE)
            log.error("Couldn't install %s: %s", version, error)
            _alert_failure("Switch Player couldn't replace itself in the Applications folder.")
            return
        log.info("Installed %s; relaunching", version)
        relaunch(self._app, background=True)
        self._quit_app()


def _on_main(function, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(function, *args)


def _open_releases_page():
    from AppKit import NSWorkspace
    from Foundation import NSURL
    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(RELEASES_URL))


def _alert(title, message, buttons=()):
    import AppKit
    activate_app()
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    for button in buttons:
        alert.addButtonWithTitle_(button)
    return alert.runModal()


def _alert_failure(reason):
    import AppKit
    choice = _alert("Switch Player couldn't update itself",
                    f"{reason} You can download the latest version from GitHub instead.",
                    ("Open Releases Page", "Cancel"))
    if choice == AppKit.NSAlertFirstButtonReturn:
        _open_releases_page()
