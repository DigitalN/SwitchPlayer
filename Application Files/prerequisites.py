"""Read-only checks and explicit installation of the tools nxnative Builds need."""

import os
import subprocess
from pathlib import Path

from nxnative import NxnativeError


def _run(argv, timeout=60):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 1, "", str(e))


def _brew():
    for folder in ("/opt/homebrew/bin", "/usr/local/bin"):
        path = Path(folder) / "brew"
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def check(run=_run):
    """What a Build needs, with manual Xcode instructions separate from the install action."""
    items = []
    mac = run(["/usr/bin/uname", "-m"])
    version = run(["/usr/bin/sw_vers", "-productVersion"])
    release = version.stdout.strip()
    try:
        mac_version = tuple(int(part) for part in release.split("."))
    except ValueError:
        mac_version = ()
    mac_ready = (mac.returncode == 0 and mac.stdout.strip() == "arm64"
                 and version.returncode == 0 and mac_version >= (26, 6))
    items.append({"id": "mac", "name": "Apple silicon and macOS", "ready": mac_ready,
                  "detail": release if mac_ready else "Switch Player needs an Apple silicon Mac with macOS 26.6 or later.",
                  "action": None})

    developer = run(["/usr/bin/xcode-select", "-p"])
    first_launch = run(["/usr/bin/xcodebuild", "-checkFirstLaunchStatus"])
    xcode_ready = (developer.returncode == 0 and developer.stdout.strip().endswith(".app/Contents/Developer")
                   and first_launch.returncode == 0)
    items.append({"id": "xcode", "name": "Xcode", "ready": xcode_ready,
                  "detail": "Xcode is selected and its first launch is complete." if xcode_ready else
                  "Install Xcode, select it, then finish its first launch and license setup.",
                  "action": None})

    metal = run(["/usr/bin/xcrun", "metal", "--version"])
    metal_ready = metal.returncode == 0
    items.append({"id": "metal", "name": "Metal Toolchain", "ready": metal_ready,
                  "detail": metal.stdout.strip().splitlines()[0] if metal_ready else
                  "In Terminal, run: xcodebuild -downloadComponent MetalToolchain",
                  "action": None})

    brew = _brew()
    tools = run([brew, "bundle", "check", "--file", str(Path(__file__).parent / "Brewfile"), "--no-upgrade"]) if brew else None
    tools_ready = tools is not None and tools.returncode == 0
    items.append({"id": "tools", "name": "Build tools", "ready": tools_ready,
                  "detail": "uv, xcodegen, zstd, glslang and the .NET SDK are installed." if tools_ready else
                  "Homebrew tools are missing." if brew else "Install Homebrew from https://brew.sh first.",
                  "action": "install" if brew and not tools_ready else None})

    return {"ready": all(item["ready"] for item in items), "items": items}


def install_tools(run=_run):
    """Only after the player asks: install the Brewfile's tools with the installed Homebrew."""
    brew = _brew()
    if not brew:
        raise NxnativeError("Install Homebrew from https://brew.sh first.")
    result = run([brew, "bundle", "--file", str(Path(__file__).parent / "Brewfile"), "--no-upgrade"], timeout=1800)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise NxnativeError(detail[-1] if detail else "Homebrew couldn't install the Build tools.")
