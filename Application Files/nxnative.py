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

One exception, ADR 0001's, until its pull request is merged, opened yet or not (the fourth in
docs/DESIGN.md): the Out of date check (Copy.freshness) reads details docs/frontends.md doesn't
document yet: the `DMCommit` and `DMChain` a Game app records, the `dm/games/<title ID>` folder
that holds each game's own files, and the shader version `DMSH_CHAIN` in
`dm/gfx/nvn/dmsh_chain.h`. A failure there can only change what the tiles show, never a Build
or Play.
"""

import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
from collections import Counter
from pathlib import Path

log = logging.getLogger("switchplayer")

UPSTREAM = "https://github.com/seedds/nxnative.git"
BUILD_SCRIPT = "scripts/build_ipa_from_nsp.py"
TOOL_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/local/share/dotnet"]
SYSTEM_PATHS = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
# Not passed on to the build: they'd point its Python at Switch Player's own (bundled) one.
DROPPED_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")


class NxnativeError(Exception):
    """Something a Build needs isn't there. The message is for the player. `returncode` is
    the failed tool's exit code, when a tool failed."""

    def __init__(self, message, returncode=None):
        super().__init__(message)
        self.returncode = returncode


def _run(argv, cwd=None, env=None, timeout=600, out=None, started=None):
    """Runs a tool and returns its output. With `out`, a Build's preparation: each line it
    prints goes to out(line) as it comes (a line it rewrites in place, as git's progress does,
    once, as it ends), and started(process) gets the process, which has a session of its own,
    so Stop can interrupt it and whatever it started."""
    if out is not None:
        return _stream(argv, cwd, env, timeout, out, started)
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NxnativeError(f"{Path(argv[0]).name} {argv[1]} took longer than {timeout // 60} minutes") from None
    except OSError as e:
        raise NxnativeError(f"Couldn't run {argv[0]}: {e.strerror}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise NxnativeError(detail[-1] if detail else f"{Path(argv[0]).name} {argv[1]} failed ({result.returncode})",
                            returncode=result.returncode)
    return result.stdout.strip()


def _stream(argv, cwd, env, timeout, out, started):
    name = f"{Path(argv[0]).name} {argv[1]}"
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        raise NxnativeError(f"Couldn't run {argv[0]}: {e.strerror}") from None
    if started:
        started(process)
    timed_out = threading.Event()

    def end():
        timed_out.set()
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    timer = threading.Timer(timeout, end)
    timer.daemon = True
    timer.start()
    lines = []
    try:
        for raw in process.stdout:
            parts = [p.rstrip() for p in raw.decode("utf-8", "replace").rstrip("\n").split("\r")]
            line = next((p for p in reversed(parts) if p.strip()), "")
            if line:
                out(line)
                lines = (lines + [line])[-5:]
        code = process.wait()
    finally:
        timer.cancel()
        process.stdout.close()
    if timed_out.is_set():
        raise NxnativeError(f"{name} took longer than {timeout // 60:.0f} minutes")
    if code != 0:
        raise NxnativeError(lines[-1] if lines else f"{name} failed ({code})", returncode=code)
    return "\n".join(lines)


def interrupted(error):
    """The tool behind an NxnativeError was ended by a signal (Stop sends SIGINT), not failing
    by itself."""
    code = getattr(error, "returncode", None)
    return code is not None and (code < 0 or code == 130)


def git(repo, *args, timeout=600, out=None, started=None):
    return _run(["/usr/bin/git", "-C", str(repo), *args], timeout=timeout, out=out, started=started)


class Copy:
    """An nxnative checkout: Switch Player's own clone (`own`), or the player's, left as it is."""

    def __init__(self, path, own, upstream=UPSTREAM):
        self.path = Path(path)
        self.own = own
        self._upstream = upstream
        self.fetch_error = None  # why freshness() couldn't fetch upstream's latest, when it couldn't

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

    @property
    def needs_download(self):
        """Switch Player's own copy isn't there yet: the next update() clones it."""
        return self.own and not self.ready()

    def ensure(self, out=None, started=None):
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
        _run(["/usr/bin/git", "clone", "--progress" if out else "--quiet", "--filter=blob:none", self._upstream, str(part)],
             timeout=1800, out=out, started=started)
        if self.path.exists():
            shutil.rmtree(self.path)
        os.replace(part, self.path)

    def fetch(self, out=None, started=None):
        """Fetches upstream's latest without moving the copy to it (Switch Player's own copy only)."""
        if self.own and self.ready():
            git(self.path, "fetch", "--progress" if out else "--quiet", "--prune", "origin", timeout=300,
                out=out, started=started)

    def update(self, out=None, started=None, say=None):
        """The Engine update, as a Build starts: Switch Player's own copy moves to upstream's latest.
        Returns the commit the Build will use. With `out` and `started`, as _run() takes them.
        When upstream can't be reached, a copy that's already complete is used as it is, and
        say(line) tells the Build's log; only having no copy yet fails (the clone needs the
        network). A fetch that Stop interrupted still fails, so the Build stops."""
        self.ensure(out, started)
        if self.own:
            before = self.commit()
            try:
                self.fetch(out, started)
            except NxnativeError as e:
                if interrupted(e):
                    raise
                log.warning("Engine update: couldn't fetch nxnative (%s); using the copy as it is, at %s", e, before)
                if say:
                    say(f"Couldn't reach nxnative's upstream ({e}). Building with Switch Player's copy as it is"
                        + (f", at {before}." if before else "."))
                return before
            git(self.path, "reset", *([] if out else ["--quiet"]), "--hard", self._upstream_head(), timeout=300,
                out=out, started=started)
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

    def _known_commit(self, commit):
        """The full hash of a commit this checkout has, or None when it hasn't (git exits 1 for
        that; a failure of git itself still raises)."""
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
            return None
        try:
            return git(self.path, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", timeout=30)
        except NxnativeError as e:
            if e.returncode == 1:
                return None
            raise

    def freshness(self, apps):
        """Fetched changes since each Game app's Build, without moving this checkout.

        Adapted from NX Play's ask_update() and no_local_commits() (the `nxplay` script in
        varyform/nx-native-wrapper; MIT, see THIRD-PARTY-NOTICES): the commit a build was made
        from, the commits since, those touching `dm/games/<title ID>`, and whether the shader
        version differs."""
        result = {app.title_id: {"out_of_date": False, "newer": 0} for app in apps if app}
        if not result:
            return result
        self.fetch_error = None
        if self.own:
            try:
                self.fetch()
            except NxnativeError as e:  # offline, or GitHub is down: compare with what's already fetched
                self.fetch_error = str(e)
                log.info("Out of date check: couldn't fetch nxnative (%s); comparing with what was fetched before", e)
        target = self._upstream_head() if self.own else "HEAD"
        for app in apps:
            if not app or not app.commit:
                continue
            # `+dirty`: nxnative's checkout had uncommitted edits, which can't be compared, so it's
            # counted from the commit under them. Only counted: nxnative's packer reuses a Game app
            # whose inputs haven't changed and keeps its old DMCommit, so a Build from the same
            # checkout couldn't always clear a yellow line (#20).
            dirty = app.commit.endswith("+dirty")
            base = self._known_commit(app.commit.removesuffix("+dirty"))
            if base is None:  # built from a commit this copy hasn't got: a missing detail for this game alone
                log.info("%s was built from nxnative %s, which this copy doesn't have", app.title_id, app.commit)
                continue
            newer = int(git(self.path, "rev-list", "--count", f"{base}..{target}", timeout=60))
            changed = bool(git(self.path, "log", "--format=%H", "-1", f"{base}..{target}", "--",
                               f"dm/games/{app.title_id}", timeout=60))
            try:
                chain = git(self.path, "show", f"{target}:dm/gfx/nvn/dmsh_chain.h", timeout=30)
            except NxnativeError:
                chain = ""
            current_chain = next((line.split('"')[1] for line in chain.splitlines()
                                  if line.startswith('#define DMSH_CHAIN "')), None)
            # A Vulkan or GL game has no DMChain, and with that detail missing only the count shows
            # (DESIGN.md, pull request 4): not even a change to the game's own files turns it yellow.
            out_of_date = app.chain is not None and not dirty and (
                changed or (current_chain is not None and current_chain != app.chain))
            result[app.title_id] = {"out_of_date": out_of_date, "newer": newer}
        return result


GAME_SUFFIXES = (".nsp", ".nsz", ".xci", ".xcz", ".nup")


def _game_file_paths(game):
    paths = [game.path] if game.path.suffix.lower() == ".nup" else list(game.path.iterdir())
    return [path for path in paths if path.suffix.lower() in GAME_SUFFIXES]


def game_files(game):
    """{file name: size} of a Game folder's base game, update and DLC files (a .nup: itself), as
    records.py keeps them at each Build. Raises OSError when the folder can't be read."""
    return {path.name: path.stat().st_size for path in _game_file_paths(game)}


def game_files_changed(game, recorded=None):
    """A Game folder gained a base game, update or DLC after its Game app was built.

    `recorded` is game_files() as of that Build's start (records.py), which still tells a file
    copied or moved in with its old modification date. Sizes are compared, not names, so a file
    renamed, tagged or carried to another disk with the whole Library isn't a new one. Without a
    record (a Game app built before Switch Player kept them), it goes by what the files' own
    modification dates say against the Game app's Info.plist, which misses such a copy."""
    if not game.app:
        return False
    try:
        if recorded is not None:
            return bool(Counter(game_files(game).values()) - Counter(recorded.values()))
        built = (game.app.path / "Contents" / "Info.plist").stat().st_mtime
        return any(path.stat().st_mtime > built for path in _game_file_paths(game))
    except OSError:
        return False


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

    def ensure(self, out=None, started=None):
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
            _run([str(uv), "sync", *([] if out else ["--quiet"]), "--project", str(self.project)], env=env,
                 timeout=1800, out=out, started=started)
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
