"""The relaunch script with stand-ins for open and ps: no app is launched."""

import os
import subprocess
import time

import launch


def _quitting_process(seconds):
    out = subprocess.run(["/bin/sh", "-c", f"sleep {seconds} >/dev/null 2>&1 & echo $!"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip()


def _relaunch(tmp_path, starts_on_attempt):
    calls, running = tmp_path / "calls", tmp_path / "running"
    calls.write_text("")
    running.write_text("")
    fake_open = tmp_path / "open"
    fake_open.write_text(
        '#!/bin/sh\n'
        f'echo "$*" >> "{calls}"\n'
        f'[ "$(wc -l < "{calls}")" -eq {starts_on_attempt} ] && echo "$EXE" >> "{running}"\n'
        'exit 0\n')
    script = (launch.RELAUNCH_SCRIPT.replace("/usr/bin/open", f'/bin/sh "{fake_open}"')
              .replace("/bin/ps -axo comm=", f'cat "{running}"').replace("-lt 50", "-lt 5"))
    exe = launch.executable(tmp_path / "Switch Player.app")
    pid = _quitting_process(0.4)
    start = time.monotonic()
    subprocess.run(["/bin/sh", "-c", script, "sh", pid, exe, "-g", "/Applications/Switch Player.app"],
                   env={**os.environ, "EXE": exe}, timeout=20, check=True)
    return calls.read_text().splitlines(), time.monotonic() - start


def test_it_waits_for_the_old_copy_then_opens_a_new_instance(tmp_path):
    calls, took = _relaunch(tmp_path, starts_on_attempt=1)
    assert took >= 0.3
    assert calls == ["-n -g /Applications/Switch Player.app"]


def test_it_opens_again_if_the_app_didnt_start(tmp_path):
    calls, _ = _relaunch(tmp_path, starts_on_attempt=2)
    assert len(calls) == 2 and all(c.startswith("-n ") for c in calls)


def test_it_gives_up_after_two_tries(tmp_path):
    calls, _ = _relaunch(tmp_path, starts_on_attempt=0)
    assert len(calls) == 2


def test_the_check_looks_for_the_apps_own_executable(tmp_path):
    app = tmp_path / "Switch Player.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        b'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleExecutable</key>'
        b'<string>Switch Player</string></dict></plist>')
    assert launch.executable(app) == str(app / "Contents" / "MacOS" / "Switch Player")


def _bundle(tmp_path, icon="SwitchPlayer.icns"):
    app = tmp_path / "Switch Player.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    info = b'<?xml version="1.0"?><plist version="1.0"><dict>'
    if icon is not None:
        info += b"<key>CFBundleIconFile</key><string>" + icon.encode() + b"</string>"
    (app / "Contents" / "Info.plist").write_bytes(info + b"</dict></plist>")
    (app / "Contents" / "Resources" / "SwitchPlayer.icns").write_bytes(b"icns")
    return app


class FakeRun:
    def __init__(self, code=0, error=None):
        self.calls, self.code, self.error = [], code, error

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(argv, self.code)


def test_a_new_versions_first_launch_refreshes_the_icon_once(tmp_path):
    """The Dock kept 0.1.0's icon after in-place updates to 0.1.2, until the bundle was
    registered with Launch Services again."""
    app, data = _bundle(tmp_path), tmp_path / "data"
    data.mkdir()
    os.utime(app, (1, 1))
    run = FakeRun()
    assert launch.is_new_version(data, "0.1.3")  # never launched: an update from before this existed
    assert launch.refresh_icon_cache(app, data, "0.1.3", run=run)
    assert run.calls == [[launch.LSREGISTER, "-f", str(app)]]
    assert app.stat().st_mtime > 1  # marked changed for the icon cache
    assert not launch.is_new_version(data, "0.1.3")  # later launches leave it alone
    assert launch.is_new_version(data, "0.1.4")  # the next update refreshes again


def test_a_failed_refresh_is_tried_again_next_launch(tmp_path):
    app, data = _bundle(tmp_path), tmp_path / "data"
    data.mkdir()
    assert not launch.refresh_icon_cache(app, data, "0.1.3", run=FakeRun(code=1))
    assert launch.is_new_version(data, "0.1.3")
    assert not launch.refresh_icon_cache(app, data, "0.1.3", run=FakeRun(error=OSError("no lsregister")))
    assert not launch.refresh_icon_cache(app, data, "0.1.3", run=FakeRun(error=subprocess.TimeoutExpired("lsregister", 60)))
    assert launch.is_new_version(data, "0.1.3")


def test_the_icon_file_comes_from_the_bundles_info_plist(tmp_path):
    assert launch.icon_file(_bundle(tmp_path / "a")) == tmp_path / "a" / "Switch Player.app" / "Contents" / "Resources" / "SwitchPlayer.icns"
    assert launch.icon_file(_bundle(tmp_path / "b", icon="SwitchPlayer")).name == "SwitchPlayer.icns"  # the extension is optional
    assert launch.icon_file(_bundle(tmp_path / "c", icon=None)) is None
    assert launch.icon_file(_bundle(tmp_path / "d", icon="Missing.icns")) is None
    assert launch.icon_file(tmp_path / "nothing.app") is None
