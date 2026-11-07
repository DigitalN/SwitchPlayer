import subprocess

import pytest

import nxnative
import prerequisites


class System:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.calls = []
        self.envs = []
        self.extra = {}
        brew = tmp_path / "homebrew" / "brew"
        brew.parent.mkdir()
        brew.touch()
        brew.chmod(0o755)
        monkeypatch.setattr(prerequisites, "_brew", lambda: str(brew))

    def run(self, argv, timeout=60, env=None):
        self.calls.append(argv)
        self.envs.append(env)
        command = tuple(argv[:2])
        outputs = {
            ("/usr/bin/uname", "-m"): "arm64\n",
            ("/usr/bin/sw_vers", "-productVersion"): "26.6\n",
            ("/usr/bin/xcode-select", "-p"): "/Applications/Xcode.app/Contents/Developer\n",
            ("/usr/bin/xcodebuild", "-checkFirstLaunchStatus"): "",
            ("/usr/bin/xcrun", "metal"): "Apple metal version 4.0\n",
            ("/usr/local/share/dotnet/dotnet", "--list-sdks"): "10.0.100 [/usr/local/share/dotnet/sdk]\n",
        }
        outputs.update(self.extra)
        if command in outputs:
            return subprocess.CompletedProcess(argv, 0, outputs[command], "")
        if argv[0].endswith("/brew") and argv[1] == "bundle":
            return subprocess.CompletedProcess(argv, 0, "The Brewfile's dependencies are satisfied\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "missing\n")


def test_ready_mac_reports_all_prerequisites(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    result = prerequisites.check(run=system.run)

    assert result["ready"] is True
    assert all(item["ready"] for item in result["items"])
    assert {item["id"] for item in result["items"]} == {"mac", "xcode", "metal", "dotnet", "tools"}


def test_the_check_never_mentions_switch_players_own_python_environment(tmp_path, monkeypatch):
    """DESIGN.md: a Build prepares uv's environment itself, so the check leaves uv out; the
    Brewfile still installs it, which a Build needs."""
    system = System(tmp_path, monkeypatch)

    def missing(argv, **kwargs):
        if argv[0].endswith("/brew") and argv[1:3] == ["bundle", "check"]:
            return subprocess.CompletedProcess(argv, 1, "", "dependencies missing\n")
        return system.run(argv, **kwargs)

    for run in (system.run, missing):
        for item in prerequisites.check(run=run)["items"]:
            assert "uv" not in item["detail"].split() and "uv," not in item["detail"]
    assert 'brew "uv"' in (prerequisites.Path(prerequisites.__file__).parent / "Brewfile").read_text()


def test_macos_must_meet_the_nxnative_minimum(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    def old_mac(argv, **kwargs):
        if argv[:2] == ["/usr/bin/sw_vers", "-productVersion"]:
            return subprocess.CompletedProcess(argv, 0, "26.5\n", "")
        return system.run(argv, **kwargs)

    result = prerequisites.check(run=old_mac)

    mac = next(item for item in result["items"] if item["id"] == "mac")
    assert mac["ready"] is False
    assert "26.6" in mac["detail"]


def test_missing_tools_offer_one_homebrew_install_action(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    def missing(argv, **kwargs):
        if argv[0].endswith("/brew") and argv[1:3] == ["bundle", "check"]:
            return subprocess.CompletedProcess(argv, 1, "", "dependencies missing\n")
        return system.run(argv, **kwargs)

    result = prerequisites.check(run=missing)

    assert result["ready"] is False
    tools = next(item for item in result["items"] if item["id"] == "tools")
    assert tools["action"] == "install"
    assert "Homebrew" in tools["detail"]


def test_install_runs_homebrew_only_when_requested(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    prerequisites.install_tools(run=system.run)

    assert system.calls == [[str(tmp_path / "homebrew" / "brew"), "bundle", "--file",
                             str(prerequisites.Path(prerequisites.__file__).parent / "Brewfile"), "--no-upgrade"]]
    assert system.envs == [{"HOMEBREW_BUNDLE_CASK_SKIP": "dotnet-sdk"}]  # its pkg needs sudo, which has no terminal here


def test_the_dotnet_sdk_is_a_manual_line_with_the_command_to_run(tmp_path, monkeypatch):
    system = System(tmp_path, monkeypatch)

    def no_sdk(argv, **kwargs):
        if argv[1:] == ["--list-sdks"]:
            return subprocess.CompletedProcess(argv, 0, "", "")  # a runtime alone lists no SDK
        return system.run(argv, **kwargs)

    result = prerequisites.check(run=no_sdk)
    items = {item["id"]: item for item in result["items"]}

    assert result["ready"] is False
    assert items["dotnet"]["ready"] is False and items["dotnet"]["action"] is None
    assert "brew install --cask dotnet-sdk" in items["dotnet"]["detail"] and "password" in items["dotnet"]["detail"]
    assert items["tools"]["ready"] is True  # the button's tools don't wait for it
    assert system.envs[system.calls.index(next(c for c in system.calls if c[1:3] == ["bundle", "check"]))] \
        == {"HOMEBREW_BUNDLE_CASK_SKIP": "dotnet-sdk"}

    found = {item["id"]: item for item in prerequisites.check(run=system.run)["items"]}
    assert found["dotnet"]["ready"] is True and "10.0.100" in found["dotnet"]["detail"]


def test_a_dotnet_sdk_from_the_homebrew_formula_counts(tmp_path, monkeypatch):
    """A Build's PATH has /opt/homebrew/bin, where the `dotnet` formula puts it, as well as the
    folder Microsoft's installer (the cask) uses."""
    system = System(tmp_path, monkeypatch)
    system.extra = {("/opt/homebrew/bin/dotnet", "--list-sdks"): "9.0.300 [/opt/homebrew/opt/dotnet/libexec/sdk]\n"}

    def formula_only(argv, **kwargs):
        if argv == ["/usr/local/share/dotnet/dotnet", "--list-sdks"]:
            return subprocess.CompletedProcess(argv, 1, "", "No such file")
        return system.run(argv, **kwargs)

    result = prerequisites.check(run=formula_only)
    sdk = next(item for item in result["items"] if item["id"] == "dotnet")

    assert sdk["ready"] is True and "9.0.300" in sdk["detail"]
    assert result["ready"] is True


def test_install_allows_time_for_homebrew_and_reports_failures(tmp_path, monkeypatch):
    calls = []

    def fail(argv, timeout, env=None):
        calls.append(timeout)
        return subprocess.CompletedProcess(argv, 1, "", "could not install\n")

    with pytest.raises(nxnative.NxnativeError, match="could not install"):
        prerequisites.install_tools(run=fail)

    assert calls == [1800]
