import base64
import json
import plistlib
import sys
import threading
import time
from pathlib import Path

import pytest

import builds
import launch
import main
import nxnative


@pytest.fixture(autouse=True)
def no_background_downloads(monkeypatch):
    monkeypatch.setattr(main.titledb.TitleDB, "update", lambda self, tids: set())
    monkeypatch.setattr(main.SwitchPlayerApp, "_refresh_freshness", lambda self: None)


@pytest.fixture(autouse=True)
def no_real_games_running(monkeypatch):
    """Never the player's own running games, and no watch thread: tests drive a Play's states
    through the page's polls (api.library()) with a clock of their own."""
    monkeypatch.setattr(main.Native, "game_processes", lambda self, game: set())
    monkeypatch.setattr(main.SwitchPlayerApp, "_start_play_watch", lambda self: None)


def test_library_waits_for_a_folder_choice(tmp_path):
    api = main.Api(main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict))

    assert api.library() == {"folder_name": None, "games": [], "update": None}


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
            "play": None,
            "progress": None,
            "error": None,
            "out_of_date": False,
            "newer": 0,
            "log": False,
            "build_log": False,
            "nxplay_save": False,
            "options": "",
        }],
        "update": None,  # the App update toast (updater.Updater.toast): none from a source checkout
    }


@pytest.mark.parametrize("nxplay_file", ["start_mac.sh", "nxplay-settings.json"])
def test_library_marks_a_game_nxplay_built(tmp_path, nxplay_file):
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    (folder / f"{tid}_build").mkdir(parents=True)
    (folder / f"{tid}_build" / nxplay_file).touch()
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
    )

    game = main.Api(app).library()["games"][0]

    assert game["state"] == "nxplay"
    assert game["app"] is False


def test_a_build_folder_nxplay_did_not_write_is_not_built(tmp_path):
    """A Switch Player Build stopped or failed part-way leaves the same <title ID>_build folder."""
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    (folder / f"{tid}_build" / "extract").mkdir(parents=True)
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
    )

    assert main.Api(app).library()["games"][0]["state"] == "none"


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
        self.prefs = {}  # bundle ID → {key: value}: Game apps' saved choices
        self.running = False
        self.processes = set()  # the Game app's processes, as game_processes finds them
        self.windows = set()  # those of them with a window on screen

    def game_processes(self, game):
        return set(self.processes)

    def window_shown(self, pids):
        return bool(self.windows & pids)

    def bring_to_front(self, pids):
        self.calls.append(("front", set(pids)))
        return True

    def read_choices(self, bundle_id, keys):
        stored = self.prefs.get(bundle_id, {})
        return {key: stored[key] for key in keys if isinstance(stored.get(key), str)}

    def write_choices(self, bundle_id, changes):
        self.calls.append(("write choices", bundle_id, dict(changes)))
        stored = self.prefs.setdefault(bundle_id, {})
        for key, value in changes.items():
            if value is None:
                stored.pop(key, None)
            else:
                stored[key] = value
        return self.answer

    def game_running(self, game):
        return self.running

    def confirm(self, message, action="Quit", detail=""):
        self.calls.append(("confirm", message, action, detail))
        return self.answer

    def alert(self, title, message):
        self.calls.append(("alert", title, message))

    def open_app(self, path, no_launcher):
        self.calls.append(("open", Path(path), no_launcher))

    def forget_settings_window_choice(self, bundle_id):
        self.calls.append(("forget window choice", bundle_id))

    def reveal(self, path):
        self.calls.append(("reveal", Path(path)))

    def open_in_console(self, path):
        self.calls.append(("console", Path(path)))

    def copy_text(self, text):
        self.calls.append(("copy", text))
        return self.answer


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
    api.open_settings_window("Rhythm Heaven")
    api.show_log("Rhythm Heaven")
    api.open_build_log("Rhythm Heaven")
    api.show_in_finder("Rhythm Heaven")

    game_app = folder / "Rhythm Heaven Groove.app"
    assert native.calls == [
        ("open", game_app, True),
        # The game's own Settings window, even when the player turned it off at launch.
        ("forget window choice", "org.nxnative.0100d9f01d474000"),
        ("open", game_app, False),
        ("reveal", game_log),
        ("console", build_log),
        ("reveal", game_app),
    ]


GROOVE_SETTINGS = {"schema": 1, "game": "0100D9F01D474000", "settings": [
    {"env": "DM_OPMODE", "kind": "choice", "section": "Game", "label": "Console mode", "note": "", "default": "0",
     "level": "basic", "options": [["1", "Docked"], ["0", "Handheld"]]},
    {"env": "DM_FULLSCREEN", "kind": "choice", "section": "Game", "label": "Display mode", "note": "", "default": "0",
     "level": "basic", "options": [["0", "Window"], ["1", "Fullscreen"]]},
    {"env": "DM_RUMBLE", "kind": "toggle", "section": "Controls", "label": "Game rumble", "note": "", "default": "1",
     "level": "basic", "on": "1", "off": "0"},
    {"env": "DM_RUMBLE_GAIN", "kind": "range", "section": "Controls", "label": "Rumble strength", "note": "",
     "default": "1.0", "needs": "DM_RUMBLE=1", "min": 0.0, "max": 3.0, "step": 0.1},
]}


def settings_game(tmp_path, bundle_id=None, settings=GROOVE_SETTINGS):
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    contents = folder / "Rhythm Heaven Groove.app" / "Contents"
    (contents / "Resources" / "dm").mkdir(parents=True)
    info = {"DMTitleID": "0100D9F01D474000", "CFBundleDisplayName": "Rhythm Heaven Groove"}
    if bundle_id:
        info["CFBundleIdentifier"] = bundle_id
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    if settings is not None:
        (contents / "Resources" / "dm" / "launcher.json").write_text(json.dumps(settings))
    return folder


def settings_api(tmp_path, native, **game):
    folder = settings_game(tmp_path, **game)
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data",
                               first_launch=lambda: {"library_folder": str(folder.parent)},
                               native=native, dispatch=lambda function, *args: function(*args),
                               save_root=tmp_path / "data" / "nxnative-saves")  # never the player's own env.txt
    api = main.Api(app)
    api.library()
    return api, folder


GROOVE = "org.nxnative.0100d9f01d474000"


def test_the_settings_panel_shows_saved_choices_defaults_and_fixed_settings(tmp_path):
    native = FakeNative()
    native.prefs[GROOVE] = {"launcher.DM_OPMODE": "1", "launcher.DM_RUMBLE_GAIN": "9.00",  # not in this build's range
                            "launcher.DM_FULLSCREEN": "1"}
    api, folder = settings_api(tmp_path, native)
    # The player's env.txt, under the data folder's stand-in for ~/Library/Application Support/nxnative
    player_env = tmp_path / "data" / "nxnative-saves" / "0100D9F01D474000" / "env.txt"
    player_env.parent.mkdir(parents=True)
    player_env.write_text("# fixed for every start\nDM_FULLSCREEN=0\n")

    panel = api.game_settings("Rhythm Heaven")

    assert (panel["name"], panel["status"], panel["running"]) == ("Rhythm Heaven Groove", "ok", False)
    assert [(row["env"], row["value"], row["fixed"]) for row in panel["rows"]] == [
        ("DM_OPMODE", "1", False),  # the saved choice
        ("DM_FULLSCREEN", "0", True),  # the env.txt wins over the saved choice
        ("DM_RUMBLE", "1", False),  # the tested default
        ("DM_RUMBLE_GAIN", "1.0", False),  # a saved choice this build doesn't offer: its default
    ]
    assert panel["rows"][3]["needs"] == "DM_RUMBLE=1"  # the list's own fields, for the page to draw


def test_the_settings_panel_saves_as_the_games_own_window_would(tmp_path):
    native = FakeNative()
    native.prefs[GROOVE] = {"launcher.DM_RUMBLE": "0", "launcher.DM_FULLSCREEN": "1"}
    api, folder = settings_api(tmp_path, native)
    (folder / "Rhythm Heaven Groove.app" / "Contents" / "Resources" / "dm" / "env.txt").write_text("DM_FULLSCREEN=0\n")

    saved = api.save_game_settings("Rhythm Heaven", {
        "DM_OPMODE": "1",  # Docked: differs from the tested default, so it's kept
        "DM_RUMBLE": "1",  # back to the default: forgotten, so a later build's default reaches the player
        "DM_RUMBLE_GAIN": "1.5",  # a range, as the window's slider writes it
        "DM_FULLSCREEN": "0",  # fixed by the Game app's env.txt: its saved choice stays
        "DM_NOT_LISTED": "1",
    })

    assert saved is True
    assert native.prefs[GROOVE] == {"launcher.DM_OPMODE": "1", "launcher.DM_RUMBLE_GAIN": "1.50",
                                    "launcher.DM_FULLSCREEN": "1"}


def test_the_settings_panel_ignores_values_the_build_doesnt_offer(tmp_path):
    native = FakeNative()
    api, folder = settings_api(tmp_path, native)

    assert api.save_game_settings("Rhythm Heaven", {"DM_OPMODE": "2", "DM_RUMBLE_GAIN": "0x1p-1"}) is True
    assert native.prefs[GROOVE] == {}
    assert api.save_game_settings("Rhythm Heaven", "Docked") is False
    native.answer = False  # the preferences couldn't be written
    assert api.save_game_settings("Rhythm Heaven", {"DM_OPMODE": "1"}) is False


def test_the_settings_panel_says_when_the_game_is_running(tmp_path):
    native = FakeNative()
    native.running = True
    api, folder = settings_api(tmp_path, native)

    assert api.game_settings("Rhythm Heaven")["running"] is True


@pytest.mark.parametrize("settings, status", [
    (None, "none"),  # a Game app with no settings list
    ({"schema": 1, "settings": []}, "none"),
    ({"schema": 2, "settings": GROOVE_SETTINGS["settings"]}, "unsupported"),
    ({"settings": [{**GROOVE_SETTINGS["settings"][0], "kind": "colour"}]}, "unsupported"),
    ("not a settings list", "unsupported"),
])
def test_the_settings_panel_says_when_it_cant_draw_a_list(tmp_path, settings, status):
    native = FakeNative()
    api, folder = settings_api(tmp_path, native, settings=settings)

    panel = api.game_settings("Rhythm Heaven")

    assert (panel["status"], panel["rows"]) == (status, [])
    assert api.save_game_settings("Rhythm Heaven", {"DM_OPMODE": "1"}) is False
    assert native.prefs == {}


def test_a_game_without_a_game_app_has_no_settings_panel(tmp_path):
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    folder.mkdir(parents=True)
    (folder / "Rhythm Heaven [0100D9F01D474000].nsp").touch()
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=lambda: {"library_folder": str(folder.parent)},
                               native=FakeNative())
    api = main.Api(app)
    api.library()

    assert api.game_settings("Rhythm Heaven") is None
    assert api.save_game_settings("Rhythm Heaven", {}) is False


def test_the_games_own_settings_window_uses_its_own_preferences_domain(tmp_path):
    folder = settings_game(tmp_path, bundle_id="com.example.groove")
    native = FakeNative()
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data",
                               first_launch=lambda: {"library_folder": str(folder.parent)},
                               native=native, dispatch=lambda function, *args: function(*args))
    api = main.Api(app)
    api.library()

    api.open_settings_window("Rhythm Heaven")

    assert native.calls == [("forget window choice", "com.example.groove"),
                            ("open", folder / "Rhythm Heaven Groove.app", False)]


def test_the_games_own_settings_window_still_opens_when_its_choice_cant_be_removed(tmp_path):
    folder = settings_game(tmp_path)

    class Refusing(FakeNative):
        def forget_settings_window_choice(self, bundle_id):
            raise OSError("cfprefsd refused")

    native = Refusing()
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data",
                               first_launch=lambda: {"library_folder": str(folder.parent)},
                               native=native, dispatch=lambda function, *args: function(*args))
    api = main.Api(app)
    api.library()

    api.open_settings_window("Rhythm Heaven")

    assert native.calls == [("open", folder / "Rhythm Heaven Groove.app", False)]


def test_native_reads_and_writes_only_string_choices(tmp_path):
    """Against a preferences file in the test's own folder, never a Game app's own."""
    Foundation = pytest.importorskip("Foundation")
    domain = str(tmp_path / "org.nxnative.test")
    Foundation.CFPreferencesSetAppValue("launcher.DM_RENDER_SCALE", 0.75, domain)  # not a string: ignored
    Foundation.CFPreferencesSetAppValue("launcher.DM_RUMBLE", "0", domain)
    Foundation.CFPreferencesAppSynchronize(domain)
    native = main.Native()
    keys = ["launcher.DM_OPMODE", "launcher.DM_RENDER_SCALE", "launcher.DM_RUMBLE"]

    assert native.read_choices(domain, keys) == {"launcher.DM_RUMBLE": "0"}
    assert native.write_choices(domain, {"launcher.DM_OPMODE": "1", "launcher.DM_RUMBLE": None}) is True
    assert native.read_choices(domain, keys) == {"launcher.DM_OPMODE": "1"}
    assert plistlib.loads((tmp_path / "org.nxnative.test.plist").read_bytes()) == {
        "launcher.DM_OPMODE": "1", "launcher.DM_RENDER_SCALE": 0.75}


def test_native_forgets_only_the_settings_window_choice(tmp_path):
    """Against a preferences file in the test's own folder (CFPreferences takes a path as a
    domain), never a Game app's own and nothing in ~/Library/Preferences."""
    Foundation = pytest.importorskip("Foundation")
    domain = str(tmp_path / "org.nxnative.test")
    Foundation.CFPreferencesSetAppValue(main.SETTINGS_WINDOW_CHOICE, False, domain)
    Foundation.CFPreferencesSetAppValue("launcher.DM_OPMODE", "1", domain)
    Foundation.CFPreferencesAppSynchronize(domain)

    main.Native().forget_settings_window_choice(domain)

    assert Foundation.CFPreferencesCopyAppValue(main.SETTINGS_WINDOW_CHOICE, domain) is None
    assert Foundation.CFPreferencesCopyAppValue("launcher.DM_OPMODE", domain) == "1"
    assert plistlib.loads((tmp_path / "org.nxnative.test.plist").read_bytes()) == {"launcher.DM_OPMODE": "1"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def play_api(tmp_path):
    native, clock = FakeNative(), Clock()
    folder = settings_game(tmp_path)
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=lambda: {"library_folder": str(folder.parent)},
                               native=native, dispatch=lambda function, *args: function(*args), clock=clock,
                               save_root=tmp_path / "data" / "nxnative-saves")
    api = main.Api(app)
    api.library()
    return api, native, clock, folder / "Rhythm Heaven Groove.app"


def play_state(api):
    return api.library()["games"][0]["play"]


def test_play_shows_starting_until_the_game_window_is_on_screen_then_playing_until_it_quits(tmp_path):
    api, native, clock, game_app = play_api(tmp_path)
    assert play_state(api) is None

    api.play("Rhythm Heaven")
    assert native.calls == [("open", game_app, True)]
    assert play_state(api) == "starting"  # open hasn't brought the Game app up yet
    native.processes = {501}
    clock.now += 20
    assert play_state(api) == "starting"  # running, loading, no window yet: however long it takes
    native.processes = {501, 502}  # its launcher's host
    native.windows = {502}
    assert play_state(api) == "playing"
    native.windows = set()  # its fullscreen window is in another Space now
    assert play_state(api) == "playing"
    native.processes = set()
    assert play_state(api) is None  # it quit: Ready again


def test_a_game_that_quits_before_its_window_shows_didnt_start_until_the_next_play(tmp_path):
    api, native, clock, game_app = play_api(tmp_path)

    api.play("Rhythm Heaven")
    native.processes = {501}
    assert play_state(api) == "starting"
    native.processes = set()
    assert play_state(api) == "didnt_start"
    assert play_state(api) == "didnt_start"

    api.play("Rhythm Heaven")
    assert play_state(api) == "starting"
    assert native.calls == [("open", game_app, True), ("open", game_app, True)]


def test_a_game_app_that_never_appears_didnt_start(tmp_path):
    api, native, clock, game_app = play_api(tmp_path)

    api.play("Rhythm Heaven")
    clock.now += main.PLAY_APPEARS - 1
    assert play_state(api) == "starting"
    clock.now += 2
    assert play_state(api) == "didnt_start"


def test_play_never_starts_a_game_twice_and_brings_a_running_one_to_the_front(tmp_path):
    api, native, clock, game_app = play_api(tmp_path)

    api.play("Rhythm Heaven")
    api.play("Rhythm Heaven")  # still starting: nothing to bring forward, and no second start
    native.processes, native.windows = {501, 502}, {502}
    assert play_state(api) == "playing"
    api.play("Rhythm Heaven")

    assert native.calls == [("open", game_app, True), ("front", {501, 502})]


def test_a_game_started_from_finder_shows_but_never_as_didnt_start(tmp_path):
    api, native, clock, game_app = play_api(tmp_path)
    native.processes = {501}

    assert play_state(api) == "starting"
    native.processes = set()
    assert play_state(api) is None  # it quit before a window: Ready, not Didn't start
    api.play("Rhythm Heaven")  # and Play starts it as usual
    assert native.calls == [("open", game_app, True)]


def test_the_watch_thread_follows_a_play_without_the_page(tmp_path, monkeypatch):
    """The page stops polling while the window is closed or behind a fullscreen game."""
    monkeypatch.undo()
    monkeypatch.setattr(main.titledb.TitleDB, "update", lambda self, tids: set())
    monkeypatch.setattr(main.SwitchPlayerApp, "_refresh_freshness", lambda self: None)
    monkeypatch.setattr(main, "PLAY_WATCH", 0.01)
    native, clock = FakeNative(), Clock()
    folder = settings_game(tmp_path)
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=lambda: {"library_folder": str(folder.parent)},
                               native=native, dispatch=lambda function, *args: function(*args), clock=clock)
    api = main.Api(app)
    api.library()

    api.play("Rhythm Heaven")
    native.processes, native.windows = {501}, {501}
    deadline = time.monotonic() + 5
    while not (app._plays.get("Rhythm Heaven") or {}).get("window") and time.monotonic() < deadline:
        time.sleep(0.01)
    native.processes, native.windows = set(), set()  # it quit, all without a poll from the page
    while app._play_thread is not None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert app._play_thread is None  # the thread ends with the last game
    assert play_state(api) is None  # Ready, never Didn't start


def test_child_pids_lists_the_processes_a_process_started():
    child = __import__("subprocess").Popen(["/bin/sleep", "5"])
    try:
        assert child.pid in main.child_pids(__import__("os").getpid())
    finally:
        child.kill()
        child.wait()


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
    monkeypatch.setattr(nxnative.Environment, "ensure", lambda self, **kwargs: prepared.append(self.folder))
    stages = []
    monkeypatch.setattr(nxnative.Environment, "ready",
                        lambda self: stages.append(main.Api(app).library()["games"][0]["progress"]) or False)

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
    assert stages == [{"preparing": "Updating nxnative", "elapsed": 0}]
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
    release = [threading.Event(), threading.Event()]  # each lookup ends only when the test says

    def update(tids):
        updates.append(tids)
        release[len(updates) - 1].wait(5)
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

    def wait_for(count):
        deadline = clock() + 5
        while len(finished) < count and clock() < deadline:
            time.sleep(0.02)

    assert api.library()["games"][0]["name"] == "Rhythm Heaven"
    assert api.library()["games"][0]["name"] == "Rhythm Heaven"  # throttled: no second scan, no second lookup
    release[0].set()
    wait_for(1)  # the first lookup ends before the next game appears
    (library_folder / "Another [0100000000010000].nup").touch()
    assert len(api.library()["games"]) == 2
    release[1].set()
    wait_for(2)

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
        ("confirm", "Stop the Build and quit?", "Quit", ""),
        ("confirm", "Stop the Build and quit?", "Quit", ""),
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
            self.scripts = []

        def evaluate_js(self, script):
            self.scripts.append(script)

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
        "Check for nxnative Updates…",
    ]
    menu.items[3].action()
    assert native.calls[-1] == ("check updates",)
    assert menu.items[4].action == app.check_nxnative


def test_page_library_choice_runs_off_the_main_thread(tmp_path):
    """pywebview's folder panel queues itself onto the main thread and waits for it, so it freezes
    the app when asked to run there: the fake refuses to run inside the dispatcher, as it does."""
    games = tmp_path / "Games"
    games.mkdir()
    on_main_thread = []

    class PickerNative(FakeNative):
        def choose_folder(self, window):
            assert not on_main_thread, "pywebview's folder panel waits on the main thread, which is this one"
            return (str(games),)

    def dispatch(function, *args):
        on_main_thread.append(True)
        try:
            return function(*args)
        finally:
            on_main_thread.pop()

    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=dict, native=PickerNative(),
                               dispatch=dispatch)
    app.attach_window(object())

    main.Api(app).choose_library()

    assert main.Api(app).library()["folder_name"] == "Games"


def test_a_game_is_running_by_its_apps_own_bundle_id(tmp_path, monkeypatch):
    asked = []

    class Running:
        @staticmethod
        def runningApplicationsWithBundleIdentifier_(bundle_id):
            asked.append(bundle_id)
            return [object()] if bundle_id == "com.example.packed-elsewhere" else []

    monkeypatch.setitem(sys.modules, "AppKit", type("AppKit", (), {"NSRunningApplication": Running}))
    tid = "0100D9F01D474000"
    folder = tmp_path / "Rhythm Heaven"
    for file_name, extra in (("Packed.app", {"CFBundleIdentifier": "com.example.packed-elsewhere"}),
                             ("Default.app", {})):
        contents = folder / file_name / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"DMTitleID": tid, **extra}))
    game = main.library.Game(id="Rhythm Heaven", path=folder, title_id=tid, app=main.library.read_app(folder / "Packed.app"))

    assert main.Native().game_running(game) is True
    assert asked == ["com.example.packed-elsewhere"]

    game.app = main.library.read_app(folder / "Default.app")  # no identifier recorded: nxnative's default
    assert main.Native().game_running(game) is False
    assert asked[-1] == f"org.nxnative.{tid.lower()}"


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
    kind, question, action, detail = native.calls[0]
    assert (kind, question, action) == ("confirm", "Import the NX Play save for Rhythm Heaven?", "Import")
    assert "NX Play save:" in detail
    assert "Current save:" in detail

    native.answer = True
    native.calls.clear()
    assert api.import_nxplay_save("Rhythm Heaven") is True
    assert (destination / "progress").read_text() == "NX Play"
    backups = list((support / tid / "save-backups").iterdir())
    assert len(backups) == 1
    assert backups[0].name.endswith(" before NX Play import")
    assert (backups[0] / "current").read_text() == "Switch Player"
    assert native.calls[-1] == ("alert", "NX Play save imported", f"Your previous save was backed up to {backups[0]}.")


def test_save_import_dialogs_run_on_the_main_thread(tmp_path):
    """pywebview calls the API on its own threads; AppKit only makes windows on the main one."""
    import threading
    tid = "0100D9F01D474000"
    folder = tmp_path / "Switch Games" / "Rhythm Heaven"
    source = folder / f"{tid}_build" / "run" / "save"
    source.mkdir(parents=True)
    (source / "progress").write_text("NX Play")
    (folder / f"Rhythm Heaven [{tid}].nsp").touch()
    threads = []

    class ThreadNative(FakeNative):
        def confirm(self, message, action="Quit", detail=""):
            threads.append(threading.current_thread())
            return True

        def alert(self, title, message):
            threads.append(threading.current_thread())

    main_queue = []
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(folder.parent)},
        native=ThreadNative(),
        dispatch=lambda function, *args: main_queue.append((function, args)),
        save_root=tmp_path / "nxnative-support",
    )
    api = main.Api(app)
    api.library()
    result = []
    worker = threading.Thread(target=lambda: result.append(api.import_nxplay_save("Rhythm Heaven")))
    worker.start()
    deadline = time.monotonic() + 5
    while worker.is_alive() and time.monotonic() < deadline:
        while main_queue:
            function, args = main_queue.pop(0)
            function(*args)
        time.sleep(0.01)

    assert result == [True]
    assert threads == [threading.main_thread(), threading.main_thread()]


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
    assert native.calls == [("alert", "Quit Rhythm Heaven first",
                             "Switch Player can't replace a save while its game is running.")]
    assert not (support / tid / "save").exists()


def test_save_import_is_not_offered_without_an_nxplay_save(tmp_path):
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

    assert api.library()["games"][0]["nxplay_save"] is False
    assert api.import_nxplay_save("Rhythm Heaven") is False
    assert native.calls == []
    assert (destination / "current").read_text() == "Switch Player"


def test_save_import_finds_a_nup_games_save_beside_the_file(tmp_path):
    tid = "0100D9F01D474000"
    game_id = f"Rhythm Heaven [{tid}].nup"
    (tmp_path / game_id).touch()
    source = tmp_path / f"{tid}_build" / "run" / "save"
    source.mkdir(parents=True)
    (source / "progress").write_text("NX Play")
    support = tmp_path / "nxnative-support"
    app = main.SwitchPlayerApp(
        data_dir=tmp_path / "data",
        first_launch=lambda: {"library_folder": str(tmp_path)},
        native=FakeNative(),
        save_root=support,
    )
    api = main.Api(app)
    api.library()

    assert api.import_nxplay_save(game_id) is True
    assert (support / tid / "save" / "progress").read_text() == "NX Play"


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

    def freshness(self, apps, built=None):
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


def test_a_finished_build_records_its_nxnative_commit_for_the_freshness_check(tmp_path, monkeypatch):
    """nxnative keeps a reused Game app's old DMCommit (#20), so the check counts from the commit
    Switch Player's own Build ran with."""
    import builds

    tid = "0100D9F01D474000"
    library_folder = tmp_path / "Switch Games"
    folder = library_folder / "Rhythm Heaven"
    contents = folder / "Rhythm Heaven Groove.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "DMTitleID": tid, "CFBundleDisplayName": "Rhythm Heaven Groove", "DMCommit": "123456789abc", "DMChain": "b-41"}))
    monkeypatch.setattr(nxnative.Copy, "update", lambda self, **kwargs: "fedcba987654")
    monkeypatch.setattr(nxnative.Copy, "build_commit", lambda self: "fedcba987654")
    monkeypatch.setattr(nxnative.Environment, "ready", lambda self: True)
    monkeypatch.setattr(nxnative, "build_command", lambda *args, **kwargs: (["true"], folder, {}))
    seen = []
    monkeypatch.setattr(nxnative.Copy, "freshness", lambda self, apps, built=None: seen.append(built) or {})

    class BuildNative(FakeNative):
        def notify(self, title, message):
            pass

    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", native=BuildNative(),
                               dispatch=lambda function, *args: function(*args),
                               first_launch=lambda: {"library_folder": str(library_folder)})
    app._library()
    app._fetch_freshness()
    assert seen[-1] == {tid: None}

    job = builds.Job(game_id="Rhythm Heaven", path=folder, name="Rhythm Heaven Groove")
    app._prepare(job)
    app._build_finished(job, False)  # stopped or failed: not what the Game app was built with
    app._fetch_freshness()
    assert seen[-1] == {tid: None}

    app._prepare(job)
    app._build_finished(job, True)
    app._fetch_freshness()
    assert seen[-1] == {tid: "fedcba987654"}
    assert main.SwitchPlayerApp(data_dir=tmp_path / "data", native=BuildNative(),
                                first_launch=dict)._records.commit("Rhythm Heaven") == "fedcba987654"


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


def built_game(folder, tid, name):
    contents = folder / name / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"DMTitleID": tid, "CFBundleDisplayName": name}))


def test_out_of_date_goes_by_the_game_files_each_build_started_with(tmp_path, monkeypatch):
    import os

    import builds

    tid = "0100D9F01D474000"
    library_folder = tmp_path / "Switch Games"
    folder = library_folder / "Rhythm Heaven"
    folder.mkdir(parents=True)
    base = folder / f"Rhythm Heaven [{tid}].nsp"
    base.write_bytes(b"b" * 100)
    os.utime(base, (1_000_000_000, 1_000_000_000))
    contents = folder / "Rhythm Heaven Groove.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"DMTitleID": tid, "CFBundleDisplayName": "Rhythm Heaven Groove"}))
    checkout = tmp_path / "player's nxnative"
    script = checkout / nxnative.BUILD_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text("# build script\n")
    monkeypatch.setattr(nxnative.Environment, "ready", lambda self: True)
    monkeypatch.setattr(nxnative, "build_command", lambda *args, **kwargs: (["true"], folder, {}))

    class BuildNative(FakeNative):
        def notify(self, title, message):
            pass

    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", native=BuildNative(),
                               dispatch=lambda function, *args: function(*args),
                               first_launch=lambda: {"library_folder": str(library_folder), "nxnative_checkout": str(checkout)})
    api = main.Api(app)

    def out_of_date():
        app._last_scan = None  # rescan: the page does, every 2 s
        return api.library()["games"][0]["out_of_date"]

    record = tmp_path / "data" / "build-records.json"

    # Built before Switch Player kept records: dates decide, and a record is never made from them.
    assert out_of_date() is False
    update = folder / f"Update [{tid[:-3]}800].nsp"  # an update dropped in later, with its old date
    update.write_bytes(b"u" * 40)
    os.utime(update, (1_000_000_000, 1_000_000_000))
    assert out_of_date() is False and not record.exists()  # the dates miss it, as before; it isn't hidden for good

    job = builds.Job(game_id="Rhythm Heaven", path=folder, name="Rhythm Heaven Groove")
    app._prepare(job)  # a Build starts, and stops or fails: nothing is recorded
    app._build_finished(job, False)
    assert out_of_date() is False and not record.exists()

    app._prepare(job)  # a Build starts: these are the files it will use
    late = folder / f"DLC [{tid[:-3]}001].nsp"
    late.write_bytes(b"d" * 70)  # dropped in while it builds: it didn't use that one
    os.utime(late, (1_000_000_000, 1_000_000_000))
    app._build_finished(job, True)
    assert record.is_file() and out_of_date() is True  # only the DLC is new to its record

    app._prepare(job)
    app._build_finished(job, True)
    assert out_of_date() is False
    assert app._records.commit("Rhythm Heaven") is None  # a folder git can't read has no commit to record

    gained = folder / f"More [{tid[:-3]}002].nsp"  # now it is caught, old date and all
    gained.write_bytes(b"m" * 90)
    os.utime(gained, (1_000_000_000, 1_000_000_000))
    assert out_of_date() is True


def test_nxnative_check_answers_like_the_app_update_check(tmp_path, monkeypatch):
    zelda, rhythm, mario = "01008CF01BAAC000", "0100D9F01D474000", "0100965017338000"
    library_folder = tmp_path / "Switch Games"
    library_folder.mkdir()
    native = FakeNative()
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=lambda: {"library_folder": str(library_folder)},
                               native=native)

    assert app._nxnative_report() == ("No games built in Switch Player yet",
                                      "Your first Build will use the latest nxnative.")

    built_game(library_folder, zelda, "Echoes of Wisdom")
    built_game(library_folder, rhythm, "Rhythm Heaven Groove")
    built_game(library_folder, mario, "Super Mario Party Jamboree")
    app._last_scan = None
    answers = {zelda: {"out_of_date": False, "newer": 0}, rhythm: {"out_of_date": False, "newer": 0},
               mario: {"out_of_date": False, "newer": 0}}
    monkeypatch.setattr(nxnative.Copy, "freshness", lambda self, apps, built=None: answers)
    assert app._nxnative_report() == ("Your games are up to date with nxnative",
                                      "Your next Build will use the latest nxnative.")

    # Counts, not names: a big Library's alert stays short, and the tiles say which.
    answers[rhythm] = {"out_of_date": False, "newer": 1200}
    assert app._nxnative_report() == ("nxnative has changes for 1 game",
                                      "It isn't out of date. It has changes since its last Build.")

    answers[mario] = {"out_of_date": False, "newer": 2}
    assert app._nxnative_report() == ("nxnative has changes for 2 games",
                                      "None are out of date. Each has changes since its last Build.")

    answers[zelda] = {"out_of_date": True, "newer": 3}
    assert app._nxnative_report() == ("nxnative has changes for 3 games",
                                      "1 is out of date, consider rebuilding it. 2 more have changes since their last Build.")

    answers[mario] = {"out_of_date": True, "newer": 2}
    assert app._nxnative_report() == ("nxnative has changes for 3 games",
                                      "2 are out of date, consider rebuilding them. 1 more has changes since its last Build.")

    answers[rhythm] = {"out_of_date": False, "newer": 0}
    assert app._nxnative_report() == ("nxnative has changes for 2 games",
                                      "2 are out of date, consider rebuilding them.")
    assert app._freshness == answers

    def fail(self, apps, built=None):
        raise nxnative.NxnativeError("Couldn't reach GitHub.")

    monkeypatch.setattr(nxnative.Copy, "freshness", fail)
    assert app._nxnative_report() == ("Switch Player couldn't check nxnative", "Couldn't reach GitHub.")


def test_an_offline_nxnative_check_says_it_compared_with_what_was_fetched_before(tmp_path, monkeypatch):
    zelda, rhythm = "01008CF01BAAC000", "0100D9F01D474000"
    library_folder = tmp_path / "Switch Games"
    library_folder.mkdir()
    built_game(library_folder, zelda, "Echoes of Wisdom")
    built_game(library_folder, rhythm, "Rhythm Heaven Groove")
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=lambda: {"library_folder": str(library_folder)},
                               native=FakeNative())
    answers = {zelda: {"out_of_date": False, "newer": 0}, rhythm: {"out_of_date": False, "newer": 0}}

    def offline(self, apps, built=None):
        self.fetch_error = "Could not resolve host: github.com"
        return answers

    monkeypatch.setattr(nxnative.Copy, "freshness", offline)
    assert app._nxnative_report() == (
        "Switch Player couldn't reach GitHub",
        "Compared with the nxnative it last fetched, your games are up to date. (Could not resolve host: github.com)")

    answers[zelda] = {"out_of_date": True, "newer": 3}
    title, message = app._nxnative_report()
    assert title == "nxnative has changes for 1 game"
    assert message == ("1 is out of date, consider rebuilding it. Switch Player couldn't reach GitHub "
                       "(Could not resolve host: github.com), so this compares with the nxnative it last fetched.")
    assert app._freshness == answers  # the tiles show it too


def test_nxnative_check_menu_alerts_from_the_main_thread(tmp_path, monkeypatch):
    import threading
    native = FakeNative()
    dispatched = []
    app = main.SwitchPlayerApp(data_dir=tmp_path, first_launch=dict, native=native,
                               dispatch=lambda function, *args: dispatched.append((function, args)))
    monkeypatch.setattr(app, "_nxnative_report", lambda: ("Title", "Message"))

    app.check_nxnative()
    for thread in threading.enumerate():
        if thread.name == "nxnative-check":
            thread.join(5)

    assert dispatched == [(native.alert, ("Title", "Message"))]


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
    monkeypatch.setattr(main.prerequisites, "check", lambda: {
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


def pane_app(tmp_path, builder=None):
    folder = tmp_path / "Switch Games" / "Ocarina of Time"
    folder.mkdir(parents=True)
    (folder / "Ocarina of Time [0400486024DEC000].nup").touch()
    return main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=lambda: {"library_folder": str(folder.parent)},
                                builder=builder or FakeBuilder(), native=FakeNative(),
                                dispatch=lambda function, *args: function(*args))


def test_the_build_log_pane_reads_the_log_in_whole_lines(tmp_path):
    builder = FakeBuilder()
    builder.log = None
    api = main.Api(pane_app(tmp_path, builder))
    assert api.library()["games"][0]["build_log"] is False
    assert api.build_log("Ocarina of Time") is None  # no Build yet

    builder.log = tmp_path / "Ocarina of Time.log"
    builder.log.write_bytes("Build of Ocarina of Time\n── Downloading nxnative\n   Receiving obj".encode())
    assert api.library()["games"][0]["build_log"] is True
    first = api.build_log("Ocarina of Time", 0, None)
    assert first["text"] == "Build of Ocarina of Time\n── Downloading nxnative\n"  # the half-written line waits
    with open(builder.log, "ab") as f:
        f.write("ects: 100%, done.\n[RED] less than 31 GB free\n".encode())
    second = api.build_log("Ocarina of Time", first["offset"], first["key"])
    assert second["key"] == first["key"]
    assert second["text"] == "   Receiving objects: 100%, done.\n[RED] less than 31 GB free\n"
    assert api.build_log("Ocarina of Time", second["offset"], second["key"])["text"] == ""

    builder.log.rename(tmp_path / "Ocarina of Time.prev.log")  # the next Build: a new file
    builder.log.write_text("Build of Ocarina of Time, again\n")
    again = api.build_log("Ocarina of Time", second["offset"], second["key"])
    assert again["key"] != second["key"] and again["text"] == "Build of Ocarina of Time, again\n"


def test_copy_log_copies_the_whole_file_as_written(tmp_path, monkeypatch):
    builder = FakeBuilder()
    builder.log = None
    app = pane_app(tmp_path, builder)
    api = main.Api(app)
    api.library()
    assert api.copy_build_log("Ocarina of Time") is False  # no Build yet: nothing to copy
    assert app._native.calls == []

    builder.log = tmp_path / "Ocarina of Time.log"
    text = "Build of Ocarina of Time\n" + "   translating\n" * 20000 + "[RED] less than 31 GB free\n   Receiving obj"
    builder.log.write_text(text)
    assert api.copy_build_log("Ocarina of Time") is True
    assert app._native.calls == [("copy", text)]  # all of it, markers and a half-written line included

    app._native.answer = False  # the clipboard refused it
    assert api.copy_build_log("Ocarina of Time") is False
    builder.log.unlink()  # gone between the tile's poll and the click
    assert api.copy_build_log("Ocarina of Time") is False


def test_a_long_build_log_starts_at_its_last_lines(tmp_path, monkeypatch):
    builder = FakeBuilder()
    builder.log = tmp_path / "long.log"
    builder.log.write_text("".join(f"line {i}\n" for i in range(1000)))
    monkeypatch.setattr(main, "BUILD_LOG_FIRST", 100)
    api = main.Api(pane_app(tmp_path, builder))
    piece = api.build_log("Ocarina of Time", 0, None)
    assert piece["text"].startswith("line 9") and piece["text"].endswith("line 999\n")
    assert piece["offset"] == builder.log.stat().st_size


def test_the_build_log_pane_keeps_its_state(tmp_path):
    app = pane_app(tmp_path)
    api = main.Api(app)
    assert api.ui_state() == {"log_open": True, "log_width": 380, "log_game": None, "settings_advanced": False}
    api.set_ui_state({"log_open": False, "log_width": 9999, "log_game": "Ocarina of Time", "settings_advanced": True})
    reopened = main.Api(main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=dict))
    assert reopened.ui_state() == {"log_open": False, "log_width": 720, "log_game": "Ocarina of Time",
                                   "settings_advanced": True}
    api.set_ui_state({"log_width": "wide", "log_open": "yes", "settings_advanced": 1})  # anything else is ignored
    assert api.ui_state() == {"log_open": False, "log_width": 720, "log_game": "Ocarina of Time",
                              "settings_advanced": True}
    api.set_ui_state(None)


def test_an_earlier_launchs_build_log_shows_before_any_build(tmp_path):
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=dict)
    app._settings.update({"library_folder": str(tmp_path / "Switch Games")})
    (tmp_path / "Switch Games" / "Ocarina of Time").mkdir(parents=True)
    log = builds.log_path(tmp_path / "data" / "Logs" / "Builds", "Ocarina of Time")
    log.parent.mkdir(parents=True)
    log.write_text("Build of Ocarina of Time\nExit code 0.\n")
    api = main.Api(app)
    assert app._builder is None
    assert api.library()["games"][0]["build_log"] is True
    assert api.build_log("Ocarina of Time")["text"] == "Build of Ocarina of Time\nExit code 0.\n"


def test_a_new_versions_first_launch_shows_the_bundles_icon_and_refreshes_the_cache(tmp_path, monkeypatch):
    app_path = tmp_path / "Switch Player.app"
    (app_path / "Contents" / "Resources").mkdir(parents=True)
    (app_path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIconFile": "SwitchPlayer.icns"}))
    icon = app_path / "Contents" / "Resources" / "SwitchPlayer.icns"
    icon.write_bytes(b"icns")
    refreshed = []
    monkeypatch.setattr(launch, "refresh_icon_cache",
                        lambda app, data, version: refreshed.append((app, data, version)) or
                        (data / launch.LAUNCHED_VERSION).write_text(version))

    class IconNative(FakeNative):
        def show_bundle_icon(self, path):
            self.calls.append(("icon", path))

    native = IconNative()
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", first_launch=dict, native=native)
    app._refresh_icon_if_updated(None)  # running from source: nothing to do
    app._refresh_icon_if_updated(app_path)
    marker = tmp_path / "data" / launch.LAUNCHED_VERSION
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:  # the refresh runs on a thread of its own
        time.sleep(0.01)
    assert native.calls == [("icon", icon)]
    assert refreshed == [(app_path, tmp_path / "data", main.VERSION)]

    native.calls.clear()
    app._refresh_icon_if_updated(app_path)  # the next launch of the same version
    assert native.calls == []


OCARINA = "0400486024DEC000"
FAKE_BUILD = Path(__file__).resolve().parent / "fake_build.py"


class OcarinaNative(FakeNative):
    def notify(self, title, message):
        self.calls.append(("notify", title, message))

    def convert_icon(self, source, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\xff\xd8\xff" + source.read_bytes())

    def prevent_sleep(self):
        return "held"

    def allow_sleep(self, token):
        pass


def ocarina(tmp_path):
    """Ocarina of Time during its first Build: titledb has no entry for it, and the Build has just
    unpacked the game's own icon into its build folder."""
    folder = tmp_path / "Switch Games" / "Ocarina of Time"
    icon = folder / f"{OCARINA}_build" / "extract" / "v131072" / "icon.jpg"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"\xff\xd8\xffown icon")
    (folder / "Ocarina of Time.nup").touch()
    return folder, icon


def ocarina_app(tmp_path, folder, native=None, builder=None):
    return main.SwitchPlayerApp(data_dir=tmp_path / "data", save_root=tmp_path / "saves",
                                first_launch=lambda: {"library_folder": str(folder.parent)},
                                builder=builder or FakeBuilder(), native=native or OcarinaNative(),
                                dispatch=lambda function, *args: function(*args))


def game_event(icon_path, **changes):
    return {"ev": "game", "title_id": OCARINA, "name": "The Legend of Zelda: Ocarina of Time", "version": "1.0",
            "bundle_id": "org.nxnative.0400486024dec000", "out": "/x/The Legend of Zelda - Ocarina of Time.app",
            "icon": str(icon_path), "t": 1.0, **changes}


def jpeg(data):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def test_a_builds_game_event_names_the_game_and_gives_its_icon_before_it_finishes(tmp_path):
    """Issue #42: titledb doesn't list Ocarina of Time, so its tile kept its Game folder's name and
    a plain tile through a whole Build, though nxnative names it about a minute in."""
    folder, icon = ocarina(tmp_path)
    native = OcarinaNative()
    app = ocarina_app(tmp_path, folder, native)
    api = main.Api(app)
    before = api.library()["games"][0]
    assert (before["name"], before["art"]) == ("Ocarina of Time", None)

    job = builds.Job(game_id="Ocarina of Time", path=folder, name="Ocarina of Time")
    app._build_named(job, game_event(icon))
    icon.unlink()  # an IPA build clears the extraction it was in: the copy stays
    game = api.library()["games"][0]
    assert game["name"] == "The Legend of Zelda: Ocarina of Time"
    assert game["art"] is not None
    assert api.art("Ocarina of Time") == jpeg(b"\xff\xd8\xffown icon")

    # Kept when that Build fails, which the notification says by the game's own name, and after a relaunch.
    job.state = "failed"
    app._build_finished(job, False)
    assert native.calls[-1] == ("notify", "The Legend of Zelda: Ocarina of Time", "Build failed")
    again = main.Api(ocarina_app(tmp_path, folder))
    game = again.library()["games"][0]
    assert game["name"] == "The Legend of Zelda: Ocarina of Time"
    assert again.art("Ocarina of Time") == jpeg(b"\xff\xd8\xffown icon")


def test_eshop_art_and_an_existing_game_app_come_before_the_game_event(tmp_path):
    folder, icon = ocarina(tmp_path)
    data = tmp_path / "data"
    (data / "titledb" / "art").mkdir(parents=True)
    (data / "titledb" / "index.json").write_text(json.dumps({
        "fetched": 0, "titles": {OCARINA: {"name": "Ocarina of Time eShop", "icon": None}}, "missing": []}))
    (data / "titledb" / "art" / f"{OCARINA}.jpg").write_bytes(b"\xff\xd8\xffeshop")
    app = ocarina_app(tmp_path, folder)
    api = main.Api(app)
    app._build_named(builds.Job(game_id="Ocarina of Time", path=folder, name="Ocarina of Time"), game_event(icon))

    game = api.library()["games"][0]
    assert game["name"] == "The Legend of Zelda: Ocarina of Time"  # the name its Game app will carry, over the eShop's
    assert api.art("Ocarina of Time") == jpeg(b"\xff\xd8\xffeshop")  # 1024×1024, over the game's own 256×256

    # A Game app already there (a Rebuild's) speaks for itself until the Build replaces it.
    (data / "titledb" / "art" / f"{OCARINA}.jpg").unlink()
    resources = folder / "Zelda.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "AppIcon.icns").write_bytes(b"app icon")
    (resources.parent / "Info.plist").write_bytes(plistlib.dumps({"DMTitleID": OCARINA, "CFBundleDisplayName": "Zelda"}))
    app._last_scan = None
    game = api.library()["games"][0]
    assert game["name"] == "Zelda"
    assert api.art("Ocarina of Time") == jpeg(b"\xff\xd8\xffapp icon")


def test_a_game_event_without_an_icon_or_a_name_or_for_another_title(tmp_path):
    folder, icon = ocarina(tmp_path)
    app = ocarina_app(tmp_path, folder)
    api = main.Api(app)
    job = builds.Job(game_id="Ocarina of Time", path=folder, name="Ocarina of Time")
    kept = tmp_path / "data" / "Icons" / "Builds" / "Ocarina of Time.jpg"
    app._build_named(job, game_event(icon))
    assert kept.is_file()

    app._build_named(job, game_event(icon, icon=None))  # a later Build's: the game has no icon now
    assert not kept.exists()
    game = api.library()["games"][0]
    assert (game["name"], game["art"]) == ("The Legend of Zelda: Ocarina of Time", None)  # a plain tile with its name

    app._build_named(job, game_event(icon, title_id="zelda"))
    app._build_named(job, game_event(icon, name=None))
    app._build_named(job, game_event(icon, name="  "))
    assert app._records.game("Ocarina of Time")["name"] == "The Legend of Zelda: Ocarina of Time"

    # The Game folder has come to hold another title: what the event said was about the last one.
    (folder / f"{OCARINA}_build").rename(folder / "0100D9F01D474000_build")
    app._last_scan = None
    assert api.library()["games"][0]["name"] == "Ocarina of Time"


def test_the_tile_takes_the_game_events_name_while_its_build_runs(tmp_path, monkeypatch):
    """Through the real Builder, with the stand-in build script: the name shows mid-Build, and the
    Build finished notification uses it."""
    folder, icon = ocarina(tmp_path)
    (folder / "plan.json").write_text(json.dumps({
        "stages": [["unpack v131072", 0.05], ["Mac shaders", 1.5]], "title_id": OCARINA,
        "name": "The Legend of Zelda: Ocarina of Time",
        "game": {"title_id": OCARINA, "name": "The Legend of Zelda: Ocarina of Time", "icon": str(icon)}}))
    monkeypatch.setattr(nxnative.Copy, "update", lambda self, **kwargs: "fedcba987654")
    monkeypatch.setattr(nxnative.Copy, "build_commit", lambda self: "fedcba987654")
    monkeypatch.setattr(nxnative.Environment, "ready", lambda self: True)
    monkeypatch.setattr(nxnative.Environment, "ensure", lambda self, **kwargs: None)
    monkeypatch.setattr(nxnative, "build_command", lambda copy, environment, path, **kwargs: (
        [sys.executable, str(FAKE_BUILD), str(path), "--platform", "mac-app", "--progress", "json"], path, None))
    native = OcarinaNative()
    app = main.SwitchPlayerApp(data_dir=tmp_path / "data", save_root=tmp_path / "saves", native=native,
                               first_launch=lambda: {"library_folder": str(folder.parent)},
                               dispatch=lambda function, *args: function(*args))
    api = main.Api(app)
    assert api.library()["games"][0]["name"] == "Ocarina of Time"

    api.build("Ocarina of Time")
    end = time.time() + 10
    while time.time() < end and api.library()["games"][0]["name"] == "Ocarina of Time":
        time.sleep(0.05)
    game = api.library()["games"][0]
    assert game["name"] == "The Legend of Zelda: Ocarina of Time" and game["state"] == "building"
    assert api.art("Ocarina of Time") == jpeg(b"\xff\xd8\xffown icon")

    end = time.time() + 10
    while time.time() < end and ("notify", "The Legend of Zelda: Ocarina of Time", "Build finished") not in native.calls:
        time.sleep(0.05)
    assert ("notify", "The Legend of Zelda: Ocarina of Time", "Build finished") in native.calls
    app._builder.stop_all(grace=5)
