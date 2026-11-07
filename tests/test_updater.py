"""Release decisions and update safety, without a network or an installed app."""

import sys
from datetime import date

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
        {"name": "Switch-Player-1.2.0.dmg", "browser_download_url": "https://example.invalid/Switch-Player-1.2.0.dmg"},
    ]}
    assert parse_release(release) == ("1.2.0", "https://example.invalid/Switch-Player-1.2.0.dmg")


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


def test_checks_once_a_day():
    today = date(2026, 11, 6)
    assert check_is_due(None, today)
    assert check_is_due("2026-11-05", today)
    assert not check_is_due("2026-11-06", today)


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

            def scheduledTimerWithTimeInterval_repeats_block_(self, interval, repeats, block):
                self.calls.append(interval)
                return "timer"

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

        self.timer, self.installed, self.alerts, self.on_main = Timer(), [], [], []
        monkeypatch.setitem(sys.modules, "Quartz", Source)
        monkeypatch.setitem(sys.modules, "AppKit", type("AppKit", (), {"NSEvent": Events}))
        monkeypatch.setitem(sys.modules, "Foundation", type("Foundation", (), {"NSTimer": self.timer}))
        monkeypatch.setattr(updater, "_alert", lambda title, message, buttons=(): self.alerts.append((title, message)))
        monkeypatch.setattr(updater, "_on_main", lambda function, *args: self.on_main.append((function, args)))
        self.update = updater.Updater(None, "0.1.0", "/unused", lambda: busy, lambda *args: None, lambda: None)
        self.update.unavailable_reason = None
        self.update.status, self.update.version = updater.READY, "0.2.0"
        monkeypatch.setattr(self.update, "_install", lambda: self.installed.append(True))


def test_check_for_updates_holds_a_waiting_update_for_the_build_and_says_so(monkeypatch):
    held = MainThreadUpdate(monkeypatch, busy=True, idle=0)

    held.update._install_if_quiet(True)

    assert held.installed == []
    assert held.alerts == [("Switch Player 0.2.0 is ready", "It will install once the Build ends.")]
    assert held.timer.calls == [updater.INSTALL_RETRY]  # the Build's end, then the automatic rules


def test_check_for_updates_installs_at_once_when_nothing_is_building(monkeypatch):
    now = MainThreadUpdate(monkeypatch, busy=False, idle=0)  # they just used the menu: not "quiet"

    now.update._install_if_quiet(True)

    assert now.installed == [True] and now.alerts == []


def test_an_automatic_install_still_waits_for_a_quiet_keyboard_and_mouse(monkeypatch):
    typing = MainThreadUpdate(monkeypatch, busy=False, idle=2)

    typing.update._install_if_quiet()

    assert typing.installed == [] and typing.alerts == []
    assert typing.timer.calls == [updater.INSTALL_RETRY]


def test_the_menu_sends_a_waiting_update_to_the_main_thread_not_straight_to_install(monkeypatch):
    waiting = MainThreadUpdate(monkeypatch, busy=True, idle=60)

    waiting.update.check_now()  # runs on pywebview's menu thread

    assert waiting.installed == [] and waiting.alerts == []
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
