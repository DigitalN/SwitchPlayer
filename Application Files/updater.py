"""Verified updates from Switch Player's GitHub releases, adapted from Mac Engine Room.

A worker fetches the latest release through /usr/bin/curl at every launch, and again once 24
hours have passed since the last check that got through: an hourly timer and a wake look for that.
A replacement must contain this bundle identifier, be signed by this copy's developer team, and
have a newer version. Automatic installation waits for a quiet moment with no Build running or
waiting. Source checkouts and ad-hoc copies never update themselves.

What it's doing shows on the page as a toast (`Updater.toast`): each step, and how it ended.
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
from pathlib import Path

from launch import REPOSITORY, RELEASES_URL, in_applications_folder, relaunch

log = logging.getLogger("switchplayer")

IDLE, CHECKING, DOWNLOADING, VERIFYING, READY, INSTALLING = (
    "idle", "checking", "downloading", "verifying", "ready", "installing")
FEED_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
WAKE_DELAY = 60
CHECK_EVERY = 24 * 60 * 60  # since the last check that got through
TICK = 60 * 60  # how often an open app looks whether that has passed, or a failed check can try again
QUIET_SECONDS = 10
INSTALL_RETRY = 30
INSTALL_DELAY = 1.5  # "Installing…" stays on screen this long before the app quits
TOAST_KEEP = 15  # a toast that fades waits this long for the page to show it (a window still opening)
INSTALLED_FILE = "installed-update"  # in the data folder: the version an App update just installed


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
    """The release's version, its disk image's URL, and the image's size in bytes (None when
    GitHub doesn't say), for the download's %."""
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise UpdateError("GitHub's answer didn't describe a release.")
    tag = data["tag_name"]
    version = tag[1:] if tag.startswith("v") else tag
    for asset in data.get("assets") or []:
        if str(asset.get("name", "")).lower().endswith(".dmg") and asset.get("browser_download_url"):
            size = asset.get("size")
            return version, asset["browser_download_url"], size if isinstance(size, int) and size > 0 else None
    raise UpdateError("The latest release has no disk image attached.")


def team_identifier(codesign_details):
    match = re.search(r"^TeamIdentifier=(\S+)$", codesign_details, re.MULTILINE)
    return match.group(1) if match and match.group(1) != "not" else None


def signing_requirement(bundle_id, team):
    return (f'=identifier "{bundle_id}" and anchor apple generic'
            f' and certificate leaf[subject.OU] = "{team}"')


def check_is_due(last_check, now):
    """Whether CHECK_EVERY has passed since `last_check`, the time (in seconds) the last check
    got through, as the last-update-check file holds it. Anything else there, such as the date
    earlier versions wrote, or a time ahead of the clock, is due."""
    try:
        last = float(last_check)
    except (TypeError, ValueError):
        return True
    return not 0 <= now - last < CHECK_EVERY


def _run(arguments, **kwargs):
    return subprocess.run(arguments, capture_output=True, **kwargs)


def _curl(url, output, max_time, accept=None, size=None, on_progress=None):
    """GitHub's answer's HTTP code and body. With `on_progress`, it's told the fraction of `size`
    bytes in `output` so far, four times a second while curl downloads."""
    arguments = ["/usr/bin/curl", "--silent", "--show-error", "--location", "--max-time", str(max_time),
                 "--output", str(output), "--write-out", "\n%{http_code}"]
    if accept:
        arguments += ["--header", f"Accept: {accept}"]
    if on_progress is None:
        result = _run([*arguments, url], text=True)
        returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
    else:
        process = subprocess.Popen([*arguments, url], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if size:
                    try:
                        on_progress(min(1.0, os.path.getsize(output) / size))
                    except OSError:
                        pass
        returncode = process.returncode
    if returncode != 0:
        raise UpdateError(f"Couldn't reach GitHub ({stderr.strip() or returncode}).")
    body, _, code = stdout.rpartition("\n")
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
    def __init__(self, app, version, data_dir, is_busy, on_status, quit_app, on_toast=None):
        self.status = IDLE
        self.version = None
        self._app = app
        self._current = (info_value(app, "CFBundleShortVersionString") if app else None) or version
        self._bundle_id = info_value(app, "CFBundleIdentifier") if app else None
        self._data_dir = Path(data_dir)
        self._last_check_file = self._data_dir / "last-update-check"
        self._is_busy = is_busy
        self._on_status = on_status
        self._on_toast = on_toast
        self._quit_app = quit_app
        self._feed = os.environ.get("SWITCHPLAYER_UPDATE_FEED") or FEED_URL
        self._team = None
        self._staged = None
        self._wake_observer = None
        self._tick_timer = None
        self._check_pending = False
        self._install_timer = None
        self._install_asked = False  # the player asked for the update held for a Build: only the Build holds it
        self._asked = False  # this check came from Check for Updates…: it installs without waiting for quiet
        self._visible = False  # this check shows on the page from its start
        self._last_failed = False  # the last check couldn't reach GitHub: the next hourly look tries again, quietly
        self._toast = None
        self._toast_id = 0
        self._dismissed = None
        self.off_note = None  # why this copy can't update, for the player, when they can change it
        self.unavailable_reason = self._why_unavailable()

    def _why_unavailable(self):
        if self._app is None:
            return "running from source"
        self._team = own_team(self._app)
        if self._team is None:
            return "this copy isn't signed by a developer team, so updates can't be verified"
        if not in_applications_folder(self._app):
            self.off_note = "move Switch Player to your Applications folder"
            return "Switch Player isn't in the Applications folder"
        if not (os.access(self._app, os.W_OK) and os.access(self._app.parent, os.W_OK)):
            self.off_note = f"Switch Player can't replace itself in {self._app.parent}"
            return self.off_note
        return None

    # ── what the page shows ─────────────────────────────

    def toast(self):
        """What the page's toast shows now, or None: {id, kind, version, progress, reason, stage,
        sticky, fades, held}. A new id is a new toast: one that fades shows for a few seconds from
        when the page first sees it, and is gone here TOAST_KEEP seconds after it began."""
        toast = self._toast
        if toast is None or toast["id"] == self._dismissed:
            return None
        if toast["fades"] and time.monotonic() - toast["at"] > TOAST_KEEP:
            return None
        shown = {key: value for key, value in toast.items() if key != "at"}
        if toast["kind"] == READY:
            shown["held"] = bool(self._is_busy())  # a Build holds it: no Install Now, a × instead
        return shown

    def dismiss(self, toast_id):
        """The toast's × (a failure that stays, or an update held for a Build)."""
        self._dismissed = toast_id

    def _show(self, kind, version=None, progress=None, reason=None, stage=None, sticky=False):
        current = self._toast
        same = current is not None and current["kind"] == kind and current["version"] == version
        if not same:
            self._toast_id += 1
        self._toast = {
            "id": self._toast_id, "kind": kind, "version": version, "progress": progress, "reason": reason,
            "stage": stage, "sticky": sticky, "fades": kind in ("up_to_date", "updated", "off") or (kind == "failed" and not sticky),
            "at": current["at"] if same else time.monotonic(),
        }
        if not same and self._on_toast:
            self._on_toast()

    def _hide(self):
        if self._toast is not None:
            self._toast = None
            if self._on_toast:
                self._on_toast()

    # ── when it checks ──────────────────────────────────

    def start(self):
        """At launch, on the main thread: the launch's check, then the hourly and wake looks."""
        if self.unavailable_reason:
            log.info("Automatic updates off: %s", self.unavailable_reason)
            if self.off_note:
                self._show("off", reason=self.off_note)
            return
        from AppKit import NSWorkspace, NSWorkspaceDidWakeNotification
        from Foundation import NSOperationQueue, NSTimer
        center = NSWorkspace.sharedWorkspace().notificationCenter()
        self._wake_observer = center.addObserverForName_object_queue_usingBlock_(
            NSWorkspaceDidWakeNotification, None, NSOperationQueue.mainQueue(), self._did_wake)
        self._tick_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            TICK, True, lambda timer: self._check_if_due())
        installed = self._take_installed()
        if installed:
            log.info("Updated to %s", installed)
            self._show("updated", version=installed)  # it checked just before it installed
        else:
            self._start_check(asked=False, visible=True)

    def _take_installed(self):
        """The version the App update before this launch installed, if this is it."""
        path = self._data_dir / INSTALLED_FILE
        try:
            version = path.read_text(encoding="utf-8").strip()
            path.unlink()
        except OSError:
            return None
        return version if version == self._current else None

    def _did_wake(self, notification):
        if not self._check_pending:
            self._check_pending = True
            _on_main_later(WAKE_DELAY, self._wake_check)

    def _wake_check(self):
        self._check_pending = False
        self._check_if_due()

    def _check_if_due(self):
        """The hourly and wake looks: a check once CHECK_EVERY has passed since the last one that
        got through, shown on the page; and after a check that couldn't reach GitHub, another, quietly."""
        if self.status != IDLE:
            return
        try:
            last = self._last_check_file.read_text(encoding="utf-8").strip()
        except OSError:
            last = None
        if self._last_failed:
            self._start_check(asked=False, visible=False)
        elif check_is_due(last, time.time()):
            self._start_check(asked=False, visible=True)

    def check_now(self):
        """Check for Updates…, on pywebview's menu thread."""
        if self.unavailable_reason:
            open_releases_page()
        elif self.status == READY:
            _on_main(self._install_if_quiet, True)  # the update already waiting, at once
        elif self.status == IDLE:
            self._start_check(asked=True, visible=True)
        else:
            # An automatic check is under way: it's the player's now, shown, and it installs at once.
            self._asked = True
            if not self._visible:
                self._visible = True
                if self.status == CHECKING:
                    self._show("checking")

    def install_now(self):
        """The toast's Install Now."""
        if self.status == READY:
            _on_main(self._install_if_quiet, True)

    def _start_check(self, asked, visible):
        self._asked, self._visible = asked, visible
        self._set_status(CHECKING)
        if visible:
            self._show("checking")
        threading.Thread(target=self._check, name="update-check", daemon=True).start()

    def _set_status(self, status, version=None):
        self.status, self.version = status, version
        _on_main(self._on_status, status, version)

    # ── checking and downloading ────────────────────────

    def _check(self):
        try:
            release = self._latest_release()
        except Exception as e:
            self._failed("check", e)
            self._last_failed = True
            return
        self._last_failed = False
        try:
            self._last_check_file.parent.mkdir(parents=True, exist_ok=True)
            self._last_check_file.write_text(str(time.time()), encoding="utf-8")
        except OSError as e:
            log.warning("Couldn't note the update check: %s", e)
        if release is None or not is_newer(release[0], self._current):
            self._set_status(IDLE)
            log.info("Up to date at %s", self._current)
            if self._visible:
                self._show("up_to_date")
            else:
                self._hide()
            return
        version, url, size = release
        self._visible = True  # found one: the toast shows from here, even after a quiet check
        log.info("Downloading %s", version)
        self._set_status(DOWNLOADING, version)
        self._show("downloading", version=version, progress=0.0 if size else None)
        try:
            self._staged = self._download(url, size, version)
        except Exception as e:
            # The day's check got through: a bad download waits for the next one, not the next hour.
            self._failed("update", e, version)
            return
        self._set_status(READY, version)
        log.info("%s is ready to install", version)
        _on_main(self._install_if_quiet, self._asked)

    def _failed(self, stage, error, version=None):
        """A check that couldn't reach GitHub ("check"), or an update that couldn't download or
        verify ("update"). Check for Updates…'s failure stays until it's closed; an automatic one
        fades; a quiet retry's isn't shown."""
        self._set_status(IDLE)
        log.error("Update %s failed: %s", "check" if stage == "check" else version, error)
        if self._asked:
            self._show("failed", version=version, reason=str(error), stage=stage, sticky=True)
        elif self._visible:
            self._show("failed", version=version, reason=str(error), stage=stage)
        else:
            self._hide()

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

    def _download(self, url, size, version):
        from Foundation import NSURL, NSFileManager, NSItemReplacementDirectory, NSUserDomainMask
        folder_url, error = NSFileManager.defaultManager().URLForDirectory_inDomain_appropriateForURL_create_error_(
            NSItemReplacementDirectory, NSUserDomainMask, NSURL.fileURLWithPath_(str(self._app)), True, None)
        if folder_url is None:
            raise UpdateError(f"Couldn't make a folder for the download ({error}).")
        folder = Path(folder_url.path())
        try:
            image = folder / "update.dmg"
            code, _ = _curl(url, image, max_time=600, size=size,
                            on_progress=lambda done: self._show("downloading", version=version, progress=done))
            if code not in (200, 0):
                raise UpdateError(f"GitHub answered with an error ({code}).")
            self._set_status(VERIFYING, version)
            self._show("verifying", version=version)
            app = folder / self._app.name
            copy_app_from_image(image, app, self._bundle_id)
            image.unlink(missing_ok=True)
            found = verify(app, self._team, self._bundle_id)
            if not is_newer(found, self._current):
                raise UpdateError(f"The latest release contains version {found}, which isn't newer than this one.")
            return found, app, folder
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    # ── installing ──────────────────────────────────────

    def _install_if_quiet(self, user_initiated=False):
        """On the main thread. Never while a Build runs or waits. The player's own Check for
        Updates… or Install Now doesn't also wait for a quiet keyboard and mouse: they just used
        it, and the toast says when the install is held for a Build, which then installs it once it
        ends. Until it installs, the toast says it's ready."""
        import Quartz
        from AppKit import NSEvent
        from Foundation import NSTimer
        if self.status != READY:
            self._stop_install_timer()
            return
        self._install_asked = self._install_asked or user_initiated  # the retries below remember it
        user_initiated = self._install_asked
        busy = self._is_busy()
        idle = Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType)
        quiet = user_initiated or (idle >= QUIET_SECONDS and NSEvent.pressedMouseButtons() == 0)
        if quiet and not busy:
            self._install()
            return
        self._show(READY, version=self.version)
        if self._install_timer is None:
            self._install_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                INSTALL_RETRY, True, lambda timer: self._install_if_quiet())

    def _stop_install_timer(self):
        self._install_asked = False
        if self._install_timer is not None:
            self._install_timer.invalidate()
            self._install_timer = None

    def _install(self):
        """Say it's installing, long enough for the page to show it, then swap and relaunch."""
        self._stop_install_timer()
        if self._staged is None:
            return
        version = self._staged[0]
        self._set_status(INSTALLING, version)
        self._show(INSTALLING, version=version)
        _on_main_later(INSTALL_DELAY, self._replace)

    def _replace(self):
        from Foundation import NSURL, NSFileManager, NSFileManagerItemReplacementUsingNewMetadataOnly
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
            self._show("failed", version=version, stage="install", sticky=True,
                       reason="Switch Player couldn't replace itself in the Applications folder.")
            return
        try:
            (self._data_dir / INSTALLED_FILE).write_text(version, encoding="utf-8")  # the new copy says so
        except OSError:
            pass
        log.info("Installed %s; relaunching", version)
        relaunch(self._app, background=True)
        self._quit_app()


def _on_main(function, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(function, *args)


def _on_main_later(delay, function, *args):
    from PyObjCTools import AppHelper
    AppHelper.callLater(delay, function, *args)


def open_releases_page():
    from AppKit import NSWorkspace
    from Foundation import NSURL
    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(RELEASES_URL))
