"""Builds: one at a time, in the order the player pressed Build, each followed through the
build script's `--progress json` events and exit code (nxnative's docs/frontends.md).

A Build starts in a session of its own, so Stop can send SIGINT to its whole process group:
the script then exits with 130. Its steps write `.partial` folders, renamed when done, and the
Game app is written as `<app>.partial`, so the last Game app stays playable and the next Build
reuses the steps that finished.

Before the build script, `prepare(job)` gets the Build ready (the Engine update, the Python
environment). What its tools print goes into the Build log through job.say(), and each tool
it starts is given to job.track(), so Stop interrupts preparation as it does the script.

Each Build's own log (preparation's output, the events for people, and stderr) is kept in the
log folder for the Build Log pane; the previous one beside it. The tools' full output is in the
build folder's logs/.
"""

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from nxnative import NxnativeError

log = logging.getLogger("switchplayer")

STOP_GRACE = 20  # seconds a Build gets to stop by itself at Quit before it's ended
COUNTS = ("frac", "eta", "done", "total", "unit")  # what a stage's progress events count


@dataclass
class Job:
    game_id: str
    path: Path
    name: str
    state: str = "waiting"  # waiting, building, stopped, failed; done ones are dropped
    progress: dict | None = None
    error: str | None = None
    log_path: Path | None = None
    process: subprocess.Popen | None = field(default=None, repr=False)
    stop_requested: bool = False
    red: str | None = None  # the last [RED] message
    stderr_tail: list = field(default_factory=list)
    say: object = field(default=lambda text: None, repr=False)  # a line for the Build log, while preparing
    track: object = field(default=lambda process: None, repr=False)  # a tool preparation started


def safe_name(name):
    return re.sub(r"[^\w .-]+", "_", name).strip() or "game"


def log_path(log_dir, game_id):
    """Where a game's Build log is kept, whether or not it has one yet."""
    return Path(log_dir) / f"{safe_name(game_id)}.log"


class Builder:
    """`prepare(job)` runs on the Build's thread before each Build (the Engine update, the
    Python environment) and returns (argv, working folder, environment), or raises
    NxnativeError with a message for the player. `on_finish(job, ok)` follows each Build,
    and `on_busy(busy)` each change between nothing to do and something running or waiting."""

    def __init__(self, prepare, log_dir, on_finish=None, on_busy=None):
        self._prepare = prepare
        self._log_dir = Path(log_dir)
        self._on_finish = on_finish or (lambda job, ok: None)
        self._on_busy = on_busy or (lambda busy: None)
        self._jobs = {}  # game id → Job, for the ones with something to show
        self._queue = []  # game ids waiting, in order
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._busy = False
        self._quitting = False
        self._thread = threading.Thread(target=self._run, name="builds", daemon=True)
        self._thread.start()

    # ── what the page and the app ask ───────────────────────
    def status(self, game_id):
        with self._lock:
            job = self._jobs.get(game_id)
            if not job:
                return None
            progress = dict(job.progress) if job.progress else None
            if progress and "since" in progress:  # preparing: how long this step has taken
                progress["elapsed"] = max(0, int(time.monotonic() - progress.pop("since")))
            return {"state": job.state, "progress": progress,
                    "error": job.error, "log": str(job.log_path) if job.log_path else None}

    def build_log(self, game_id):
        """The game's last Build log, kept from an earlier launch too."""
        path = self._log_path(game_id)
        return path if path.is_file() else None

    @property
    def busy(self):
        with self._lock:
            return any(j.state in ("waiting", "building") for j in self._jobs.values())

    def build(self, game_id, path, name):
        with self._lock:
            job = self._jobs.get(game_id)
            if self._quitting or (job and job.state in ("waiting", "building")):
                return
            self._jobs[game_id] = Job(game_id=game_id, path=Path(path), name=name)
            self._queue.append(game_id)
            log.info("Build of %s %s", name, "waiting" if len(self._queue) > 1 or self._running() else "starting")
            self._busy_changed()
            self._wake.notify_all()

    def stop(self, game_id):
        with self._lock:
            job = self._jobs.get(game_id)
            if not job:
                return
            if job.state == "waiting":
                self._queue.remove(game_id)
                del self._jobs[game_id]  # back to what it was before
                log.info("Took %s out of the queue", job.name)
                self._busy_changed()
            elif job.state == "building" and not job.stop_requested:
                job.stop_requested = True
                log.info("Stopping the Build of %s", job.name)
                self._interrupt(job)

    def stop_all(self, grace=STOP_GRACE):
        """At Quit: empties the queue and stops the running Build, waiting for it to end."""
        with self._lock:
            self._quitting = True
            for gid in self._queue:
                self._jobs.pop(gid, None)
            self._queue.clear()
            running = [j for j in self._jobs.values() if j.state == "building"]
            for job in running:
                job.stop_requested = True
                self._interrupt(job)
            self._wake.notify_all()
        for job in running:
            if job.process:
                try:
                    job.process.wait(grace)
                except subprocess.TimeoutExpired:
                    log.warning("The Build of %s didn't stop in %d s; ending it", job.name, grace)
                    self._signal(job, signal.SIGKILL)
                    job.process.wait(5)
        self._thread.join(grace)

    # ── the Build's thread ──────────────────────────────────
    def _running(self):
        return any(j.state == "building" for j in self._jobs.values())

    def _busy_changed(self):
        busy = bool(self._queue) or self._running()
        if busy != self._busy:
            self._busy = busy
            # Off this lock, with the value as it is then: two changes close together can't
            # arrive in the wrong order.
            threading.Thread(target=lambda: self._on_busy(self.busy), daemon=True).start()

    def _interrupt(self, job):
        if job.process and job.process.poll() is None:
            self._signal(job, signal.SIGINT)

    def _track(self, job, process):
        """A tool preparation started: Stop interrupts it, and one started after Stop at once."""
        with self._lock:
            job.process = process
            if job.stop_requested:
                self._interrupt(job)

    @staticmethod
    def _signal(job, sig):
        try:
            os.killpg(job.process.pid, sig)  # its own session: the script and the tools it runs
        except (ProcessLookupError, PermissionError):
            pass

    def _run(self):
        while True:
            with self._lock:
                while not self._queue and not self._quitting:
                    self._wake.wait()
                if self._quitting:
                    return
                job = self._jobs[self._queue.pop(0)]
                job.state = "building"
            ok = False
            try:
                ok = self._build(job)
            except Exception:
                log.exception("The Build of %s failed unexpectedly", job.name)
                self._finish(job, "failed", "Switch Player couldn't follow this Build; its log has more")
            try:
                self._on_finish(job, ok)
            except Exception:
                log.exception("After the Build of %s", job.name)
            with self._lock:
                self._busy_changed()

    def _log_path(self, game_id):
        return log_path(self._log_dir, game_id)

    def _build(self, job):
        self._log_dir.mkdir(parents=True, exist_ok=True)
        job.log_path = self._log_path(job.game_id)
        if job.log_path.exists():
            os.replace(job.log_path, job.log_path.with_suffix(".prev.log"))
        with open(job.log_path, "w", encoding="utf-8") as out:
            write = threading.Lock()

            def say(text):
                with write:
                    out.write(text + "\n")
                    out.flush()

            say(f"Build of {job.name} ({job.path}), {time.strftime('%Y-%m-%d %H:%M:%S')}")
            job.say, job.track = say, lambda process: self._track(job, process)
            try:
                command = self._prepare(job)
            except NxnativeError as e:
                if job.stop_requested:  # what Stop interrupted fails, as it should
                    say("\nStopped")
                    return self._finish(job, "stopped")
                say(f"\n{e}")
                return self._finish(job, "failed", str(e))
            finally:
                job.say, job.track = Job.say, Job.track
                with self._lock:
                    job.process = None
            if job.stop_requested:
                say("\nStopped")
                return self._finish(job, "stopped")
            argv, cwd, env = command
            say(f"\n$ {' '.join(argv)}\n  in {cwd}\n")
            try:
                process = subprocess.Popen(
                    argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True, text=True, encoding="utf-8", errors="replace", bufsize=1)
            except OSError as e:
                out.write(f"Couldn't start it: {e}\n")
                return self._finish(job, "failed", f"Couldn't start nxnative's build script: {e.strerror}")
            with self._lock:
                job.process = process
                if job.stop_requested:  # Stop came while it started
                    self._interrupt(job)
            errors = threading.Thread(target=self._read_stderr, args=(job, process, out, write), daemon=True)
            errors.start()
            for line in process.stdout:
                self._event(job, line, out, write)
            code = process.wait()
            errors.join(10)
            with write:
                out.write(f"\nExit code {code}. Each tool's full output is in {job.path.parent if job.path.suffix.lower() == '.nup' else job.path}"
                          f"/<title ID>_build/logs/.\n")
        return self._result(job, code)

    def _read_stderr(self, job, process, out, write):
        for line in process.stderr:
            with write:
                out.write(line)
                out.flush()
            if line.strip():
                job.stderr_tail = (job.stderr_tail + [line.rstrip()])[-20:]

    def _event(self, job, line, out, write):
        try:
            e = json.loads(line)
        except ValueError:
            e = None
        if not isinstance(e, dict) or "ev" not in e:
            with write:
                out.write(line)
                out.flush()
            return
        ev = e["ev"]
        text = None
        with self._lock:
            p = job.progress
            if ev == "stage":
                job.progress = {"k": e.get("k"), "n": e.get("n"), "name": e.get("name") or "", "step": e.get("step") or "",
                                **dict.fromkeys(COUNTS)}
                text = f"── {e.get('k')}/{e.get('n')} {e.get('name')}" + (f": {e['step']}" if e.get("step") else "")
            elif ev in ("step", "progress") and p is not None:
                for key in ("k", "name", "step"):
                    if e.get(key) is not None:
                        p[key] = e[key]
                if ev == "step":
                    p.update(dict.fromkeys(COUNTS))
                    text = f"   {e.get('step')}"
                else:
                    for key in COUNTS:  # frac 0-0.99 or null, eta in seconds, done of total in its unit
                        value = e.get(key)
                        p[key] = value if (isinstance(value, str) if key == "unit" else
                                           isinstance(value, (int, float)) and not isinstance(value, bool)) else None
            elif ev == "stage_end":
                text = f"   {'done' if e.get('ok', True) else 'failed'}" + (f": {e['summary']}" if e.get("summary") else "") \
                    + (f" ({e['elapsed']:.0f} s)" if isinstance(e.get("elapsed"), (int, float)) else "")
            elif ev == "say":
                msg = str(e.get("msg", ""))
                if msg.startswith("[RED] "):
                    job.red = msg[len("[RED] "):]
                text = msg
            elif ev == "shaders_stale":
                text = f"   shaders to rebuild ({e.get('reason')}): {e.get('count')}"
        if text is not None:
            with write:
                out.write(text + "\n")
                out.flush()

    def _result(self, job, code):
        if code == 0:
            return self._finish(job, None)
        if code == 130 or job.stop_requested:
            return self._finish(job, "stopped")
        if code == 2:  # its arguments: usage on stderr, no events
            why = job.stderr_tail[-1] if job.stderr_tail else "it didn't accept the command line"
            return self._finish(job, "failed", f"nxnative's build script didn't accept the Build Options: {why}")
        if code == 1:
            why = job.red or (job.stderr_tail[-1] if job.stderr_tail else None)
            return self._finish(job, "failed", why or "nxnative's build stopped on an error")
        return self._finish(job, "failed", f"nxnative's build script ended unexpectedly (exit code {code})")

    def _finish(self, job, state, error=None):
        with self._lock:
            job.process = None
            job.progress = None
            if state is None:
                del self._jobs[job.game_id]  # done: the Game app speaks for itself
            else:
                job.state, job.error = state, error
        log.info("Build of %s %s%s", job.name, {None: "finished", "stopped": "stopped", "failed": "failed"}[state],
                 f": {error}" if error else "")
        return state is None
