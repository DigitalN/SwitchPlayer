import json
import sys
import threading
import time
from pathlib import Path

import pytest

import builds
from nxnative import NxnativeError

FAKE = Path(__file__).resolve().parent / "fake_build.py"


def game(tmp_path, name, **plan):
    folder = tmp_path / "Library" / name
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text(json.dumps({"name": name, **plan}))
    return folder


class Harness:
    def __init__(self, tmp_path, prepare=None):
        self.finished = []
        self.busy = []
        self.done = threading.Event()

        def default_prepare(job):
            return [sys.executable, str(FAKE), str(job.path), "--platform", "mac-app", "--progress", "json"], job.path, None

        def on_finish(job, ok):
            self.finished.append((job.game_id, ok))
            self.done.set()

        self.builder = builds.Builder(prepare or default_prepare, tmp_path / "Logs", on_finish=on_finish,
                                      on_busy=self.busy.append)

    def wait(self, count, timeout=10):
        end = time.time() + timeout
        while len(self.finished) < count and time.time() < end:
            time.sleep(0.02)
        assert len(self.finished) >= count, self.finished

    def until(self, check, timeout=10):
        end = time.time() + timeout
        while not check() and time.time() < end:
            time.sleep(0.02)
        assert check()


def test_a_finished_build(tmp_path):
    folder = game(tmp_path, "Rhythm Heaven", stages=[["extract base", 0.1], ["Mac shaders", 0.1]])
    h = Harness(tmp_path)
    h.builder.build("Rhythm Heaven", folder, "Rhythm Heaven Groove")
    h.until(lambda: (h.builder.status("Rhythm Heaven") or {}).get("progress"))
    status = h.builder.status("Rhythm Heaven")
    assert status["state"] == "building" and status["progress"]["n"] == 2
    h.wait(1)
    assert h.finished == [("Rhythm Heaven", True)]
    assert h.builder.status("Rhythm Heaven") is None  # done: its Game app speaks for itself
    assert (folder / "Rhythm Heaven.app" / "Contents" / "Info.plist").is_file()
    text = h.builder.build_log("Rhythm Heaven").read_text()
    assert "── 2/2 Mac shaders" in text and "Exit code 0" in text
    h.until(lambda: h.busy and h.busy[-1] is False)
    assert True in h.busy


def test_a_failed_build_shows_nxnatives_red_message(tmp_path):
    folder = game(tmp_path, "Echoes", exit=1, red="captured shaders the profile's shader table does not list: 3",
                  stderr="Traceback (most recent call last): …")
    h = Harness(tmp_path)
    h.builder.build("Echoes", folder, "Echoes")
    h.wait(1)
    status = h.builder.status("Echoes")
    assert status["state"] == "failed"
    assert status["error"] == "captured shaders the profile's shader table does not list: 3"
    assert "Traceback" in h.builder.build_log("Echoes").read_text()


def test_a_failure_without_a_red_message_shows_stderr(tmp_path):
    folder = game(tmp_path, "Echoes", exit=1, stderr="ValueError: something unexpected")
    h = Harness(tmp_path)
    h.builder.build("Echoes", folder, "Echoes")
    h.wait(1)
    assert h.builder.status("Echoes")["error"] == "ValueError: something unexpected"


def test_build_options_it_doesnt_accept(tmp_path):
    folder = game(tmp_path, "Echoes", usage=True)
    h = Harness(tmp_path)
    h.builder.build("Echoes", folder, "Echoes")
    h.wait(1)
    assert h.builder.status("Echoes")["error"] == (
        "nxnative's build script didn't accept the Build Options: build_ipa_from_nsp.py: error: unrecognized arguments: --bogus")


def test_one_at_a_time_and_stop(tmp_path):
    slow = game(tmp_path, "Slow", stages=[["Mac shaders", 30]])
    second = game(tmp_path, "Second")
    third = game(tmp_path, "Third")
    h = Harness(tmp_path)
    h.builder.build("Slow", slow, "Slow")
    h.builder.build("Second", second, "Second")
    h.builder.build("Third", third, "Third")
    h.builder.build("Second", second, "Second")  # pressed again: still once in the queue
    h.until(lambda: (h.builder.status("Slow") or {}).get("progress"))
    assert [h.builder.status(g)["state"] for g in ("Slow", "Second", "Third")] == ["building", "waiting", "waiting"]

    h.builder.stop("Third")  # out of the queue, back to what it was
    assert h.builder.status("Third") is None
    started = time.time()
    h.builder.stop("Slow")  # SIGINT → 130
    h.wait(1)
    assert time.time() - started < 5
    assert h.builder.status("Slow")["state"] == "stopped"
    h.wait(2)  # the next one in line ran
    assert h.finished == [("Slow", False), ("Second", True)]
    assert not (third / "argv.json").exists()


def test_stop_while_preparing(tmp_path):
    folder = game(tmp_path, "Echoes")
    release = threading.Event()

    def prepare(job):
        release.wait(5)  # the Engine update, say
        return [sys.executable, str(FAKE), str(job.path)], job.path, None

    h = Harness(tmp_path, prepare)
    h.builder.build("Echoes", folder, "Echoes")
    h.until(lambda: h.builder.status("Echoes")["state"] == "building")
    h.builder.stop("Echoes")
    release.set()
    h.wait(1)
    assert h.builder.status("Echoes")["state"] == "stopped"
    assert not (folder / "argv.json").exists()  # the script never started


def test_what_prepare_finds_missing_is_the_error(tmp_path):
    folder = game(tmp_path, "Echoes")

    def prepare(job):
        raise NxnativeError("Switch Player needs uv to install nxnative's Python packages: brew install uv")

    h = Harness(tmp_path, prepare)
    h.builder.build("Echoes", folder, "Echoes")
    h.wait(1)
    assert h.builder.status("Echoes") == {
        "state": "failed", "progress": None, "error": "Switch Player needs uv to install nxnative's Python packages: brew install uv",
        "log": str(tmp_path / "Logs" / "Echoes.log")}


def test_quit_stops_the_build_and_empties_the_queue(tmp_path):
    slow = game(tmp_path, "Slow", stages=[["Mac shaders", 30]])
    other = game(tmp_path, "Other")
    h = Harness(tmp_path)
    h.builder.build("Slow", slow, "Slow")
    h.builder.build("Other", other, "Other")
    h.until(lambda: (h.builder.status("Slow") or {}).get("progress"))
    started = time.time()
    h.builder.stop_all(grace=5)
    assert time.time() - started < 5
    assert h.builder.status("Slow")["state"] == "stopped"
    assert h.builder.status("Other") is None
    assert not (other / "argv.json").exists()
    h.builder.build("Other", other, "Other")  # nothing new starts once quitting
    assert h.builder.status("Other") is None


def test_the_previous_log_is_kept(tmp_path):
    folder = game(tmp_path, "Echoes")
    h = Harness(tmp_path)
    h.builder.build("Echoes", folder, "Echoes")
    h.wait(1)
    h.builder.build("Echoes", folder, "Echoes")
    h.wait(2)
    assert (tmp_path / "Logs" / "Echoes.prev.log").is_file()


@pytest.mark.parametrize("name, expected", [("Echoes of Wisdom", "Echoes of Wisdom"), ("a/b:c", "a_b_c"), ("…", "_")])
def test_safe_names(name, expected):
    assert builds.safe_name(name) == expected
