import subprocess

import pytest

import nxnative
import prerequisites


class System:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.calls = []
        brew = tmp_path / "homebrew" / "brew"
        brew.parent.mkdir()
        brew.touch()
        brew.chmod(0o755)
        monkeypatch.setattr(prerequisites, "_brew", lambda: str(brew))
        monkeypatch.setattr(nxnative.Environment, "ready", lambda self: True)

    def run(self, argv, timeout=60):
        self.calls.append(argv)
        command = tuple(argv[:2])
        outputs = {
            ("/usr/bin/uname", "-m"): "arm64\n",
            ("/usr/bin/sw_vers", "-productVersion"): "26.6\n",
            ("/usr/bin/xcode-select", "-p"): "/Applications/Xcode.app/Contents/Developer\n",
            ("/usr/bin/xcodebuild", "-checkFirstLaunchStatus"): "",
            ("/usr/bin/xcrun", "metal"): "Apple metal version 4.0\n",
            ("/usr/local/share/dotnet/dotnet", "--list-sdks"): "10.0.100 [/usr/local/share/dotnet/sdk]\n",
        }
        if command in outputs:
            return subprocess.CompletedProcess(argv, 0, outputs[command], "")
        if argv[0].endswith("/brew") and argv[1] == "bundle":
            return subprocess.CompletedProcess(argv, 0, "The Brewfile's dependencies are satisfied\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "missing\n")


def test_ready_mac_reports_all_prerequisites(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    result = prerequisites.check(tmp_path / "environment", run=system.run)

    assert result["ready"] is True
    assert all(item["ready"] for item in result["items"])
    assert {item["id"] for item in result["items"]} == {"mac", "xcode", "metal", "tools", "python"}


def test_macos_must_meet_the_nxnative_minimum(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    def old_mac(argv):
        if argv[:2] == ["/usr/bin/sw_vers", "-productVersion"]:
            return subprocess.CompletedProcess(argv, 0, "26.5\n", "")
        return system.run(argv)

    result = prerequisites.check(tmp_path / "environment", run=old_mac)

    mac = next(item for item in result["items"] if item["id"] == "mac")
    assert mac["ready"] is False
    assert "26.6" in mac["detail"]


def test_missing_tools_offer_one_homebrew_install_action(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    def missing(argv):
        if argv[0].endswith("/brew") and argv[1:3] == ["bundle", "check"]:
            return subprocess.CompletedProcess(argv, 1, "", "dependencies missing\n")
        return system.run(argv)

    result = prerequisites.check(tmp_path / "environment", run=missing)

    assert result["ready"] is False
    tools = next(item for item in result["items"] if item["id"] == "tools")
    assert tools["action"] == "install"
    assert "Homebrew" in tools["detail"]


def test_install_runs_homebrew_only_when_requested(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    prerequisites.install_tools(run=system.run)

    assert system.calls == [[str(tmp_path / "homebrew" / "brew"), "bundle", "--file",
                             str(prerequisites.Path(prerequisites.__file__).parent / "Brewfile"), "--no-upgrade"]]


def test_install_allows_time_for_homebrew_and_reports_failures(tmp_path, monkeypatch):
    calls = []

    def fail(argv, timeout):
        calls.append(timeout)
        return subprocess.CompletedProcess(argv, 1, "", "could not install\n")

    with pytest.raises(nxnative.NxnativeError, match="could not install"):
        prerequisites.install_tools(run=fail)

    assert calls == [1800]
