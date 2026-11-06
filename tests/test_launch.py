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
