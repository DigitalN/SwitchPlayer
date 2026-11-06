import json
import sys
import types
from pathlib import Path

import settings


def test_validate_returns_complete_independent_defaults():
    first = settings.validate(None)
    second = settings.validate([])

    assert first == {
        "library_folder": None,
        "keys_file": None,
        "nxnative_checkout": None,
        "build_options": {},
    }
    assert second == first
    first["build_options"]["game"] = "--option"
    assert second["build_options"] == {}


def test_validate_expands_paths_cleans_options_and_drops_unknown_keys():
    raw = {
        "library_folder": "~/Switch Games",
        "keys_file": "~/prod.keys",
        "nxnative_checkout": "",
        "build_options": {
            "game": "  --flag 'two words'  ",
            "blank": "   ",
            "non-string": 42,
            12: "--ignored",
        },
        "unknown": "ignored",
    }

    assert settings.validate(raw) == {
        "library_folder": str(Path("~/Switch Games").expanduser()),
        "keys_file": str(Path("~/prod.keys").expanduser()),
        "nxnative_checkout": None,
        "build_options": {"game": "--flag 'two words'"},
    }


def test_nxplay_preferences_include_only_existing_paths(monkeypatch, tmp_path):
    library = tmp_path / "Switch Games"
    library.mkdir()
    keys = tmp_path / "prod.keys"
    keys.touch()
    missing = tmp_path / "missing.keys"
    values = {"gamesRoot": str(library), "keysPath": str(keys)}
    foundation = types.SimpleNamespace(
        CFPreferencesCopyAppValue=lambda key, domain: values.get(key) if domain == settings.NXPLAY_DOMAIN else None
    )
    monkeypatch.setitem(sys.modules, "Foundation", foundation)

    assert settings.nxplay_preferences() == {
        "library_folder": str(library),
        "keys_file": str(keys),
    }

    values["keysPath"] = str(missing)
    values["gamesRoot"] = None
    assert settings.nxplay_preferences() == {}


def test_first_launch_imports_nxplay_and_saves_atomically(tmp_path):
    path = tmp_path / "data" / "settings.json"
    library = tmp_path / "Switch Games"
    library.mkdir()

    saved = settings.Settings(path, first_launch=lambda: {"library_folder": str(library)})

    assert saved.get()["library_folder"] == str(library)
    assert json.loads(path.read_text()) == saved.get()
    assert not path.with_name(path.name + ".partial").exists()


def test_existing_settings_do_not_run_first_launch(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"library_folder": "~/Games", "build_options": {"game": " --fast "}}))

    def unexpected():
        raise AssertionError("first launch ran for an existing settings file")

    saved = settings.Settings(path, first_launch=unexpected)

    assert saved.get()["library_folder"] == str(Path("~/Games").expanduser())
    assert saved.get()["build_options"] == {"game": "--fast"}


def test_unreadable_settings_start_from_defaults_without_importing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")

    def unexpected():
        raise AssertionError("first launch ran for an unreadable settings file")

    saved = settings.Settings(path, first_launch=unexpected)

    assert saved.get() == settings.validate(None)


def test_update_validates_and_persists(tmp_path):
    path = tmp_path / "settings.json"
    saved = settings.Settings(path, first_launch=dict)

    result = saved.update({
        "library_folder": "~/New Library",
        "build_options": {"game": " --fast ", "empty": " ", "invalid": 1},
        "unknown": True,
    })

    assert result["library_folder"] == str(Path("~/New Library").expanduser())
    assert result["build_options"] == {"game": "--fast"}
    assert "unknown" not in result
    assert settings.Settings(path).get() == result


def test_build_options_are_stripped_removed_and_persisted(tmp_path):
    path = tmp_path / "settings.json"
    saved = settings.Settings(path, first_launch=dict)

    saved.set_build_options("game", "  --flag 'two words'  ")
    assert saved.get()["build_options"] == {"game": "--flag 'two words'"}
    assert settings.Settings(path).get()["build_options"] == {"game": "--flag 'two words'"}

    saved.set_build_options("game", "   ")
    assert saved.get()["build_options"] == {}
    assert settings.Settings(path).get()["build_options"] == {}


def test_get_returns_a_defensive_copy(tmp_path):
    saved = settings.Settings(tmp_path / "settings.json", first_launch=dict)
    saved.set_build_options("game", "--fast")

    values = saved.get()
    values["library_folder"] = "changed"
    values["build_options"]["game"] = "changed"

    assert saved.get()["library_folder"] is None
    assert saved.get()["build_options"] == {"game": "--fast"}
