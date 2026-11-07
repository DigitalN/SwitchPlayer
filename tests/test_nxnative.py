import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import nxnative


def git(repo, *args):
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def short(repo, revision="HEAD"):
    return git(repo, "rev-parse", "--short=12", revision)


class Upstream:
    def __init__(self, tmp_path):
        self.bare = tmp_path / "upstream.git"
        self.work = tmp_path / "upstream-work"
        subprocess.run(
            ["/usr/bin/git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.bare)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["/usr/bin/git", "init", "--quiet", "--initial-branch=main", str(self.work)],
            check=True,
            capture_output=True,
        )
        git(self.work, "config", "user.name", "Switch Player Tests")
        git(self.work, "config", "user.email", "tests@example.com")
        git(self.work, "remote", "add", "origin", str(self.bare))
        self.commit("print('first')\n")
        self.push()

    def commit(self, contents):
        script = self.work / nxnative.BUILD_SCRIPT
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(contents)
        git(self.work, "add", nxnative.BUILD_SCRIPT)
        git(self.work, "commit", "--quiet", "-m", "Update nxnative")
        return git(self.work, "rev-parse", "HEAD")

    def push(self, force=False):
        args = ["push", "--quiet"]
        if force:
            args.append("--force")
        git(self.work, *args, "origin", "main")

    def reset(self, revision):
        git(self.work, "reset", "--quiet", "--hard", revision)


@pytest.fixture
def upstream(tmp_path):
    return Upstream(tmp_path)


def test_own_copy_clones_updates_and_follows_a_force_push(tmp_path, upstream):
    first = git(upstream.work, "rev-parse", "HEAD")
    copy = nxnative.Copy(tmp_path / "data" / "nxnative", own=True, upstream=str(upstream.bare))

    assert not copy.ready()
    assert copy.update() == short(upstream.work, first)
    assert copy.ready()
    assert not copy.path.with_name(copy.path.name + ".partial").exists()

    second = upstream.commit("print('second')\n")
    upstream.push()
    copy.fetch()
    assert copy.commit() == short(upstream.work, first)

    copy.script.write_text("a local change\n")
    assert copy.update() == short(upstream.work, second)
    assert copy.script.read_text() == "print('second')\n"

    upstream.reset(first)
    upstream.push(force=True)
    assert copy.update() == short(upstream.work, first)
    assert copy.script.read_text() == "print('first')\n"


def test_an_unreachable_upstream_builds_with_the_complete_copy_as_it_is(tmp_path, upstream):
    first = git(upstream.work, "rev-parse", "HEAD")
    copy = nxnative.Copy(tmp_path / "data" / "nxnative", own=True, upstream=str(upstream.bare))
    copy.update()
    upstream.commit("print('second')\n")
    upstream.push()
    upstream.bare.rename(tmp_path / "offline.git")  # GitHub can't be reached
    said = []

    assert copy.update(say=said.append) == short(upstream.work, first)

    assert copy.script.read_text() == "print('first')\n"
    assert len(said) == 1 and "Building with Switch Player's copy as it is" in said[0]
    assert short(upstream.work, first) in said[0]


def test_an_unreachable_upstream_still_fails_when_there_is_no_copy_yet(tmp_path, upstream):
    copy = nxnative.Copy(tmp_path / "data" / "nxnative", own=True, upstream=str(tmp_path / "nowhere.git"))
    said = []

    with pytest.raises(nxnative.NxnativeError):
        copy.update(say=said.append)

    assert not copy.ready() and said == []


def test_a_players_checkout_is_never_changed(tmp_path, upstream):
    first = short(upstream.work)
    player = tmp_path / "player"
    subprocess.run(
        ["/usr/bin/git", "clone", "--quiet", str(upstream.bare), str(player)],
        check=True,
        capture_output=True,
    )
    copy = nxnative.Copy(player, own=False, upstream=str(tmp_path / "not-used"))
    (player / nxnative.BUILD_SCRIPT).write_text("the player's change\n")
    upstream.commit("print('new upstream')\n")
    upstream.push()

    copy.fetch()
    assert copy.update() == first
    assert (player / nxnative.BUILD_SCRIPT).read_text() == "the player's change\n"


def test_a_players_checkout_must_already_contain_the_build_script(tmp_path):
    copy = nxnative.Copy(tmp_path / "missing", own=False)

    with pytest.raises(nxnative.NxnativeError, match="no nxnative build script"):
        copy.ensure()
    assert not copy.path.exists()


def test_find_uv_requires_an_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(nxnative, "TOOL_PATHS", [str(tmp_path)])
    uv = tmp_path / "uv"
    uv.touch()

    assert nxnative.find_uv() is None
    uv.chmod(0o755)
    assert nxnative.find_uv() == uv


def test_environment_syncs_only_when_its_package_list_changes(monkeypatch, tmp_path):
    source = tmp_path / "pyproject.toml"
    source.write_text("first package list\n")
    environment = nxnative.Environment(tmp_path / "environment", source)
    uv = tmp_path / "uv"
    calls = []

    def fake_run(argv, cwd=None, env=None, timeout=None, out=None, started=None):
        calls.append((argv, cwd, env, timeout))
        environment.python.parent.mkdir(parents=True, exist_ok=True)
        environment.python.touch()
        return ""

    monkeypatch.setattr(nxnative, "find_uv", lambda: uv)
    monkeypatch.setattr(nxnative, "_run", fake_run)
    monkeypatch.setenv("PYTHONPATH", "inherited")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "inherited")

    environment.ensure()

    assert environment.ready()
    assert (environment.project / "pyproject.toml").read_text() == "first package list\n"
    assert (environment.folder / "synced-pyproject.toml").read_text() == "first package list\n"
    assert len(calls) == 1
    argv, cwd, env, timeout = calls[0]
    assert argv == [str(uv), "sync", "--quiet", "--project", str(environment.project)]
    assert cwd is None and timeout == 1800
    assert env["UV_PROJECT_ENVIRONMENT"] == str(environment.venv)
    assert env["UV_PYTHON_INSTALL_DIR"] == str(environment.folder / "python")
    assert env["UV_CACHE_DIR"] == str(environment.folder / "uv-cache")
    assert env["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert env["UV_NO_PROGRESS"] == "1"
    assert "PYTHONPATH" not in env and "DYLD_INSERT_LIBRARIES" not in env

    environment.ensure()
    assert len(calls) == 1

    source.write_text("second package list\n")
    assert not environment.ready()
    environment.ensure()
    assert len(calls) == 2
    assert (environment.project / "pyproject.toml").read_text() == "second package list\n"
    assert environment.ready()


def test_environment_requires_uv(monkeypatch, tmp_path):
    source = tmp_path / "pyproject.toml"
    source.touch()
    environment = nxnative.Environment(tmp_path / "environment", source)
    monkeypatch.setattr(nxnative, "find_uv", lambda: None)

    with pytest.raises(nxnative.NxnativeError, match="brew install uv"):
        environment.ensure()


def test_a_failed_sync_has_no_completion_marker(monkeypatch, tmp_path):
    source = tmp_path / "pyproject.toml"
    source.write_text("new package list\n")
    environment = nxnative.Environment(tmp_path / "environment", source)
    environment.folder.mkdir()
    (environment.folder / "synced-pyproject.toml").write_text("old package list\n")
    monkeypatch.setattr(nxnative, "find_uv", lambda: tmp_path / "uv")

    def fail(*args, **kwargs):
        raise nxnative.NxnativeError("network unavailable")

    monkeypatch.setattr(nxnative, "_run", fail)

    with pytest.raises(nxnative.NxnativeError, match="Couldn't install.*network unavailable"):
        environment.ensure()
    assert not (environment.folder / "synced-pyproject.toml").exists()


def test_build_command_arguments_and_environment(monkeypatch, tmp_path):
    copy = nxnative.Copy(tmp_path / "nxnative", own=False)
    environment = nxnative.Environment(tmp_path / "environment", tmp_path / "pyproject.toml")
    game = tmp_path / "Game folder"
    keys = tmp_path / "prod.keys"
    inherited = (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
        "PYTHONNOUSERSITE",
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
        "DYLD_INSERT_LIBRARIES",
        "_PYI_ARCHIVE",
        "_MEIPASS2",
    )
    for name in inherited:
        monkeypatch.setenv(name, "inherited")
    monkeypatch.setenv("SWITCHPLAYER_TEST_VALUE", "preserved")

    argv, cwd, env = nxnative.build_command(
        copy,
        environment,
        game,
        keys_file=keys,
        options="--flag 'two words'",
    )

    assert argv == [
        str(environment.python),
        str(copy.script),
        str(game),
        "--platform",
        "mac-app",
        "--progress",
        "json",
        "-k",
        str(keys),
        "--flag",
        "two words",
    ]
    assert cwd == copy.path
    assert env["PATH"] == os.pathsep.join(
        [str(environment.venv / "bin")] + nxnative.TOOL_PATHS + nxnative.SYSTEM_PATHS
    )
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["SWITCHPLAYER_TEST_VALUE"] == "preserved"
    for name in inherited:
        assert name not in env


@pytest.mark.parametrize("name", ["Game.nup", "GAME.NUP"])
def test_nup_builds_do_not_pass_keys(tmp_path, name):
    copy = nxnative.Copy(tmp_path / "nxnative", own=False)
    environment = nxnative.Environment(tmp_path / "environment", tmp_path / "pyproject.toml")

    argv, _, _ = nxnative.build_command(copy, environment, tmp_path / name, keys_file=tmp_path / "prod.keys")

    assert "-k" not in argv


def test_unreadable_build_options_are_a_player_facing_error(tmp_path):
    copy = nxnative.Copy(tmp_path / "nxnative", own=False)
    environment = nxnative.Environment(tmp_path / "environment", tmp_path / "pyproject.toml")

    with pytest.raises(nxnative.NxnativeError, match="Build Options can't be read"):
        nxnative.build_command(copy, environment, tmp_path / "Game", options="'unterminated")


def test_preparation_streams_each_line_and_hands_over_its_process():
    lines, processes = [], []
    script = "import sys; print('one'); sys.stdout.write('5%\\r50%\\r100%, done.\\n'); print(); print('two')"
    output = nxnative._run([sys.executable, "-c", script], out=lines.append, started=processes.append)
    assert lines == ["one", "100%, done.", "two"]  # a line rewritten in place, once, as it ends
    assert output.endswith("two")
    assert len(processes) == 1 and processes[0].returncode == 0


def test_preparations_error_is_its_last_line():
    with pytest.raises(nxnative.NxnativeError, match="^fatal: unable to access$"):
        nxnative._run([sys.executable, "-c", "import sys; print('working'); print('fatal: unable to access'); sys.exit(128)"],
                      out=lambda line: None)


def test_preparation_that_runs_too_long_is_ended():
    started = time.monotonic()
    with pytest.raises(nxnative.NxnativeError, match="took longer than 0 minutes"):
        nxnative._run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3, out=lambda line: None)
    assert time.monotonic() - started < 5


def test_a_first_build_downloads_nxnative_and_says_so(tmp_path):
    upstream = Upstream(tmp_path)
    copy = nxnative.Copy(tmp_path / "data" / "nxnative", own=True, upstream=str(upstream.bare))
    assert copy.needs_download
    lines = []
    copy.update(out=lines.append, started=lambda process: None)
    assert not copy.needs_download and copy.ready()
    assert any("Cloning into" in line for line in lines)
    upstream.commit("print('second')\n")
    upstream.push()
    lines.clear()
    copy.update(out=lines.append, started=lambda process: None)
    assert any("HEAD is now at" in line for line in lines)  # the Engine update, in the Build log
    assert not nxnative.Copy(tmp_path / "players", own=False).needs_download  # the player's: never downloaded


def test_a_fetch_stop_interrupted_stops_the_build_but_offline_falls_back(tmp_path, monkeypatch):
    """#15 falls back to the copy as it is when upstream can't be reached; a fetch that Stop
    interrupted (SIGINT: a negative exit code) must fail instead, so the Build ends as Stopped."""
    upstream = Upstream(tmp_path)
    copy = nxnative.Copy(tmp_path / "data" / "nxnative", own=True, upstream=str(upstream.bare))
    copy.update()
    said = []

    def offline(*args, **kwargs):
        raise nxnative.NxnativeError("Could not resolve host: github.com", returncode=128)

    monkeypatch.setattr(copy, "fetch", offline)
    assert copy.update(out=lambda line: None, started=lambda process: None, say=said.append) == copy.commit()
    assert said and said[0].startswith("Couldn't reach nxnative's upstream")

    def stopped(*args, **kwargs):
        raise nxnative.NxnativeError("git fetch failed (-2)", returncode=-2)

    monkeypatch.setattr(copy, "fetch", stopped)
    with pytest.raises(nxnative.NxnativeError):
        copy.update(out=lambda line: None, started=lambda process: None, say=said.append)
    assert len(said) == 1
    assert nxnative.interrupted(nxnative.NxnativeError("x", returncode=130))
    assert not nxnative.interrupted(nxnative.NxnativeError("x")) and not nxnative.interrupted(ValueError())
