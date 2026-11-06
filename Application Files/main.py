"""Switch Player's pywebview application and the page's in-process API."""

import base64
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import builds
import library
import migration
import nxnative
import prerequisites
import settings
import titledb

log = logging.getLogger("switchplayer")

APP_NAME = "Switch Player"
VERSION = "0.1.0"
BUNDLE_ID = "com.digitaln.switchplayer"
if os.environ.get("SWITCHPLAYER_DATA_DIR"):
    DATA_DIR = Path(os.environ["SWITCHPLAYER_DATA_DIR"]).expanduser()
    LOG_DIR = DATA_DIR / "Logs"
else:
    DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
    LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME


def dispatch(function, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(function, *args)


class Native:
    def open_app(self, path, no_launcher):
        argv = ["/usr/bin/open", str(path)]
        if no_launcher:
            argv += ["--args", "--no-launcher"]
        subprocess.run(argv, check=True)

    def reveal(self, path):
        from AppKit import NSURL, NSWorkspace
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(path))])

    def game_running(self, game):
        if not game.app:
            return False
        from AppKit import NSRunningApplication
        bundle_id = f"org.nxnative.{game.title_id.lower()}"
        return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id))

    def confirm(self, message, action="Quit"):
        from AppKit import NSAlert, NSAlertFirstButtonReturn
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.addButtonWithTitle_(action)
        alert.addButtonWithTitle_("Cancel")
        return alert.runModal() == NSAlertFirstButtonReturn

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
                 native=None, dispatch=dispatch, save_root=None, game_running=None):
        self._native = native or Native()
        self._dispatch = dispatch
        self._data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self._log_dir = self._data_dir / "Logs" if data_dir is not None else LOG_DIR
        self._save_root = Path(save_root) if save_root is not None else self._default_save_root()
        self._game_running = game_running or self._native.game_running
        self._settings = settings.Settings(self._data_dir / "settings.json", first_launch=first_launch)
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
        self._freshness_thread = None
        self._freshness_checked = None
        self._window = None
        self._caffeinate = None

    def _default_save_root(self):
        if os.environ.get("SWITCHPLAYER_DATA_DIR"):
            return self._data_dir / "nxnative-saves"
        return Path.home() / "Library" / "Application Support" / "nxnative"

    @staticmethod
    def _art_key(path):
        return f"{path.name}:{path.stat().st_mtime_ns}" if path else None

    def _get_builder(self):
        if self._builder is None:
            self._builder = builds.Builder(self._prepare, self._log_dir / "Builds",
                                          on_finish=self._build_finished, on_busy=self._busy_changed)
        return self._builder

    def _build_finished(self, job, ok):
        self._last_scan = None
        if ok:
            game = self._games.get(job.game_id)
            with self._freshness_lock:
                if game:
                    self._freshness.pop(game.title_id, None)
                self._freshness_checked = None
            self._dispatch(self._native.notify, job.name, "Build finished")
        elif job.state == "failed":
            self._dispatch(self._native.notify, job.name, "Build failed")

    def _busy_changed(self, busy):
        if busy and self._caffeinate is None:
            self._caffeinate = self._native.prevent_sleep()
        elif not busy and self._caffeinate is not None:
            self._native.allow_sleep(self._caffeinate)
            self._caffeinate = None

    def attach_window(self, window):
        self._window = window

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

    def run(self, webview=None):
        if webview is None:
            import webview
        self._native.install_lifecycle(self)
        menus = [webview.Menu("__app__", [
            webview.menu.MenuAction("Choose Library Folder…", self.choose_library),
            webview.menu.MenuAction("Choose Keys File…", self.choose_keys),
            webview.menu.MenuAction("Build Setup…", self.show_setup),
            webview.menu.MenuAction("Check for nxnative Changes Now", lambda: self._refresh_freshness(force=True)),
        ])]
        window = webview.create_window(
            APP_NAME, html=(Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8"),
            js_api=Api(self), width=960, height=700, min_size=(420, 400), menu=menus,
        )
        self.attach_window(window)
        window.events.closing += self.close_window
        webview.start(private_mode=True, debug=False, menu=menus)

    def _prepare(self, job):
        values = self._settings.get()
        copy = nxnative.Copy(values["nxnative_checkout"] or self._data_dir / "nxnative",
                             own=values["nxnative_checkout"] is None)
        environment = nxnative.Environment(self._data_dir / "nxnative-python",
                                           Path(__file__).parent / "nxnative-python" / "pyproject.toml")
        copy.update()
        environment.ensure()
        game = self._games.get(job.game_id)
        if game and game.title_id:
            migration.first_build(game.path / f"{game.title_id}_build" / "run" / "save",
                                  self._save_root / game.title_id / "save")
        return nxnative.build_command(copy, environment, job.path, keys_file=values["keys_file"],
                                      options=values["build_options"].get(job.game_id, ""))

    def _library(self):
        values = self._settings.get()
        folder = values["library_folder"]
        if not folder:
            self._games = {}
            self._scan_folder = None
            return {"folder_name": None, "games": []}
        now = time.monotonic()
        if folder != self._scan_folder or self._last_scan is None or now - self._last_scan >= 2:
            games = library.scan(folder)
            self._games = {game.id: game for game in games}
            self._scan_folder = folder
            self._last_scan = now
            self._update_titles(games)
            self._refresh_freshness()
        games = sorted(self._games.values(),
                       key=lambda game: library.sort_key(game.name(self._titledb.name(game.title_id))))
        result = []
        with self._freshness_lock:
            freshness = dict(self._freshness)
        for game in games:
            status = self._builder.status(game.id) if self._builder else None
            current = freshness.get(game.title_id, {})
            result.append({
                "id": game.id,
                "name": game.name(self._titledb.name(game.title_id)),
                "title_id": game.title_id,
                "art": self._art_key(self._art_path(game)),
                "state": status["state"] if status else ("ready" if game.app else "none"),
                "app": bool(game.app),
                "progress": status["progress"] if status else None,
                "error": status["error"] if status else None,
                "out_of_date": current.get("out_of_date", False) or nxnative.game_files_changed(game),
                "newer": current.get("newer", 0),
                "log": self._game_log(game).exists(),
                "nxplay_save": game.nxplay_save,
                "options": values["build_options"].get(game.id, ""),
            })
        return {"folder_name": Path(folder).name, "games": result}

    def _refresh_freshness(self, force=False):
        with self._freshness_lock:
            if self._freshness_thread and self._freshness_thread.is_alive():
                return
            if not force and self._freshness_checked and time.time() - self._freshness_checked < 86400:
                return
            self._freshness_checked = time.time()
            self._freshness_thread = threading.Thread(target=self._fetch_freshness, name="freshness", daemon=True)
            self._freshness_thread.start()

    def _fetch_freshness(self):
        values = self._settings.get()
        copy = nxnative.Copy(values["nxnative_checkout"] or self._data_dir / "nxnative",
                             own=values["nxnative_checkout"] is None)
        apps = [game.app for game in self._games.values() if game.app]
        freshness = copy.freshness(apps)
        with self._freshness_lock:
            self._freshness = freshness

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
        art = self._titledb.art(game.title_id)
        if art or not game or not game.app:
            return art
        icon = game.app.path / "Contents" / "Resources" / "AppIcon.icns"
        if not icon.is_file():
            return None
        output = self._data_dir / "Icons" / f"{builds.safe_name(game.id)}-{icon.stat().st_mtime_ns}.jpg"
        if not output.is_file():
            self._native.convert_icon(icon, output)
        return output if output.is_file() else None

    def _game_log(self, game):
        if self._data_dir == DATA_DIR and not os.environ.get("SWITCHPLAYER_DATA_DIR"):
            return Path.home() / "Library" / "Logs" / "nxnative" / f"{game.title_id}.log"
        return self._data_dir / "nxnative-logs" / f"{game.title_id}.log"

    def _play(self, game_id):
        game = self._games.get(game_id)
        if game and game.app:
            self._dispatch(self._native.open_app, game.app.path, True)

    def _open_settings(self, game_id):
        game = self._games.get(game_id)
        if game and game.app:
            self._dispatch(self._native.open_app, game.app.path, False)

    def _show_log(self, game_id):
        game = self._games.get(game_id)
        if game:
            path = self._game_log(game)
            if path.exists():
                self._dispatch(self._native.reveal, path)

    def _show_build_log(self, game_id):
        if self._builder:
            path = self._builder.build_log(game_id)
            if path and path.exists():
                self._dispatch(self._native.reveal, path)

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
            self._get_builder().build(game.id, game.path, game.name(self._titledb.name(game.title_id)))

    def _stop(self, game_id):
        if self._builder:
            self._builder.stop(game_id)

    def _set_build_options(self, game_id, text):
        self._settings.set_build_options(game_id, text)

    def _import_nxplay_save(self, game_id):
        game = self._games.get(game_id)
        if not game or not game.title_id or self._game_running(game):
            return False
        source = game.path / f"{game.title_id}_build" / "run" / "save"
        destination = self._save_root / game.title_id / "save"
        try:
            details = migration.import_details(source, destination, running=False)
        except nxnative.NxnativeError as e:
            log.warning("NX Play save import: %s", e)
            return False

        def date(value):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "No save yet"
        message = (f"Import the NX Play save for {game.name()}?\n\n"
                   f"NX Play save: {date(details['nxplay_date'])}\n"
                   f"Current save: {date(details['current_date'])}\n\n"
                   "The current save will be backed up before it is replaced.")
        if not self._native.confirm(message, "Import"):
            return False
        if self._game_running(game):
            return False
        try:
            migration.import_save(source, destination)
            return True
        except nxnative.NxnativeError as e:
            log.warning("NX Play save import: %s", e)
            return False


class Api:
    """The page can call only public methods; application internals stay behind underscores."""

    def __init__(self, app):
        self._app = app

    def library(self):
        return self._app._library()

    def art(self, game_id):
        return self._app._art(game_id)

    def choose_library(self):
        self._app._dispatch(self._app.choose_library)

    def play(self, game_id):
        self._app._play(game_id)

    def open_settings(self, game_id):
        self._app._open_settings(game_id)

    def build(self, game_id):
        self._app._build(game_id)

    def show_log(self, game_id):
        self._app._show_log(game_id)

    def show_build_log(self, game_id):
        self._app._show_build_log(game_id)

    def show_in_finder(self, game_id):
        self._app._show_in_finder(game_id)

    def stop(self, game_id):
        self._app._stop(game_id)

    def set_build_options(self, game_id, text):
        self._app._set_build_options(game_id, text)

    def import_nxplay_save(self, game_id):
        return self._app._import_nxplay_save(game_id)

    def setup(self):
        return prerequisites.check(self._app._data_dir / "nxnative-python")

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
