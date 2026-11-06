import base64
import json
import plistlib
import sys
import time
from pathlib import Path

import pytest

import main
import nxnative


@pytest.fixture(autouse=True)
def no_background_downloads(monkeypatch):
    monkeypatch.setattr(main.titledb.TitleDB, "update", lambda self, tids: set())
    monkeypatch.setattr(main.SwitchPlayerApp, "_refresh_freshness", lambda self: None)


def test_library_waits_for_a_folder_choice(tmp_path):
    api = main.Api(main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict))

    assert api.library() == {"folder_name": None, "games": []}


def test_library_returns_the_page_contract_for_an_unbuilt_game(tmp_path):
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / "Rhythm Heaven [0100D9F01D474000].nsp").touch()
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
    )

    assert main.Api(app).library() == {
        "folder_name": "Switch Games",
        "games": [{
            "id": "Rhythm Heaven",
            "name": "Rhythm Heaven",
            "title_id": "0100D9F01D474000",
            "art": None,
            "state": "none",
            "app": False,
            "progress": None,
            "error": None,
            "out_of_date": False,
            "newer": 0,
            "log": False,
            "nxplay_save": False,
            "options": "",
        }],
    }


def test_titledb_supplies_the_name_and_art(tmp_path):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    data = tmp_path / "data"
    (data / "titledb" / "art").mkdir(parents=True)
    (data / "titledb" / "index.json").write_text(json.dumps({
        "fetched": 0,
        "titles": {tid: {"name": "Rhythm Heaven Groove", "icon": None}},
        "missing": [],
    }))
    image = b"\xff\xd8\xfftest art"
    (data / "titledb" / "art" / f"{tid}.jpg").write_bytes(image)
    app = main.SwitchPlayerApp(
        data_dir=data,
        first_launch=lambda: {"library_folder": str(folder.parent)},
    )
    api = main.Api(app)

    game = api.library()["games"][0]

    assert game["name"] == "Rhythm Heaven Groove"
    assert game["art"] is not None
    assert api.art(game["id"]) == "data:image/jpeg;base64," + base64.b64encode(image).decode()


class FakeBuilder:
    def __init__(self):
        self.jobs = {}
        self.log = None
        self.stop_all_calls = 0

    @property
    def busy(self):
        return any(job["state"] in ("waiting", "building") for job in self.jobs.values())

    def status(self, game_id):
        return self.jobs.get(game_id)

    def build(self, game_id, path, name):
        self.jobs[game_id] = {"state": "waiting", "progress": None, "error": None, "log": None}

    def stop(self, game_id):
        self.jobs.pop(game_id, None)

    def stop_all(self):
        self.stop_all_calls += 1
        self.jobs.clear()

    def build_log(self, game_id):
        return self.log


def test_build_stop_and_options_flow_through_the_page_api(tmp_path):
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / "Rhythm Heaven [0100D9F01D474000].nsp").touch()
    builder = FakeBuilder()
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
        builder=builder,
    )
    api = main.Api(app)
    api.library()

    api.build("Rhythm Heaven")
    assert api.library()["games"][0]["state"] == "waiting"
    assert builder.jobs["Rhythm Heaven"]["state"] == "waiting"

    api.set_build_options("Rhythm Heaven", " --flag 'two words' ")
    assert api.library()["games"][0]["options"] == "--flag 'two words'"

    api.stop("Rhythm Heaven")
    assert api.library()["games"][0]["state"] == "none"


class FakeNative:
    def __init__(self):
        self.calls = []
        self.answer = True

    def game_running(self, game):
        return False

    def confirm(self, message, action="Quit"):
        self.calls.append(("confirm", message, action))
        return self.answer

    def open_app(self, path, no_launcher):
        self.calls.append(("open", Path(path), no_launcher))

    def reveal(self, path):
        self.calls.append(("reveal", Path(path)))


def test_play_settings_finder_and_logs_use_the_native_adapter(tmp_path):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    contents = folder / "Rhythm Heaven Groove.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "DMTitleID": tid,
        "CFBundleDisplayName": "Rhythm Heaven Groove",
    }))
    data = tmp_path / "data"
    game_log = data / "nxnative-logs" / f"{tid}.log"
    game_log.parent.mkdir(parents=True)
    game_log.write_text("game log")
    build_log = data / "build.log"
    build_log.write_text("build log")
    builder = FakeBuilder()
    builder.log = build_log
    native = FakeNative()
    app = main.SwitchPlayerApp(
        data_dir=data,
        first_launch=lambda: {"library_folder": str(folder.parent)},
        builder=builder,
        native=native,
        dispatch=lambda function, *args: function(*args),
    )
    api = main.Api(app)

    assert api.library()["games"][0]["log"] is True
    api.play("Rhythm Heaven")
    api.open_settings("Rhythm Heaven")
    api.show_log("Rhythm Heaven")
    api.show_build_log("Rhythm Heaven")
    api.show_in_finder("Rhythm Heaven")

    game_app = folder / "Rhythm Heaven Groove.app"
    assert native.calls == [
        ("open", game_app, True),
        ("open", game_app, False),
        ("reveal", game_log),
        ("reveal", build_log),
        ("reveal", game_app),
    ]


def test_build_prepares_the_isolated_nxnative_script_and_environment(tmp_path, monkeypatch):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    (folder / "plan.json").write_text(json.dumps({"name": "Rhythm Heaven Groove", "title_id": tid}))
    source = folder / f"{tid}_build" / "run" / "save"
    source.mkdir(parents=True)
    (source / "progress").write_text("NX Play")
    save_root = tmp_path / "nxnative-support"
    checkout = tmp_path / "player's nxnative"
    script = checkout / nxnative.BUILD_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text((Path(__file__).parent / "fake_build.py").read_text())
    data = tmp_path / "data"
    python = data / "nxnative-python" / "env" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    keys = tmp_path / "prod.keys"
    keys.touch()
    prepared = []
    monkeypatch.setattr(nxnative.Environment, "ensure", lambda self: prepared.append(self.folder))

    class BuildNative(FakeNative):
        def prevent_sleep(self):
            self.calls.append(("prevent_sleep",))
            return "sleep token"

        def allow_sleep(self, token):
            self.calls.append(("allow_sleep", token))

        def notify(self, title, message):
            self.calls.append(("notify", title, message))

    native = BuildNative()
    app = main.SwitchPlayerApp(
        data_dir=data,
        first_launch=lambda: {
            "library_folder": str(folder.parent),
            "keys_file": str(keys),
            "nxnative_checkout": str(checkout),
        },
        native=native,
        dispatch=lambda function, *args: function(*args),
        save_root=save_root,
    )
    api = main.Api(app)
    api.library()
    api.set_build_options("Rhythm Heaven", "--flag 'two words'")

    clock = time.monotonic
    monkeypatch.setattr(main.time, "monotonic", lambda: 100)
    api.build("Rhythm Heaven")
    deadline = clock() + 10
    argv_path = folder / "argv.json"
    while not argv_path.exists() and clock() < deadline:
        time.sleep(0.02)
    while api.library()["games"][0]["state"] != "ready" and clock() < deadline:
        time.sleep(0.02)

    assert api.library()["games"][0]["state"] == "ready"
    assert json.loads(argv_path.read_text()) == [
        str(folder), "--platform", "mac-app", "--progress", "json",
        "-k", str(keys), "--flag", "two words",
    ]
    assert prepared == [data / "nxnative-python"]
    assert (save_root / tid / "save" / "progress").read_text() == "NX Play"
    assert (source / "progress").read_text() == "NX Play"
    assert (folder / "Rhythm Heaven Groove.app" / "Contents" / "Info.plist").is_file()
    deadline = clock() + 5
    while ("allow_sleep", "sleep token") not in native.calls and clock() < deadline:
        time.sleep(0.02)
    assert ("prevent_sleep",) in native.calls
    assert ("allow_sleep", "sleep token") in native.calls
    assert ("notify", "Rhythm Heaven", "Build finished") in native.calls
    app._builder.stop_all()


def test_library_throttles_scans_and_updates_missing_titles_in_the_background(tmp_path, monkeypatch):
    tid = "0100D9F01D474000"
    library_folder = tmp_path / "Switch Games"
    folder = library_folder / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    data = tmp_path / "data"
    app = main.SwitchPlayerApp(
        data_dir=data,
        first_launch=lambda: {"library_folder": str(library_folder)},
    )
    updates = []
    finished = []

    def update(tids):
        updates.append(tids)
        (data / "titledb" / "art").mkdir(parents=True, exist_ok=True)
        (data / "titledb" / "index.json").write_text(json.dumps({
            "fetched": 0, "titles": {tid: {"name": "Rhythm Heaven Groove", "icon": None}}, "missing": [],
        }))
        (data / "titledb" / "art" / f"{tid}.jpg").write_bytes(b"\xff\xd8\xffart")
        finished.append(tids)

    app._titledb.update = update
    ticks = iter([100, 101, 103])
    clock = time.monotonic
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks, 104))
    api = main.Api(app)

    assert api.library()["games"][0]["name"] == "Rhythm Heaven"
    assert api.library()["games"][0]["name"] == "Rhythm Heaven"
    (library_folder / "Another [0100000000010000].nup").touch()
    assert len(api.library()["games"]) == 2
    deadline = clock() + 5
    while len(finished) < 2 and clock() < deadline:
        time.sleep(0.02)

    assert updates == [[tid], ["0100000000010000"]]
    assert finished == updates
    assert api.library()["games"][0]["name"] == "Another [0100000000010000]"
    assert api.library()["games"][1]["name"] == "Rhythm Heaven Groove"
    assert api.library()["games"][1]["art"] is not None


def test_app_icon_is_the_cached_art_fallback(tmp_path):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    icon = folder / "Rhythm Heaven Groove.app" / "Contents" / "Resources" / "AppIcon.icns"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"icon")
    (icon.parent.parent / "Info.plist").write_bytes(plistlib.dumps({
        "DMTitleID": tid, "CFBundleDisplayName": "Rhythm Heaven Groove",
    }))

    class Icons(FakeNative):
        def convert_icon(self, source, output):
            self.calls.append(("icon", source, output))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\xff\xd8\xffconverted")

    native = Icons()
    api = main.Api(main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
        native=native,
    ))

    key = api.library()["games"][0]["art"]
    assert key is not None
    assert api.art("Rhythm Heaven") == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffconverted").decode()
    api.art("Rhythm Heaven")
    assert len(native.calls) == 1


def test_window_close_reopen_and_busy_quit(tmp_path):
    class Window:
        def __init__(self):
            self.calls = []

        def hide(self):
            self.calls.append("hide")

        def show(self):
            self.calls.append("show")

    builder = FakeBuilder()
    native = FakeNative()
    app = main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict, builder=builder, native=native,
                               dispatch=lambda function, *args: function(*args))
    window = Window()
    app.attach_window(window)

    assert app.close_window() is False
    app.reopen()
    assert window.calls == ["hide", "show"]
    assert app.application_should_terminate() is True
    assert builder.stop_all_calls == 1

    builder.jobs["Game"] = {"state": "waiting"}
    native.answer = False
    assert app.application_should_terminate() is False
    assert builder.stop_all_calls == 1
    native.answer = True
    assert app.application_should_terminate() is True
    assert builder.stop_all_calls == 2
    assert native.calls == [
        ("confirm", "Stop the Build and quit?", "Quit"),
        ("confirm", "Stop the Build and quit?", "Quit"),
    ]


def test_page_errors_are_logged(tmp_path, caplog):
    api = main.Api(main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict))

    with caplog.at_level("WARNING", logger="switchplayer"):
        api.log_error("page failed")

    assert "Page error: page failed" in caplog.text


def test_only_one_instance_holds_the_lock(tmp_path):
    lock = main._acquire_single_instance_lock(tmp_path)
    try:
        assert lock is not None
        assert main._acquire_single_instance_lock(tmp_path) is None
    finally:
        lock.close()


def test_library_and_keys_choices_update_settings(tmp_path):
    games = tmp_path / "Games"
    games.mkdir()
    keys = tmp_path / "prod.keys"
    keys.touch()

    class PickerNative(FakeNative):
        def choose_folder(self, window):
            return (str(games),)

        def choose_keys(self, window):
            return (str(keys),)

    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=dict, native=PickerNative())
    app.attach_window(object())

    app.choose_library()
    app.choose_keys()

    assert main.Api(app).library()["folder_name"] == "Games"
    assert app._settings.get()["keys_file"] == str(keys)


def test_run_wires_the_real_page_to_a_private_pywebview_window(tmp_path):
    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class Events:
        def __init__(self):
            self.closing = Event()

    class Window:
        def __init__(self):
            self.events = Events()

    class MenuAction:
        def __init__(self, title, action):
            self.title = title
            self.action = action

    class Menu:
        def __init__(self, title, items):
            self.title = title
            self.items = items

    class Webview:
        menu = type("MenuNamespace", (), {"MenuAction": MenuAction})

        def __init__(self):
            self.window = None
            self.kwargs = None
            self.start_kwargs = None

        def Menu(self, title, items):
            return Menu(title, items)

        def create_window(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.window = Window()
            return self.window

        def start(self, **kwargs):
            self.start_kwargs = kwargs

    class LaunchNative(FakeNative):
        def install_lifecycle(self, app):
            self.calls.append(("lifecycle", app))

    webview = Webview()
    native = LaunchNative()
    app = main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict, native=native,
                               dispatch=lambda function, *args: function(*args))
    app._updater = type("Updater", (), {"start": lambda self: native.calls.append(("updates",)),
                                       "check_now": lambda self: native.calls.append(("check updates",))})()

    app.run(webview)

    assert webview.args == ("Switch Player",)
    assert webview.kwargs["html"] == (Path(main.__file__).parent / "index.html").read_text()
    assert isinstance(webview.kwargs["js_api"], main.Api)
    assert webview.start_kwargs["private_mode"] is True
    assert webview.start_kwargs["debug"] is False
    assert app._window is webview.window
    assert webview.window.events.closing.handlers == [app.close_window]
    assert native.calls == [("lifecycle", app), ("updates",)]
    menu = webview.kwargs["menu"][0]
    assert menu.title == "__app__"
    assert [item.title for item in menu.items] == [
        "Choose Library Folder…", "Choose Keys File…", "Build Setup…", "Check for Updates…",
        "Check for nxnative Changes Now",
    ]
    menu.items[3].action()
    assert native.calls[-1] == ("check updates",)


def test_page_library_choice_uses_the_native_dispatcher(tmp_path):
    games = tmp_path / "Games"
    games.mkdir()

    class PickerNative(FakeNative):
        def choose_folder(self, window):
            return (str(games),)

    dispatched = []
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data", first_launch=dict, native=PickerNative(),
        dispatch=lambda function, *args: (dispatched.append(function.__name__), function(*args))[1],
    )
    app.attach_window(object())

    main.Api(app).choose_library()

    assert dispatched == ["choose_library"]
    assert main.Api(app).library()["folder_name"] == "Games"


def test_native_notifications_use_the_installed_appkit_api(monkeypatch):
    calls = []

    class Notification:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setTitle_(self, title):
            calls.append(("title", title))

        def setInformativeText_(self, message):
            calls.append(("body", message))

    class Center:
        @classmethod
        def defaultUserNotificationCenter(cls):
            return cls()

        def deliverNotification_(self, notification):
            calls.append(("deliver", notification))

    appkit = type("AppKit", (), {"NSUserNotification": Notification, "NSUserNotificationCenter": Center})
    monkeypatch.setitem(sys.modules, "AppKit", appkit)

    main.Native().notify("Rhythm Heaven", "Build finished")

    assert calls[:2] == [("title", "Rhythm Heaven"), ("body", "Build finished")]
    assert calls[2][0] == "deliver"
    assert isinstance(calls[2][1], Notification)


def test_save_import_shows_dates_backs_up_and_copies_only_after_confirmation(tmp_path):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    source = folder / f"{tid}_build" / "run" / "save"
    source.mkdir(parents=True)
    (source / "progress").write_text("NX Play")
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    support = tmp_path / "nxnative-support"
    destination = support / tid / "save"
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")
    native = FakeNative()
    native.answer = False
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
        native=native,
        dispatch=lambda function, *args: function(*args),
        save_root=support,
    )
    api = main.Api(app)
    api.library()

    assert api.import_nxplay_save("Rhythm Heaven") is False
    assert (destination / "current").read_text() == "Switch Player"
    assert len(native.calls) == 1
    assert native.calls[0][0] == "confirm"
    assert native.calls[0][2] == "Import"
    assert "NX Play save:" in native.calls[0][1]
    assert "Current save:" in native.calls[0][1]

    native.answer = True
    assert api.import_nxplay_save("Rhythm Heaven") is True
    assert (destination / "progress").read_text() == "NX Play"
    backups = list((support / tid / "save-backups").iterdir())
    assert len(backups) == 1
    assert backups[0].name.endswith(" before NX Play import")
    assert (backups[0] / "current").read_text() == "Switch Player"


def test_save_import_refuses_while_the_game_is_running(tmp_path):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    source = folder / f"{tid}_build" / "run" / "save"
    source.mkdir(parents=True)
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    support = tmp_path / "nxnative-support"
    native = FakeNative()
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
        native=native,
        save_root=support,
        game_running=lambda game: True,
    )
    api = main.Api(app)
    api.library()

    assert api.import_nxplay_save("Rhythm Heaven") is False
    assert native.calls == []
    assert not (support / tid / "save").exists()


def test_save_import_does_not_offer_a_missing_source(tmp_path):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    support = tmp_path / "nxnative-support"
    destination = support / tid / "save"
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")
    native = FakeNative()
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
        native=native,
        save_root=support,
    )
    api = main.Api(app)
    api.library()

    assert api.import_nxplay_save("Rhythm Heaven") is False
    assert native.calls == []
    assert (destination / "current").read_text() == "Switch Player"


def test_library_reports_freshness_after_a_background_fetch(tmp_path, monkeypatch):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    contents = folder / "Rhythm Heaven Groove.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "DMTitleID": tid,
        "CFBundleDisplayName": "Rhythm Heaven Groove",
        "DMCommit": "123456789abc",
        "DMChain": "b-41",
    }))
    import threading
    started = threading.Event()
    release = threading.Event()

    def freshness(self, apps):
        started.set()
        release.wait(5)
        return {tid: {"out_of_date": True, "newer": 142}}

    monkeypatch.undo()
    monkeypatch.setattr(nxnative.Copy, "freshness", freshness)
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
    )
    api = main.Api(app)

    assert api.library()["games"][0]["out_of_date"] is False
    assert started.wait(5)
    release.set()
    deadline = time.monotonic() + 5
    while not api.library()["games"][0]["out_of_date"] and time.monotonic() < deadline:
        time.sleep(0.02)

    game = api.library()["games"][0]
    assert game["out_of_date"] is True
    assert game["newer"] == 142


def test_freshness_refreshes_daily_or_on_demand(tmp_path, monkeypatch):
    monkeypatch.undo()
    calls = []
    monkeypatch.setattr(main.SwitchPlayerApp, "_fetch_freshness", lambda self: calls.append("fetch"))
    now = [1000]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    app = main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict)

    app._refresh_freshness()
    app._freshness_thread.join(5)
    app._refresh_freshness()
    assert calls == ["fetch"]

    now[0] += 86400
    app._refresh_freshness()
    app._freshness_thread.join(5)
    app._refresh_freshness(force=True)
    app._freshness_thread.join(5)
    assert calls == ["fetch", "fetch", "fetch"]


def test_wake_observation_uses_the_native_notification_center(monkeypatch):
    calls = []
    observer = object()

    class Center:
        def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
            calls.append((name, block))
            return observer

    class Workspace:
        def notificationCenter(self):
            return Center()

    class AppKit:
        NSWorkspaceDidWakeNotification = "wake"
        NSApplicationActivationPolicyRegular = 0
        NSApp = type("App", (), {"setActivationPolicy_": lambda self, policy: None})()
        NSWorkspace = type("WorkspaceNamespace", (), {"sharedWorkspace": lambda: Workspace()})

    class Delegate:
        pass

    monkeypatch.setitem(sys.modules, "AppKit", AppKit)
    monkeypatch.setitem(sys.modules, "objc", type("ObjC", (), {
        "classAddMethods": lambda cls, methods: None,
        "selector": lambda function, **kwargs: function,
    }))
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", type("Cocoa", (), {
        "BrowserView": type("Browser", (), {"AppDelegate": Delegate}),
    }))
    native = main.Native()
    app = type("App", (), {"_refresh_freshness": lambda self: calls.append("refresh")})()

    native.install_lifecycle(app)
    calls[0][1](None)

    assert calls[0][0] == "wake"
    assert calls[1] == "refresh"
    assert native._wake_observer is observer


def test_setup_is_read_only_until_the_player_installs_tools(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main.prerequisites, "check", lambda environment: {
        "ready": False,
        "items": [{"id": "tools", "name": "Build tools", "ready": False,
                   "detail": "Homebrew tools are missing.", "action": "install"}],
    })
    monkeypatch.setattr(main.prerequisites, "install_tools", lambda: calls.append("install"))
    api = main.Api(main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict))

    result = api.setup()
    assert result["ready"] is False
    assert calls == []

    assert api.install_tools() is True
    assert calls == ["install"]


def test_setup_menu_opens_the_dialog_and_install_failures_are_reported(tmp_path, monkeypatch):
    class Window:
        def __init__(self):
            self.scripts = []

        def evaluate_js(self, script):
            self.scripts.append(script)

    app = main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict)
    window = Window()
    app.attach_window(window)
    app.show_setup()
    assert window.scripts == ["showSetup()"]

    def fail():
        raise nxnative.NxnativeError("Homebrew failed")

    monkeypatch.setattr(main.prerequisites, "install_tools", fail)
    assert main.Api(app).install_tools() is False
