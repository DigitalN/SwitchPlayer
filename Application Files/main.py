"""Switch Player's pywebview application and the page's in-process API."""

import base64
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import builds
import game_settings
import launch
import library
import migration
import nxnative
import prerequisites
import records
import settings
import titledb
import updater

log = logging.getLogger("switchplayer")

APP_NAME = "Switch Player"
VERSION = "0.1.10"
BUNDLE_ID = "com.digitaln.switchplayer"
BUILD_LOG_FIRST = 512 * 1024  # how much of a long Build log the pane starts with
BUILD_LOG_PIECE = 256 * 1024  # and reads at most at a time
# A Game app's saved "Show this window at launch" (Launcher.swift's @AppStorage), which nxnative's
# front-end contract doesn't name. Only for a settings list the Settings panel can't draw: opening
# the game's own Settings window removes it first, under ADR 0001's second exception.
SETTINGS_WINDOW_CHOICE = "window.show"
# After Play, how long the Game app has to appear before its start counts as Didn't start, and how
# often a started game is looked at: its processes (the Game app, and the host its launcher runs as
# a child, docs/frontends.md) and whether any of them has a window on screen yet.
PLAY_APPEARS = 15
PLAY_WATCH = 0.5
if os.environ.get("SWITCHPLAYER_DATA_DIR"):
    DATA_DIR = Path(os.environ["SWITCHPLAYER_DATA_DIR"]).expanduser()
    LOG_DIR = DATA_DIR / "Logs"
else:
    DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
    LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME


def dispatch(function, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(function, *args)


def child_pids(pid):
    """The processes `pid` started (libproc's proc_listchildpids)."""
    import ctypes
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    libproc.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    buffer = (ctypes.c_int * 256)()
    count = libproc.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
    return [child for child in buffer[:max(0, min(count, 256))] if child > 0]


def app_bundle_id(game):
    """A Game app's CFBundleIdentifier, which is also its saved choices' preferences domain:
    nxnative's default only unless the app was packed with another."""
    return game.app.bundle_id or f"org.nxnative.{game.title_id.lower()}"


class Native:
    def open_app(self, path, no_launcher):
        argv = ["/usr/bin/open", str(path)]
        if no_launcher:
            argv += ["--args", "--no-launcher"]
        subprocess.run(argv, check=True)

    def read_choices(self, bundle_id, keys):
        """A Game app's saved choices among `keys`: only strings, as its launcher reads them."""
        from Foundation import CFPreferencesAppSynchronize, CFPreferencesCopyAppValue
        CFPreferencesAppSynchronize(bundle_id)  # anything the game's own window saved since
        found = {}
        for key in keys:
            value = CFPreferencesCopyAppValue(key, bundle_id)
            if isinstance(value, str):
                found[key] = str(value)
        return found

    def write_choices(self, bundle_id, changes):
        """Save {key: string, or None to remove} in a Game app's saved choices. True once saved."""
        from Foundation import CFPreferencesAppSynchronize, CFPreferencesSetAppValue
        for key, value in changes.items():
            CFPreferencesSetAppValue(key, value, bundle_id)
        return bool(CFPreferencesAppSynchronize(bundle_id))

    def game_processes(self, game):
        """The running Game app's processes: those macOS knows by its bundle ID, and every
        process they started (its launcher runs the game's host as a child)."""
        from AppKit import NSRunningApplication
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(app_bundle_id(game))
        found = {int(app.processIdentifier()) for app in apps if not app.isTerminated()}
        todo = list(found)
        while todo:
            for child in child_pids(todo.pop()):
                if child not in found:
                    found.add(child)
                    todo.append(child)
        return found

    @staticmethod
    def _windows(pids, on_screen):
        """The owners of the ordinary windows (layer 0, bigger than a dialog's corner) of `pids`.
        Only the owner and size are read, which needs no Screen Recording permission."""
        import Quartz
        options = Quartz.kCGWindowListExcludeDesktopElements
        options |= Quartz.kCGWindowListOptionOnScreenOnly if on_screen else Quartz.kCGWindowListOptionAll
        owners = []
        for window in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
            bounds = window.get(Quartz.kCGWindowBounds) or {}
            if (int(window.get(Quartz.kCGWindowOwnerPID, -1)) in pids and window.get(Quartz.kCGWindowLayer) == 0
                    and bounds.get("Width", 0) >= 200 and bounds.get("Height", 0) >= 150):
                owners.append(int(window[Quartz.kCGWindowOwnerPID]))
        return owners

    def window_shown(self, pids):
        """Whether any of `pids` has a window on screen."""
        return bool(self._windows(pids, on_screen=True))

    def bring_to_front(self, pids):
        """Activate the game: the process with its window, in whatever Space that is. On the
        main thread."""
        from AppKit import NSApplicationActivateAllWindows, NSRunningApplication
        for pid in dict.fromkeys(self._windows(pids, on_screen=False)):
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is not None and app.activateWithOptions_(NSApplicationActivateAllWindows):
                return True
        return False

    def forget_settings_window_choice(self, bundle_id):
        """Remove a Game app's saved "Show this window at launch", so its launcher falls back to
        nxnative's default and shows the Settings window."""
        from Foundation import CFPreferencesAppSynchronize, CFPreferencesSetAppValue
        CFPreferencesSetAppValue(SETTINGS_WINDOW_CHOICE, None, bundle_id)
        if not CFPreferencesAppSynchronize(bundle_id):
            log.warning("Couldn't save %s's preferences", bundle_id)

    def reveal(self, path):
        from AppKit import NSURL, NSWorkspace
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(path))])

    def open_in_console(self, path):
        subprocess.run(["/usr/bin/open", "-a", "Console", str(path)], check=True)

    def copy_text(self, text):
        """Put plain text on the clipboard (the page's own clipboard calls aren't reliable in the
        app's web view). On the main thread."""
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        return bool(pasteboard.setString_forType_(text, NSPasteboardTypeString))

    def game_running(self, game):
        if not game.app:
            return False
        from AppKit import NSRunningApplication
        return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(app_bundle_id(game)))

    def confirm(self, message, action="Quit", detail=""):
        from AppKit import NSAlert, NSAlertFirstButtonReturn
        launch.activate_app()
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_(action)
        alert.addButtonWithTitle_("Cancel")
        return alert.runModal() == NSAlertFirstButtonReturn

    def alert(self, title, message):
        """A result the player asked for, such as Check for nxnative Updates…'s."""
        from AppKit import NSAlert
        launch.activate_app()
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.runModal()

    def prevent_sleep(self):
        return subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def allow_sleep(self, process):
        process.terminate()

    def notify(self, title, message):
        from AppKit import NSUserNotification, NSUserNotificationCenter
        notification = NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setInformativeText_(message)
        NSUserNotificationCenter.defaultUserNotificationCenter().deliverNotification_(notification)

    def choose_folder(self, window):
        import webview
        return window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)

    def choose_keys(self, window):
        import webview
        return window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                         file_types=("Keys (*.keys)",))

    def install_lifecycle(self, app):
        import AppKit
        import objc
        from webview.platforms import cocoa

        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

        def applicationShouldTerminate_(delegate, sender):
            return AppKit.NSTerminateNow if app.application_should_terminate() else AppKit.NSTerminateCancel

        def reopen(delegate, sender, has_visible_windows):
            if has_visible_windows:
                return True
            app.reopen()
            return False

        objc.classAddMethods(cocoa.BrowserView.AppDelegate, [
            applicationShouldTerminate_,
            objc.selector(reopen, selector=b"applicationShouldHandleReopen:hasVisibleWindows:", signature=b"Z@:@Z"),
        ])
        center = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
        self._wake_observer = center.addObserverForName_object_queue_usingBlock_(
            AppKit.NSWorkspaceDidWakeNotification, None, None, lambda notification: app._refresh_freshness(),
        )

    def show_bundle_icon(self, icon):
        """The Dock tile shows the app's own icon file this launch, whatever macOS has cached."""
        import AppKit
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is not None:
            AppKit.NSApp.setApplicationIconImage_(image)

    def convert_icon(self, source, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        part = output.with_suffix(".partial.jpg")
        try:
            subprocess.run(["/usr/bin/sips", "-s", "format", "jpeg", str(source), "--out", str(part)],
                           check=True, capture_output=True)
            os.replace(part, output)
        except (OSError, subprocess.CalledProcessError):
            part.unlink(missing_ok=True)


class SwitchPlayerApp:
    def __init__(self, data_dir=None, first_launch=settings.nxplay_preferences, builder=None,
                 native=None, dispatch=dispatch, save_root=None, game_running=None,
                 clock=time.monotonic, watch_plays=True):
        self._native = native or Native()
        self._dispatch = dispatch
        self._data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self._log_dir = self._data_dir / "Logs" if data_dir is not None else LOG_DIR
        self._save_root = Path(save_root) if save_root is not None else self._default_save_root()
        self._game_running = game_running or self._native.game_running
        self._settings = settings.Settings(self._data_dir / "settings.json", first_launch=first_launch)
        self._records = records.Records(self._data_dir / "build-records.json")
        self._build_icons = self._data_dir / "Icons" / "Builds"  # each game's icon from its Build's game event
        self._build_files = {}  # game id → its game files when its running Build started
        self._build_commits = {}  # game id → the nxnative commit its running Build runs with
        self._titledb = titledb.TitleDB(self._data_dir / "titledb")
        self._builder = builder
        self._games = {}
        self._scan_folder = None
        self._last_scan = None
        self._title_pending = set()
        self._title_thread = None
        self._title_lock = threading.Lock()
        self._freshness = {}
        self._freshness_lock = threading.Lock()
        self._freshness_fetch_lock = threading.Lock()
        self._freshness_thread = None
        self._freshness_checked = None
        self._freshness_offline = None  # why the last check couldn't fetch nxnative, when it couldn't
        # Games started or running: game id → {since, pids, window, ours} (see _update_play)
        self._clock = clock
        self._plays = {}
        self._didnt_start = set()  # games whose last Play ended before their window showed
        self._plays_lock = threading.Lock()
        self._play_thread = None
        self._watch_plays = watch_plays
        self._window = None
        self._caffeinate = None
        self._updater = updater.Updater(launch.bundle_path(), VERSION, self._data_dir,
                                        is_busy=lambda: bool(self._builder and self._builder.busy),
                                        on_status=self._update_status, quit_app=self._quit_for_update,
                                        on_toast=self._update_toast_changed)

    def _update_status(self, status, version):
        log.info("App update status: %s%s", status, f" {version}" if version else "")

    def _update_toast_changed(self):
        """A new step or result for the App update toast: the page asks for it now rather than at
        its next poll, so a step as short as Installing… still shows. evaluate_js waits for the
        page, so never on the main thread; before the page has loaded, nudge() isn't there yet."""
        window = self._window
        if window is None:
            return

        def nudge():
            try:
                window.evaluate_js("window.nudge && nudge()")
            except Exception as e:  # a page that's loading or gone: its next poll shows it
                log.debug("Couldn't nudge the page: %s", e)
        threading.Thread(target=nudge, name="toast-nudge", daemon=True).start()

    def _quit_for_update(self):
        import AppKit
        AppKit.NSApp.terminate_(None)

    def _default_save_root(self):
        if os.environ.get("SWITCHPLAYER_DATA_DIR"):
            return self._data_dir / "nxnative-saves"
        return Path.home() / "Library" / "Application Support" / "nxnative"

    @staticmethod
    def _art_key(path):
        return f"{path.name}:{path.stat().st_mtime_ns}" if path else None

    def _get_builder(self):
        if self._builder is None:
            self._builder = builds.Builder(self._prepare, self._log_dir / "Builds", on_finish=self._build_finished,
                                          on_busy=self._busy_changed, on_game=self._build_named)
        return self._builder

    def _build_finished(self, job, ok):
        self._last_scan = None
        files = self._build_files.pop(job.game_id, None)
        commit = self._build_commits.pop(job.game_id, None)
        game = self._games.get(job.game_id)
        name = self._game_name(game) if game else job.name  # as the tile says it, nxnative's name once it gave one
        if ok:
            # What this Build used: Out of date and the changes since the last Build go by it.
            self._records.set_build(job.game_id, files, commit)
            with self._freshness_lock:
                if game:
                    self._freshness.pop(game.title_id, None)
                self._freshness_checked = None
            self._dispatch(self._native.notify, name, "Build finished")
        elif job.state == "failed":
            self._dispatch(self._native.notify, name, "Build failed")

    def _build_named(self, job, event):
        """The build script's `game` event: the game's own name and icon, about a minute into its
        Build. Both are kept at once (the icon copied, as the build folder it's in may be cleared
        when the Build ends), so the tile shows them from now on, even if this Build stops or fails,
        until the Game app exists to speak for itself."""
        tid, name, icon = event.get("title_id"), event.get("name"), event.get("icon")
        if not library.is_title_id(tid) or not isinstance(name, str) or not name.strip():
            log.warning("Ignored a game event without a title ID and a name: %r", event)
            return
        tid = tid.upper()
        output = self._build_icons / f"{builds.safe_name(job.game_id)}.jpg"
        kept = None
        try:
            if isinstance(icon, str) and Path(icon).is_file():
                output.parent.mkdir(parents=True, exist_ok=True)
                part = output.with_name(output.name + ".partial")
                shutil.copyfile(icon, part)  # a new modified time, so the page asks for the new art
                os.replace(part, output)
                kept = output.name
            else:
                output.unlink(missing_ok=True)  # another title's, or a game without an icon now
        except OSError as e:
            log.warning("Couldn't keep the icon of %s: %s", job.name, e)
        self._records.set_game(job.game_id, tid, name.strip(), kept)

    def _named(self, game):
        """What a Build's `game` event said about this game ({title_id, name, icon}), or None
        before one, or when the Game folder has since come to hold another title."""
        record = self._records.game(game.id)
        if record and game.title_id and record["title_id"].upper() == game.title_id.upper():
            return record
        return None

    def _busy_changed(self, busy):
        if busy and self._caffeinate is None:
            self._caffeinate = self._native.prevent_sleep()
        elif not busy and self._caffeinate is not None:
            self._native.allow_sleep(self._caffeinate)
            self._caffeinate = None

    def attach_window(self, window):
        self._window = window

    def _native_dialog(self, function, *args):
        """Run an AppKit dialog on the main thread and wait for its answer: the page's API calls
        arrive on pywebview's own threads, where AppKit refuses to make windows."""
        if threading.current_thread() is threading.main_thread():
            return function(*args)
        done = threading.Event()
        result, errors = [], []

        def run():
            try:
                result.append(function(*args))
            except Exception as e:
                errors.append(e)
            done.set()

        self._dispatch(run)
        done.wait()
        if errors:
            raise errors[0]
        return result[0]

    def close_window(self):
        if self._window:
            self._dispatch(self._window.hide)
        return False

    def reopen(self):
        if self._window:
            self._dispatch(self._window.show)

    def application_should_terminate(self):
        if self._builder:
            if self._builder.busy and not self._native.confirm("Stop the Build and quit?"):
                return False
            self._builder.stop_all()
        return True

    def choose_library(self):
        if not self._window:
            return
        paths = self._native.choose_folder(self._window)
        if paths:
            self._settings.update({"library_folder": paths[0]})
            self._last_scan = None

    def choose_keys(self):
        if not self._window:
            return
        paths = self._native.choose_keys(self._window)
        if paths:
            self._settings.update({"keys_file": paths[0]})

    def show_setup(self):
        if self._window:
            self._window.evaluate_js("showSetup()")

    def check_nxnative(self):
        """Check for nxnative Updates…: compare in the background, then answer with an alert."""
        threading.Thread(target=self._check_nxnative, name="nxnative-check", daemon=True).start()

    def _refresh_icon_if_updated(self, app):
        """A new version's first launch: show the bundle's own icon in the Dock now, and refresh
        what macOS cached for this path in the background (launch.refresh_icon_cache). Later
        launches leave the Dock tile to macOS, with its icon styles."""
        if app is None or not launch.is_new_version(self._data_dir, VERSION):
            return
        icon = launch.icon_file(app)
        if icon:
            self._native.show_bundle_icon(icon)
        threading.Thread(target=launch.refresh_icon_cache, args=(app, self._data_dir, VERSION),
                         name="icon-refresh", daemon=True).start()

    def _check_nxnative(self):
        title, message = self._nxnative_report()
        self._dispatch(self._native.alert, title, message)

    def run(self, webview=None):
        if webview is None:
            import webview
        self._native.install_lifecycle(self)
        self._refresh_icon_if_updated(launch.bundle_path())
        menus = [webview.Menu("__app__", [
            webview.menu.MenuAction("Choose Library Folder…", self.choose_library),
            webview.menu.MenuAction("Choose Keys File…", self.choose_keys),
            webview.menu.MenuAction("Build Setup…", self.show_setup),
            webview.menu.MenuAction("Check for Updates…", self._updater.check_now),
            webview.menu.MenuAction("Check for nxnative Updates…", self.check_nxnative),
        ])]
        window = webview.create_window(
            APP_NAME, html=(Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8"),
            js_api=Api(self), width=1200, height=750, min_size=(420, 400), menu=menus,  # three columns beside the Build Log
        )
        self.attach_window(window)
        window.events.closing += self.close_window
        self._updater.start()
        webview.start(private_mode=True, debug=False, menu=menus)

    def _prepare(self, job):
        game = self._games.get(job.game_id)
        try:
            self._build_files[job.game_id] = nxnative.game_files(game) if game else None
        except OSError:
            self._build_files[job.game_id] = None
        values = self._settings.get()
        copy = nxnative.Copy(values["nxnative_checkout"] or self._data_dir / "nxnative",
                             own=values["nxnative_checkout"] is None)
        environment = nxnative.Environment(self._data_dir / "nxnative-python",
                                           Path(__file__).parent / "nxnative-python" / "pyproject.toml")
        # Each step on the tile with how long it has taken, and what its tools print in the Build
        # log; Stop interrupts them (builds.Job.track).
        def step(text):
            job.progress = {"preparing": text, "since": time.monotonic()}
            job.say(f"── {text}")

        def out(line):
            job.say(f"   {line}")

        step("Downloading nxnative" if copy.needs_download else "Updating nxnative")
        copy.update(out=out, started=job.track, say=job.say)
        self._build_commits[job.game_id] = copy.build_commit()
        if not environment.ready():
            step("Installing Python packages")
        environment.ensure(out=out, started=job.track)
        if job.stop_requested:
            return None
        source = game.nxplay_save_path() if game else None
        destination = self._save_root / game.title_id / "save" if source else None
        if source and migration.first_build(source, destination):  # never cut short: Stop waits for it
            job.say(f"── Brought NX Play's save across to {destination}")
        command = nxnative.build_command(copy, environment, job.path, keys_file=values["keys_file"],
                                         options=values["build_options"].get(job.game_id, ""))
        job.progress = None  # the build script's own events take over
        return command

    def _library(self):
        values = self._settings.get()
        folder = values["library_folder"]
        if not folder:
            self._games = {}
            self._scan_folder = None
            return {"folder_name": None, "games": [], "update": self._updater.toast()}
        now = time.monotonic()
        if folder != self._scan_folder or self._last_scan is None or now - self._last_scan >= 2:
            games = library.scan(folder)
            self._games = {game.id: game for game in games}
            self._scan_folder = folder
            self._last_scan = now
            self._update_titles(games)
            self._refresh_freshness()
        games = sorted(self._games.values(),
                       key=lambda game: library.sort_key(self._game_name(game)))
        result = []
        with self._freshness_lock:
            freshness = dict(self._freshness)
        for game in games:
            status = self._builder.status(game.id) if self._builder else None
            current = freshness.get(game.title_id, {})
            state = "ready" if game.app else "nxplay" if game.nxplay_build else "none"
            result.append({
                "id": game.id,
                "name": self._game_name(game),
                "title_id": game.title_id,
                "art": self._art_key(self._art_path(game)),
                "state": status["state"] if status else state,
                "app": bool(game.app),
                "play": self._update_play(game) if game.app else None,
                "progress": status["progress"] if status else None,
                "error": status["error"] if status else None,
                "out_of_date": current.get("out_of_date", False) or self._files_changed(game),
                "newer": current.get("newer", 0),
                "log": self._game_log(game).exists(),
                "build_log": bool(self._build_log_file(game.id)),
                "nxplay_save": game.nxplay_save,
                "options": values["build_options"].get(game.id, ""),
            })
        return {"folder_name": Path(folder).name, "games": result, "update": self._updater.toast()}

    def _files_changed(self, game):
        """Whether the Game folder gained files since the Game app's Build: by the record that
        Build wrote (records.py), or, for a Game app built before there were records, by the files'
        modification dates until its next Build writes one. Never a record made from those dates:
        it would take a file copied in with an old date for one the Build had used."""
        return nxnative.game_files_changed(game, self._records.files(game.id))

    def _refresh_freshness(self, force=False):
        with self._freshness_lock:
            if self._freshness_thread and self._freshness_thread.is_alive():
                return
            if not force and self._freshness_checked and time.time() - self._freshness_checked < 86400:
                return
            self._freshness_checked = time.time()
            self._freshness_thread = threading.Thread(target=self._fetch_freshness_background, name="freshness", daemon=True)
            self._freshness_thread.start()

    def _fetch_freshness_background(self):
        try:
            self._fetch_freshness()
        except (nxnative.NxnativeError, ValueError):
            log.info("Couldn't check nxnative freshness", exc_info=True)

    def _fetch_freshness(self):
        values = self._settings.get()
        copy = nxnative.Copy(values["nxnative_checkout"] or self._data_dir / "nxnative",
                             own=values["nxnative_checkout"] is None)
        games = [game for game in self._games.values() if game.app]
        apps = [game.app for game in games]
        built = {game.app.title_id: self._records.commit(game.id) for game in games}
        with self._freshness_fetch_lock:
            freshness = copy.freshness(apps, built)
        with self._freshness_lock:
            self._freshness = freshness
            self._freshness_offline = copy.fetch_error
            self._freshness_checked = time.time()
        return freshness

    def _nxnative_report(self):
        """The alert's title and message for Check for nxnative Updates…, in the same terms as
        the tiles: how many games are Out of date and how many have other changes since their
        last Build. Counts, not names, so a big Library's alert stays short; the tiles say which."""
        if self._last_scan is None:
            self._library()
        built = sorted((game for game in self._games.values() if game.app),
                       key=lambda game: library.sort_key(self._game_name(game)))
        if not built:
            return "No games built in Switch Player yet", "Your first Build will use the latest nxnative."
        try:
            freshness = self._fetch_freshness()
        except (nxnative.NxnativeError, ValueError, OSError) as e:
            log.warning("Couldn't check nxnative: %s", e)
            return "Switch Player couldn't check nxnative", str(e)
        out_of_date = changed = 0
        for game in built:
            current = freshness.get(game.title_id, {})
            if current.get("out_of_date") or self._files_changed(game):
                out_of_date += 1
            elif current.get("newer"):
                changed += 1
        with self._freshness_lock:
            offline = self._freshness_offline
        # Offline, the answer is only as new as the last fetch, and says so.
        stale = f"Switch Player couldn't reach GitHub ({offline}), so this compares with the nxnative it last fetched."
        if not out_of_date and not changed:
            if offline:
                return "Switch Player couldn't reach GitHub", f"Compared with the nxnative it last fetched, your games are up to date. ({offline})"
            return "Your games are up to date with nxnative", "Your next Build will use the latest nxnative."
        count = out_of_date + changed
        parts = []
        if out_of_date:
            parts.append(f"{out_of_date:,} {'is' if out_of_date == 1 else 'are'} out of date, "
                         f"consider rebuilding {'it' if out_of_date == 1 else 'them'}.")
            if changed:
                parts.append(f"{changed:,} more {'has' if changed == 1 else 'have'} changes since "
                             f"{'its' if changed == 1 else 'their'} last Build.")
        elif changed == 1:
            parts.append("It isn't out of date. It has changes since its last Build.")
        else:
            parts.append("None are out of date. Each has changes since its last Build.")
        if offline:
            parts.append(stale)
        return f"nxnative has changes for {count} game{'' if count == 1 else 's'}", " ".join(parts)

    def _game_name(self, game):
        named = self._named(game)
        return game.name(self._titledb.name(game.title_id), named and named["name"])

    def _update_titles(self, games):
        missing = [game.title_id for game in games
                   if game.title_id and (not self._titledb.name(game.title_id) or not self._titledb.art(game.title_id))]
        with self._title_lock:
            self._title_pending.update(missing)
            if not self._title_pending or (self._title_thread and self._title_thread.is_alive()):
                return
            self._title_thread = threading.Thread(target=self._fetch_titles, name="titledb", daemon=True)
            self._title_thread.start()

    def _fetch_titles(self):
        while True:
            with self._title_lock:
                tids = sorted(self._title_pending)
                self._title_pending.clear()
            if tids:
                self._titledb.update(tids)
            with self._title_lock:
                if not self._title_pending:
                    self._title_thread = None
                    return

    def _art_path(self, game):
        """The eShop's art, else the Game app's own icon, else the icon a Build's `game` event gave
        (the one its Game app will get), else none: a plain tile."""
        art = self._titledb.art(game.title_id)
        if art:
            return art
        icon = game.app.path / "Contents" / "Resources" / "AppIcon.icns" if game.app else None
        if icon and icon.is_file():
            output = self._data_dir / "Icons" / f"{builds.safe_name(game.id)}-{icon.stat().st_mtime_ns}.jpg"
            if not output.is_file():
                self._native.convert_icon(icon, output)
            if output.is_file():
                return output
        named = self._named(game)
        kept = self._build_icons / Path(named["icon"]).name if named and named["icon"] else None
        return kept if kept and kept.is_file() else None

    def _game_log(self, game):
        if self._data_dir == DATA_DIR and not os.environ.get("SWITCHPLAYER_DATA_DIR"):
            return Path.home() / "Library" / "Logs" / "nxnative" / f"{game.title_id}.log"
        return self._data_dir / "nxnative-logs" / f"{game.title_id}.log"

    def _play(self, game_id):
        """Play: start the Game app straight into its game, which shows as Starting… until its
        window is on screen. A game already starting or running is never started again: one
        with its window up comes to the front."""
        game = self._games.get(game_id)
        if not game or not game.app:
            return
        if self._update_play(game) in ("starting", "playing"):
            with self._plays_lock:
                play = self._plays.get(game_id)
                pids = set(play["pids"]) if play and play["window"] else None
            if pids:
                self._dispatch(self._native.bring_to_front, pids)
            return
        with self._plays_lock:
            self._didnt_start.discard(game_id)
            self._plays[game_id] = {"since": self._clock(), "pids": set(), "window": False, "ours": True}
        self._start_play_watch()
        self._dispatch(self._native.open_app, game.app.path, True)

    def _update_play(self, game):
        """Where a game's Play stands, from macOS: "starting" (its Game app is running, or Play
        was just pressed, with no window on screen yet), "playing" (a window has shown, until
        it quits), "didnt_start" (the last Play ended before a window showed), else None.
        A start from Finder or the Dock shows the same way, but never as Didn't start."""
        try:
            pids = self._native.game_processes(game)
        except Exception as e:
            log.warning("Couldn't look for %s's processes: %s", game.id, e)
            pids = set()
        with self._plays_lock:
            play = self._plays.get(game.id)
            seen = bool(play and play["window"])
        shown = seen
        if pids and not seen:
            try:
                shown = bool(self._native.window_shown(pids))
            except Exception as e:
                log.warning("Couldn't look for %s's window: %s", game.id, e)
        now = self._clock()
        started = False
        with self._plays_lock:
            play = self._plays.get(game.id)
            if pids:
                if play is None:
                    play = self._plays[game.id] = {"since": now, "pids": set(), "window": False, "ours": False}
                    started = True
                play["pids"] = pids
                play["window"] = play["window"] or shown  # once shown, Playing until it quits, in any Space
                state = "playing" if play["window"] else "starting"
            elif play and play["ours"] and not play["pids"] and now - play["since"] < PLAY_APPEARS:
                state = "starting"  # Play was pressed; macOS hasn't started the Game app yet
            else:
                if play:
                    del self._plays[game.id]
                    if play["ours"] and not play["window"]:
                        self._didnt_start.add(game.id)
                state = "didnt_start" if game.id in self._didnt_start else None
        if started:
            self._start_play_watch()
        return state

    def _start_play_watch(self):
        """Look at started games twice a second, whether or not the page is polling (it stops
        while the window is closed, or hidden behind a fullscreen game), so a game whose window
        showed is never taken for one that didn't start."""
        with self._plays_lock:
            if not self._watch_plays or self._play_thread is not None:
                return
            self._play_thread = threading.Thread(target=self._play_watch, name="play-watch", daemon=True)
            self._play_thread.start()

    def _play_watch(self):
        while True:
            with self._plays_lock:
                if not self._plays:
                    self._play_thread = None
                    return
                ids = list(self._plays)
            for game_id in ids:
                game = self._games.get(game_id)
                if game and game.app:
                    self._update_play(game)
                else:
                    with self._plays_lock:
                        self._plays.pop(game_id, None)
            time.sleep(PLAY_WATCH)

    def _game_settings(self, game_id):
        """The Settings panel's contents: {name, status, running, rows} (game_settings.rows),
        or None for a game without a Game app."""
        game = self._games.get(game_id)
        if not game or not game.app:
            return None
        status, settings = game_settings.settings_list(game.app.path)
        rows = []
        if status == "ok":
            try:
                saved = self._native.read_choices(app_bundle_id(game), [setting.key for setting in settings])
            except Exception as e:
                log.warning("Couldn't read %s's saved choices: %s", game_id, e)
                saved = {}
            rows = game_settings.rows(settings, saved, self._fixed_settings(game))
        try:
            running = bool(self._game_running(game))
        except Exception as e:
            log.warning("Couldn't tell whether %s is running: %s", game_id, e)
            running = False
        return {"name": self._game_name(game), "status": status, "running": running, "rows": rows}

    def _fixed_settings(self, game):
        return game_settings.fixed_values(game.app.path, self._save_root / game.title_id / "env.txt")

    def _save_game_settings(self, game_id, values):
        """The Settings panel's Save: {env: value} into the Game app's saved choices, as its own
        Settings window would save them. True once saved."""
        game = self._games.get(game_id)
        if not game or not game.app or not isinstance(values, dict):
            return False
        status, settings = game_settings.settings_list(game.app.path)
        if status != "ok":
            return False
        changes = game_settings.changes(settings, values, self._fixed_settings(game))
        try:
            saved = bool(self._native.write_choices(app_bundle_id(game), changes))
        except Exception as e:
            log.warning("Couldn't save %s's Game settings: %s", game_id, e)
            return False
        if not saved:
            log.warning("Couldn't save %s's Game settings: its preferences weren't written", game_id)
        return saved

    def _open_settings_window(self, game_id):
        """The Settings panel's way out for a settings list it can't draw: the Game app with its
        own Settings window, even when the player turned "Show this window at launch" off. A
        failure to remove that choice still opens the Game app, which may then start the game."""
        game = self._games.get(game_id)
        if game and game.app:
            try:
                self._native.forget_settings_window_choice(app_bundle_id(game))
            except Exception as e:
                log.warning("Couldn't turn %s's Settings window back on: %s", game_id, e)
            self._dispatch(self._native.open_app, game.app.path, False)

    def _show_log(self, game_id):
        game = self._games.get(game_id)
        if game:
            path = self._game_log(game)
            if path.exists():
                self._dispatch(self._native.reveal, path)

    def _build_log(self, game_id, offset=0, key=None):
        """The next piece of a game's Build log for the Build Log pane, from byte `offset` of the
        file `key` names: {key, offset, text}, whole lines only. Another file than `key` (a new
        Build of that game) starts again from its beginning, or its last BUILD_LOG_FIRST bytes."""
        path = self._build_log_file(game_id)
        if not path:
            return None
        try:
            with open(path, "rb") as f:
                stat = os.fstat(f.fileno())
                current = f"{stat.st_ino}:{getattr(stat, 'st_birthtime', stat.st_ctime)}"
                if key != current or not isinstance(offset, int) or not 0 <= offset <= stat.st_size:
                    offset = max(0, stat.st_size - BUILD_LOG_FIRST)
                    if offset:
                        f.seek(offset)
                        offset += len(f.readline())  # from the start of a line
                f.seek(offset)
                data = f.read(BUILD_LOG_PIECE)
        except OSError:
            return None
        whole = data[:data.rfind(b"\n") + 1]  # a line still being written comes with the next piece
        if not whole and len(data) == BUILD_LOG_PIECE:  # unless it's longer than a piece
            whole = data
        return {"key": current, "offset": offset + len(whole), "text": whole.decode("utf-8", "replace")}

    def _build_log_file(self, game_id):
        """The game's last Build log, from this launch or an earlier one."""
        if self._builder:
            return self._builder.build_log(game_id)
        path = builds.log_path(self._log_dir / "Builds", game_id)  # no Build yet this launch
        return path if path.is_file() else None

    def _open_build_log(self, game_id):
        path = self._build_log_file(game_id)
        if path:
            self._dispatch(self._native.open_in_console, path)

    def _copy_build_log(self, game_id):
        """Copy Log: the game's whole Build log, as the file holds it (the pane shows at most its
        last lines, without nxnative's [RED] markers). True once it's on the clipboard."""
        path = self._build_log_file(game_id)
        if not path:
            return False
        try:
            text = path.read_bytes().decode("utf-8", "replace")
        except OSError as e:
            log.warning("Couldn't copy the Build log of %s: %s", game_id, e)
            return False
        if not self._native_dialog(self._native.copy_text, text):
            log.warning("Couldn't copy the Build log of %s: the clipboard refused it", game_id)
            return False
        return True

    def _ui_state(self):
        values = self._settings.get()
        pane = values["build_log_pane"]
        return {"log_open": pane["open"], "log_width": pane["width"], "log_game": pane["game"],
                "settings_advanced": values["settings_advanced"]}

    def _set_ui_state(self, values):
        if not isinstance(values, dict):
            return
        if isinstance(values.get("settings_advanced"), bool):
            self._settings.update({"settings_advanced": values["settings_advanced"]})
        pane = dict(self._settings.get()["build_log_pane"])
        valid = {"open": lambda v: isinstance(v, bool),
                 "width": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
                 "game": lambda v: v is None or isinstance(v, str)}
        for ours, theirs in (("open", "log_open"), ("width", "log_width"), ("game", "log_game")):
            if theirs in values and valid[ours](values[theirs]):  # anything else keeps what's saved
                pane[ours] = values[theirs]
        self._settings.update({"build_log_pane": pane})

    def _show_in_finder(self, game_id):
        game = self._games.get(game_id)
        if game:
            self._dispatch(self._native.reveal, game.app.path if game.app else game.path)

    def _art(self, game_id):
        game = self._games.get(game_id)
        path = self._art_path(game) if game else None
        if not path:
            return None
        try:
            data = base64.b64encode(path.read_bytes()).decode()
        except OSError:
            return None
        return f"data:image/jpeg;base64,{data}"

    def _build(self, game_id):
        game = self._games.get(game_id)
        if game:
            self._get_builder().build(game.id, game.path, self._game_name(game))

    def _stop(self, game_id):
        if self._builder:
            self._builder.stop(game_id)

    def _set_build_options(self, game_id, text):
        self._settings.set_build_options(game_id, text)

    def _import_nxplay_save(self, game_id):
        """Import NX Play Save…: ask with both dates, then say what happened, every dialog native
        and on the main thread. True only when the save was imported."""
        game = self._games.get(game_id)
        if not game or not game.nxplay_save:
            return False
        name = self._game_name(game)
        failed = "Switch Player couldn't import the save"
        if self._game_running(game):
            self._native_dialog(self._native.alert, f"Quit {name} first",
                                "Switch Player can't replace a save while its game is running.")
            return False
        source = game.nxplay_save_path()
        destination = self._save_root / game.title_id / "save"
        try:
            details = migration.import_details(source, destination, running=False)
        except nxnative.NxnativeError as e:
            log.warning("NX Play save import: %s", e)
            self._native_dialog(self._native.alert, failed, str(e))
            return False

        def date(value):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "No save yet"
        detail = (f"NX Play save: {date(details['nxplay_date'])}\n"
                  f"Current save: {date(details['current_date'])}\n\n"
                  "The current save will be backed up before it is replaced.")
        if not self._native_dialog(self._native.confirm, f"Import the NX Play save for {name}?", "Import", detail):
            return False
        if self._game_running(game):
            self._native_dialog(self._native.alert, f"Quit {name} first",
                                "Switch Player can't replace a save while its game is running.")
            return False
        try:
            backup = migration.import_save(source, destination)
        except nxnative.NxnativeError as e:
            log.warning("NX Play save import: %s", e)
            self._native_dialog(self._native.alert, failed, str(e))
            return False
        self._native_dialog(self._native.alert, "NX Play save imported",
                            f"Your previous save was backed up to {backup}." if backup
                            else f"{name} had no save in Switch Player yet, so nothing needed a backup.")
        return True


class Api:
    """The page can call only public methods; application internals stay behind underscores."""

    def __init__(self, app):
        self._app = app

    def library(self):
        return self._app._library()

    def art(self, game_id):
        return self._app._art(game_id)

    def choose_library(self):
        # On this call's own thread, as the menu's action runs: pywebview queues its folder panel
        # onto the main thread and waits for it, so asking the main thread to do it freezes the app.
        self._app.choose_library()

    def play(self, game_id):
        self._app._play(game_id)

    def game_settings(self, game_id):
        return self._app._game_settings(game_id)

    def save_game_settings(self, game_id, values):
        return self._app._save_game_settings(game_id, values)

    def open_settings_window(self, game_id):
        self._app._open_settings_window(game_id)

    def build(self, game_id):
        self._app._build(game_id)

    def show_log(self, game_id):
        self._app._show_log(game_id)

    def build_log(self, game_id, offset=0, key=None):
        return self._app._build_log(game_id, offset, key)

    def open_build_log(self, game_id):
        self._app._open_build_log(game_id)

    def copy_build_log(self, game_id):
        return self._app._copy_build_log(game_id)

    def ui_state(self):
        return self._app._ui_state()

    def set_ui_state(self, values):
        self._app._set_ui_state(values)

    def show_in_finder(self, game_id):
        self._app._show_in_finder(game_id)

    def stop(self, game_id):
        self._app._stop(game_id)

    def set_build_options(self, game_id, text):
        self._app._set_build_options(game_id, text)

    def import_nxplay_save(self, game_id):
        return self._app._import_nxplay_save(game_id)

    def setup(self):
        return prerequisites.check()

    def install_update(self):
        self._app._updater.install_now()

    def dismiss_update(self, toast_id):
        self._app._updater.dismiss(toast_id)

    def open_releases_page(self):
        updater.open_releases_page()

    def install_tools(self):
        try:
            prerequisites.install_tools()
            return True
        except nxnative.NxnativeError as e:
            log.warning("Build tool installation: %s", e)
            return False

    def log_error(self, message):
        log.warning("Page error: %s", str(message)[:1000])


def _setup_logging():
    from logging.handlers import RotatingFileHandler
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s")
    file_handler = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])
    logging.getLogger("pywebview").setLevel(logging.WARNING)


def _acquire_single_instance_lock(folder=DATA_DIR):
    import fcntl
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / ".lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main():
    _setup_logging()
    lock = _acquire_single_instance_lock()
    if lock is None:
        log.info("Another Switch Player instance is already running")
        return
    SwitchPlayerApp().run()


if __name__ == "__main__":
    main()
