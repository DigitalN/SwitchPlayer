"""Release decisions and update safety, without a network or an installed app."""

import subprocess
import sys
import time

import pytest

import updater
from launch import in_applications_folder
from updater import UpdateError, check_is_due, is_newer, parse_release, signing_requirement, team_identifier


def test_versions_compare_number_by_number():
    assert is_newer("1.10.0", "1.9.0")
    assert is_newer("1.1", "1.0.9")
    assert is_newer("v2.0", "1.99")
    assert not is_newer("1.1.0", "1.1")
    assert not is_newer("1.0.0", "1.1.0")


def test_release_gives_version_and_disk_image():
    release = {"tag_name": "v1.2.0", "assets": [
        {"name": "notes.txt", "browser_download_url": "https://example.invalid/notes.txt"},
        {"name": "Switch-Player-1.2.0.dmg", "browser_download_url": "https://example.invalid/Switch-Player-1.2.0.dmg",
         "size": 123456789},
    ]}
    assert parse_release(release) == ("1.2.0", "https://example.invalid/Switch-Player-1.2.0.dmg", 123456789)
    del release["assets"][1]["size"]  # no size: the download shows no %
    assert parse_release(release)[2] is None


def test_release_without_disk_image_or_tag_is_refused():
    with pytest.raises(UpdateError, match="no disk image"):
        parse_release({"tag_name": "v1.2.0", "assets": [{"name": "Switch-Player.zip", "browser_download_url": "x"}]})
    with pytest.raises(UpdateError):
        parse_release({"message": "Not Found"})


def test_team_comes_from_codesign_details():
    details = "Identifier=com.digitaln.switchplayer\nTeamIdentifier=XT4LT7MS6Y\n"
    assert team_identifier(details) == "XT4LT7MS6Y"
    assert team_identifier("TeamIdentifier=not set\n") is None
    assert team_identifier("code object is not signed at all") is None


def test_signing_requirement_pins_identifier_and_team():
    requirement = signing_requirement("com.digitaln.switchplayer", "XT4LT7MS6Y")
    assert requirement.startswith('=identifier "com.digitaln.switchplayer" and anchor apple generic')
    assert requirement.endswith('certificate leaf[subject.OU] = "XT4LT7MS6Y"')


def test_checks_once_24_hours_have_passed_since_the_last_check_that_got_through():
    now = 1_800_000_000.0
    assert check_is_due(None, now)
    assert check_is_due(str(now - updater.CHECK_EVERY), now)
    assert not check_is_due(str(now - updater.CHECK_EVERY + 60), now)
    assert not check_is_due(str(now), now)
    assert check_is_due("2026-11-06", now)  # the date earlier versions wrote
    assert check_is_due(str(now + 3600), now)  # a clock set back


def test_applications_folder(tmp_path):
    assert in_applications_folder("/Applications/Switch Player.app")
    assert not in_applications_folder("/Applications Old/Switch Player.app")
    assert not in_applications_folder(tmp_path / "Switch Player.app")


def test_source_checkouts_do_not_update(tmp_path):
    update = updater.Updater(None, "0.1.0", tmp_path, lambda: False, lambda *args: None, lambda: None)
    assert update.unavailable_reason == "running from source"


def test_automatic_install_waits_for_the_build_queue(monkeypatch):
    class Timer:
        def __init__(self):
            self.calls = []

        def scheduledTimerWithTimeInterval_repeats_block_(self, interval, repeats, block):
            self.calls.append((interval, block))
            return "timer"

    class Events:
        @staticmethod
        def pressedMouseButtons():
            return 0

    class EventsSource:
        kCGEventSourceStateCombinedSessionState = 1
        kCGAnyInputEventType = 2

        @staticmethod
        def CGEventSourceSecondsSinceLastEventType(state, event_type):
            return 60

    timer = Timer()
    monkeypatch.setitem(sys.modules, "Quartz", EventsSource)
    monkeypatch.setitem(sys.modules, "AppKit", type("AppKit", (), {"NSEvent": Events}))
    monkeypatch.setitem(sys.modules, "Foundation", type("Foundation", (), {"NSTimer": timer}))
    update = updater.Updater(None, "0.1.0", "/unused", lambda: True, lambda *args: None, lambda: None)
    update.status = updater.READY
    installed = []
    monkeypatch.setattr(update, "_install", lambda: installed.append(True))

    update._install_if_quiet()

    assert installed == []
    assert timer.calls[0][0] == updater.INSTALL_RETRY


class MainThreadUpdate:
    """An Updater with a downloaded update waiting, and the AppKit calls it makes faked."""

    def __init__(self, monkeypatch, busy, idle):
        class Timer:
            calls = []

            blocks = []

            def scheduledTimerWithTimeInterval_repeats_block_(self, interval, repeats, block):
                self.calls.append(interval)
                self.blocks.append(block)
                return self

            def invalidate(self):
                pass

        class Events:
            @staticmethod
            def pressedMouseButtons():
                return 0

        class Source:
            kCGEventSourceStateCombinedSessionState = 1
            kCGAnyInputEventType = 2

            @staticmethod
            def CGEventSourceSecondsSinceLastEventType(state, event_type):
                return idle

        self.timer, self.installed, self.on_main = Timer(), [], []
        monkeypatch.setitem(sys.modules, "Quartz", Source)
        monkeypatch.setitem(sys.modules, "AppKit", type("AppKit", (), {"NSEvent": Events}))
        monkeypatch.setitem(sys.modules, "Foundation", type("Foundation", (), {"NSTimer": self.timer}))
        monkeypatch.setattr(updater, "_on_main", lambda function, *args: self.on_main.append((function, args)))
        self.update = updater.Updater(None, "0.1.0", "/unused", lambda: busy, lambda *args: None, lambda: None)
        self.update.unavailable_reason = None
        self.update.status, self.update.version = updater.READY, "0.2.0"
        monkeypatch.setattr(self.update, "_install", lambda: self.installed.append(True))


def test_check_for_updates_holds_a_waiting_update_for_the_build_and_says_so(monkeypatch):
    held = MainThreadUpdate(monkeypatch, busy=True, idle=0)

    held.update._install_if_quiet(True)

    assert held.installed == []
    toast = held.update.toast()
    assert (toast["kind"], toast["version"], toast["held"]) == ("ready", "0.2.0", True)  # "…will install when the Build ends"
    assert held.timer.calls == [updater.INSTALL_RETRY]  # the Build's end, then the automatic rules


def test_check_for_updates_installs_at_once_when_nothing_is_building(monkeypatch):
    now = MainThreadUpdate(monkeypatch, busy=False, idle=0)  # they just used the menu: not "quiet"

    now.update._install_if_quiet(True)

    assert now.installed == [True]


def test_an_update_held_for_a_build_installs_when_the_build_ends_without_waiting_for_a_quiet_mouse(monkeypatch):
    """The toast says "when the Build ends", so the retries remember the player asked."""
    busy = [True]
    held = MainThreadUpdate(monkeypatch, busy=True, idle=0)  # they keep using the Mac
    held.update._is_busy = lambda: busy[0]

    held.update._install_if_quiet(True)
    retry = held.timer.blocks[0]
    retry(None)  # the Build is still running
    assert held.installed == []

    busy[0] = False
    retry(None)  # 30 s on, the Build has ended; the player is still typing

    assert held.installed == [True]


def test_an_automatic_hold_still_waits_for_a_quiet_mouse_after_the_build(monkeypatch):
    busy = [True]
    held = MainThreadUpdate(monkeypatch, busy=True, idle=3)
    held.update._is_busy = lambda: busy[0]

    held.update._install_if_quiet()
    busy[0] = False
    held.timer.blocks[0](None)

    assert held.installed == []  # nobody asked: it waits for ten quiet seconds as well


def test_an_automatic_install_still_waits_for_a_quiet_keyboard_and_mouse(monkeypatch):
    typing = MainThreadUpdate(monkeypatch, busy=False, idle=2)

    typing.update._install_if_quiet()

    assert typing.installed == []
    assert typing.timer.calls == [updater.INSTALL_RETRY]
    toast = typing.update.toast()
    assert (toast["kind"], toast["held"]) == ("ready", False)  # with Install Now


def test_install_now_installs_a_waiting_update_without_waiting_for_quiet(monkeypatch):
    typing = MainThreadUpdate(monkeypatch, busy=False, idle=2)
    typing.update._install_if_quiet()

    typing.update.install_now()  # the toast's button, on pywebview's thread
    function, args = typing.on_main[-1]
    function(*args)

    assert typing.installed == [True]


def test_the_menu_sends_a_waiting_update_to_the_main_thread_not_straight_to_install(monkeypatch):
    waiting = MainThreadUpdate(monkeypatch, busy=True, idle=60)

    waiting.update.check_now()  # runs on pywebview's menu thread

    assert waiting.installed == []
    assert waiting.on_main == [(waiting.update._install_if_quiet, (True,))]


def test_relaunch_keeps_test_data_and_feed_isolated(tmp_path, monkeypatch):
    import launch
    calls = []
    monkeypatch.setenv("SWITCHPLAYER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWITCHPLAYER_UPDATE_FEED", "file:///tmp/feed.json")
    monkeypatch.setattr(launch.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv))

    launch.relaunch(tmp_path / "Switch Player.app", background=True)

    argv = calls[0]
    assert argv[:3] == ["/bin/sh", "-c", launch.RELAUNCH_SCRIPT]
    assert "-n" in argv[2]
    assert f"SWITCHPLAYER_DATA_DIR={tmp_path / 'data'}" in argv
    assert "SWITCHPLAYER_UPDATE_FEED=file:///tmp/feed.json" in argv


class Checks:
    """An Updater whose checks run here, one at a time, against a made-up GitHub answer."""

    def __init__(self, monkeypatch, tmp_path, release=None, error=None):
        self.on_main, self.later, self.started, self.changes = [], [], [], []
        monkeypatch.setattr(updater, "_on_main", lambda function, *args: self.on_main.append((function, args)))
        monkeypatch.setattr(updater, "_on_main_later", lambda delay, function, *args: self.later.append((delay, function)))
        self.update = updater.Updater(None, "0.1.0", tmp_path, lambda: False, lambda *args: None, lambda: None,
                                      on_toast=lambda: self.changes.append(self.kind()))
        self.update.unavailable_reason = None
        self.release, self.error = release, error

        def latest():
            if self.error:
                raise UpdateError(self.error)
            return self.release
        monkeypatch.setattr(self.update, "_latest_release", latest)
        monkeypatch.setattr(self.update, "_download", lambda url, size, version: (version, "app", "folder"))

        class Thread:  # the check runs when it starts, here
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()
        monkeypatch.setattr(updater.threading, "Thread", Thread)

    def kind(self):
        toast = self.update.toast()
        return toast and toast["kind"]


def test_the_launch_check_shows_checking_then_up_to_date_which_fades(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path, release=("0.1.0", "url", 10))

    checks.update._start_check(asked=False, visible=True)

    assert checks.changes == ["checking", "up_to_date"]
    assert checks.update.toast()["fades"] is True
    assert check_is_due((tmp_path / "last-update-check").read_text(), time.time()) is False


def test_a_toast_that_fades_is_gone_after_a_while_even_if_no_page_showed_it(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path, release=None)
    checks.update._start_check(asked=False, visible=True)
    started = time.monotonic()

    monkeypatch.setattr(updater.time, "monotonic", lambda: started + updater.TOAST_KEEP + 1)

    assert checks.update.toast() is None


def test_an_open_app_checks_again_once_24_hours_have_passed(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path, release=None)
    starts = []
    monkeypatch.setattr(checks.update, "_start_check", lambda asked, visible: starts.append((asked, visible)))
    last = tmp_path / "last-update-check"

    last.write_text(str(time.time() - 3600))
    checks.update._check_if_due()
    assert starts == []

    last.write_text(str(time.time() - updater.CHECK_EVERY - 1))
    checks.update._check_if_due()
    assert starts == [(False, True)]  # shown, as the launch's check is


def test_a_failed_automatic_check_says_so_once_and_retries_quietly(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path, error="Couldn't reach GitHub (offline).")

    checks.update._start_check(asked=False, visible=True)  # the launch's check
    toast = checks.update.toast()
    assert (toast["kind"], toast["stage"], toast["sticky"], toast["fades"]) == ("failed", "check", False, True)
    assert toast["reason"] == "Couldn't reach GitHub (offline)."

    checks.changes.clear()
    (tmp_path / "last-update-check").write_text(str(time.time()))  # checked earlier today: not due
    checks.update._check_if_due()  # the next hourly look tries again anyway, without a toast
    assert checks.changes == [None] and checks.update.toast() is None

    checks.error, checks.release = None, None
    checks.update._check_if_due()  # back online: it got through, still quietly
    assert checks.update.toast() is None and checks.update._last_failed is False


def test_check_for_updates_failure_stays_until_closed(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path, error="Couldn't reach GitHub (offline).")

    checks.update.check_now()

    toast = checks.update.toast()
    assert (toast["kind"], toast["sticky"], toast["fades"]) == ("failed", True, False)
    checks.update.dismiss(toast["id"])
    assert checks.update.toast() is None


def test_a_quiet_check_that_finds_an_update_shows_from_downloading(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path, release=("0.2.0", "url", 10))
    checks.update._last_failed = True

    checks.update._check_if_due()

    assert checks.changes == ["downloading"]
    assert checks.update.status == updater.READY
    assert checks.on_main[-1] == (checks.update._install_if_quiet, (False,))  # an automatic install's rules


def test_check_for_updates_during_a_quiet_check_makes_it_the_players(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path)
    checks.update.status, checks.update._visible = updater.CHECKING, False

    checks.update.check_now()

    assert checks.update._asked is True
    assert checks.kind() == "checking"


def test_installing_shows_before_the_app_quits(monkeypatch, tmp_path):
    checks = Checks(monkeypatch, tmp_path)
    checks.update._staged = ("0.2.0", "app", "folder")
    checks.update.status = updater.READY

    checks.update._install()

    assert checks.kind() == "installing"
    assert checks.later == [(updater.INSTALL_DELAY, checks.update._replace)]


def test_the_launch_after_an_update_says_so_once(tmp_path):
    (tmp_path / updater.INSTALLED_FILE).write_text("0.1.0")
    update = updater.Updater(None, "0.1.0", tmp_path, lambda: False, lambda *args: None, lambda: None)

    assert update._take_installed() == "0.1.0"
    assert update._take_installed() is None  # gone once read
    (tmp_path / updater.INSTALLED_FILE).write_text("0.0.9")  # an install that didn't take
    assert update._take_installed() is None


def test_a_copy_outside_applications_says_updates_are_off_at_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "own_team", lambda app: "XT4LT7MS6Y")
    update = updater.Updater(tmp_path / "Switch Player.app", "0.1.0", tmp_path, lambda: False,
                             lambda *args: None, lambda: None)

    update.start()

    toast = update.toast()
    assert (toast["kind"], toast["reason"]) == ("off", "move Switch Player to your Applications folder")


def test_source_checkouts_and_ad_hoc_copies_stay_quiet_about_updates(monkeypatch, tmp_path):
    source = updater.Updater(None, "0.1.0", tmp_path, lambda: False, lambda *args: None, lambda: None)
    monkeypatch.setattr(updater, "own_team", lambda app: None)
    ad_hoc = updater.Updater(tmp_path / "Switch Player.app", "0.1.0", tmp_path, lambda: False,
                             lambda *args: None, lambda: None)

    for update in (source, ad_hoc):
        update.start()
        assert update.unavailable_reason and update.toast() is None


def test_a_download_reports_its_progress(monkeypatch, tmp_path):
    image = tmp_path / "update.dmg"

    class Curl:
        returncode = 0
        writes = [b"x" * 50, b"x" * 50]

        def __init__(self, argv, **kwargs):
            assert argv[0] == "/usr/bin/curl" and argv[-1] == "https://example.invalid/update.dmg"

        def communicate(self, timeout):
            if self.writes:
                with open(image, "ab") as f:
                    f.write(self.writes.pop(0))
                raise subprocess.TimeoutExpired("curl", timeout)
            return "\n200", ""

    monkeypatch.setattr(updater.subprocess, "Popen", Curl)
    progress = []

    code, _ = updater._curl("https://example.invalid/update.dmg", image, max_time=600, size=100,
                            on_progress=progress.append)

    assert code == 200
    assert progress == [0.5, 1.0]
