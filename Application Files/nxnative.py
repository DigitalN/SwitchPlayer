"""The nxnative copy Switch Player builds with, the Python environment its build script runs in,
and the command line of a Build.

Switch Player's own copy is a clone of upstream nxnative in its data folder. It has no local
commits, so an Engine update can always move it to upstream's latest, force pushes included.
That happens only when a Build starts. The player can point Switch Player at another checkout
instead, which Switch Player then never changes.

The build script and the shell tools it runs call a bare `python3`, so Switch Player's Python
environment goes first on PATH. A GUI app doesn't get the shell's PATH, so Homebrew's and
the .NET SDK's folders are added too. The environment's packages are NX Play's list
(nxnative-python/pyproject.toml) until nxnative lists its own (the third pull request in
docs/DESIGN.md).

Only nxnative's documented front-end contract is used (docs/frontends.md, ADR 0001): the build
script's command line. Whichever Xcode macOS has selected builds; nxnative's own error says
when it needs another.
"""

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("switchplayer")

UPSTREAM = "https://github.com/seedds/nxnative.git"
BUILD_SCRIPT = "scripts/build_ipa_from_nsp.py"
TOOL_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/local/share/dotnet"]
SYSTEM_PATHS = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
# Not passed on to the build: they'd point its Python at Switch Player's own (bundled) one.
DROPPED_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")


class NxnativeError(Exception):
    """Something a Build needs isn't there. The message is for the player."""


def _run(argv, cwd=None, env=None, timeout=600):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NxnativeError(f"{Path(argv[0]).name} {argv[1]} took longer than {timeout // 60} minutes") from None
    except OSError as e:
        raise NxnativeError(f"Couldn't run {argv[0]}: {e.strerror}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise NxnativeError(detail[-1] if detail else f"{Path(argv[0]).name} {argv[1]} failed ({result.returncode})")
    return result.stdout.strip()


def git(repo, *args, timeout=600):
    return _run(["/usr/bin/git", "-C", str(repo), *args], timeout=timeout)


class Copy:
    """An nxnative checkout: Switch Player's own clone (`own`), or the player's, left as it is."""

    def __init__(self, path, own, upstream=UPSTREAM):
        self.path = Path(path)
        self.own = own
        self._upstream = upstream

    @property
    def script(self):
        return self.path / BUILD_SCRIPT

    def ready(self):
        return self.script.is_file()

    def commit(self):
        try:
            return git(self.path, "rev-parse", "--short=12", "HEAD", timeout=30)
        except NxnativeError:
            return None

    def ensure(self):
        """Clones Switch Player's own copy if it isn't there yet."""
        if self.ready():
            return
        if not self.own:
            raise NxnativeError(f"There's no nxnative build script in {self.path}")
        # Into a folder beside it, renamed once complete, so a clone cut short isn't taken for one.
        # Without file contents until checkout: the history is what's kept for long.
        part = self.path.with_name(self.path.name + ".partial")
        shutil.rmtree(part, ignore_errors=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Cloning nxnative into %s", self.path)
        _run(["/usr/bin/git", "clone", "--quiet", "--filter=blob:none", self._upstream, str(part)], timeout=1800)
        if self.path.exists():
            shutil.rmtree(self.path)
        os.replace(part, self.path)

    def fetch(self):
        """Fetches upstream's latest without moving the copy to it (Switch Player's own copy only)."""
        if self.own and self.ready():
            git(self.path, "fetch", "--quiet", "--prune", "origin", timeout=300)

    def update(self):
        """The Engine update, as a Build starts: Switch Player's own copy moves to upstream's latest.
        Returns the commit the Build will use."""
        self.ensure()
        if self.own:
            before = self.commit()
            self.fetch()
            git(self.path, "reset", "--quiet", "--hard", self._upstream_head(), timeout=300)
            after = self.commit()
            if after != before:
                log.info("Engine update: nxnative %s → %s", before, after)
        return self.commit()

    def _upstream_head(self):
        try:
            return git(self.path, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/HEAD", timeout=30)
        except NxnativeError:
            git(self.path, "remote", "set-head", "origin", "--auto", timeout=120)
            return git(self.path, "rev-parse", "--verify", "refs/remotes/origin/HEAD", timeout=30)


def find_uv():
    for folder in TOOL_PATHS:
        uv = Path(folder) / "uv"
        if uv.is_file() and os.access(uv, os.X_OK):
            return uv
    return None


class Environment:
    """Switch Player's own Python for nxnative, with the packages its build script imports: in
    `folder`, Python and uv's cache included (not whichever Python the Mac happens to have)."""

    def __init__(self, folder, project_source):
        self.folder = Path(folder)
        self.project_source = Path(project_source)
        self.project = self.folder / "project"  # uv writes its lock here, never into the app's bundle
        self.venv = self.folder / "env"

    @property
    def python(self):
        return self.venv / "bin" / "python3"

    def ready(self):
        """Synced from this package list. The copy of the list is written only once a sync has
        worked, so one cut short isn't taken for done."""
        synced = self.folder / "synced-pyproject.toml"
        return self.python.exists() and synced.is_file() and synced.read_bytes() == self.project_source.read_bytes()

    def ensure(self):
        if self.ready():
            return
        uv = find_uv()
        if uv is None:
            raise NxnativeError("Switch Player needs uv to install nxnative's Python packages: brew install uv")
        log.info("Installing nxnative's Python packages into %s", self.venv)
        self.project.mkdir(parents=True, exist_ok=True)
        (self.folder / "synced-pyproject.toml").unlink(missing_ok=True)
        shutil.copyfile(self.project_source, self.project / "pyproject.toml")
        env = {**_base_env(), "UV_PROJECT_ENVIRONMENT": str(self.venv), "UV_PYTHON_INSTALL_DIR": str(self.folder / "python"),
               "UV_CACHE_DIR": str(self.folder / "uv-cache"), "UV_PYTHON_PREFERENCE": "only-managed", "UV_NO_PROGRESS": "1"}
        try:
            _run([str(uv), "sync", "--quiet", "--project", str(self.project)], env=env, timeout=1800)
        except NxnativeError as e:
            raise NxnativeError(f"Couldn't install nxnative's Python packages: {e}") from None
        shutil.copyfile(self.project_source, self.folder / "synced-pyproject.toml")


def _base_env():
    env = {k: v for k, v in os.environ.items()
           if k not in DROPPED_ENV and not k.startswith(("PYTHON", "DYLD_", "_PYI", "_MEIPASS"))}
    env["PATH"] = os.pathsep.join(TOOL_PATHS + SYSTEM_PATHS)
    return env


def build_command(copy, environment, game_path, keys_file=None, options=""):
    """(argv, working folder, environment) for one Build of the Game folder or .nup at
    `game_path`. Keys aren't needed for a .nup."""
    try:
        extra = shlex.split(options or "")
    except ValueError as e:
        raise NxnativeError(f"The Build Options can't be read ({e})") from None
    argv = [str(environment.python), str(copy.script), str(game_path), "--platform", "mac-app", "--progress", "json"]
    if keys_file and Path(game_path).suffix.lower() != ".nup":
        argv += ["-k", str(keys_file)]
    env = _base_env()
    env["PATH"] = os.pathsep.join([str(environment.venv / "bin")] + TOOL_PATHS + SYSTEM_PATHS)
    env["PYTHONUNBUFFERED"] = "1"  # its events arrive as they happen
    return argv + extra, copy.path, env
