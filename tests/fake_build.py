"""Stands in for nxnative's build script in the tests: the same events, exit codes and
reaction to SIGINT, driven by the Game folder it's given.

    fake_build.py <Game folder> --platform mac-app --progress json [-k keys] [options]

The Game folder's `plan.json` says what to do: {"stages": [[name, seconds], …], "exit": 0,
"red": "…", "usage": true, "stderr": "…"}. A finished Build writes `<name>.app` with an
Info.plist into the folder, as the real script does.
"""

import json
import plistlib
import signal
import sys
import time
from pathlib import Path


def emit(**event):
    print(json.dumps({**event, "t": round(time.time(), 3)}), flush=True)


def main():
    folder = Path(sys.argv[1])
    plan = json.loads((folder / "plan.json").read_text())
    (folder / "argv.json").write_text(json.dumps(sys.argv[1:]))
    if plan.get("usage"):
        print("usage: build_ipa_from_nsp.py [-h] …\nbuild_ipa_from_nsp.py: error: unrecognized arguments: --bogus", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    stages = plan.get("stages", [["extract base", 0.05]])
    for k, (name, secs) in enumerate(stages, 1):
        emit(ev="stage", k=k, n=len(stages), name=name, step="")
        steps = 4
        for i in range(steps):
            time.sleep(secs / steps)
            emit(ev="progress", k=k, name=name, step="working", done=i + 1, total=steps, unit="", est=False, note="",
                 frac=min(0.99, (i + 1) / steps), eta=secs * (steps - i - 1) / steps, elapsed=secs * (i + 1) / steps)
        emit(ev="stage_end", k=k, name=name, ok=True, summary="", elapsed=secs)
    if plan.get("stderr"):
        print(plan["stderr"], file=sys.stderr, flush=True)
    if plan.get("red"):
        emit(ev="say", msg=f"[RED] {plan['red']}")
    code = plan.get("exit", 0)
    if code == 0:
        contents = folder / f"{plan.get('name', 'Fake Game')}.app" / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps(
            {"DMTitleID": plan.get("title_id", "0100D9F01D474000"), "CFBundleDisplayName": plan.get("name", "Fake Game"),
             "DMCommit": "0123456789ab"}))
        emit(ev="say", msg="done")
    return code


if __name__ == "__main__":
    sys.exit(main())
