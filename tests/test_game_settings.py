import json

import pytest

import game_settings


def setting(**fields):
    return game_settings.Setting({"env": "DM_X", "section": "Game", "label": "X", "note": "", **fields})


def test_a_choice_accepts_only_its_options():
    s = setting(kind="choice", default="0", options=[["1", "Docked"], ["0", "Handheld"]])

    assert s.accepts("1") and s.accepts("0")
    assert not s.accepts("2") and not s.accepts(None) and not s.accepts(1)
    assert s.is_default("0") and not s.is_default("1")
    assert s.stored("1") == "1"


def test_a_toggle_takes_its_own_values_or_1_and_0():
    assert setting(kind="toggle", default="on", on="on", off="off").accepts("off")
    assert not setting(kind="toggle", default="on", on="on", off="off").accepts("1")
    assert setting(kind="toggle", default="1").accepts("0")


def test_a_range_checks_and_writes_numbers_as_nxnatives_window_does():
    s = setting(kind="range", default="1.0", min=0.0, max=3.0, step=0.1)

    assert s.accepts("1.5") and s.accepts("0") and s.accepts("3.00")
    for refused in ("3.1", "-0.1", " 1.5", "0x1p-1", "nan", "inf", "wide", ""):
        assert not s.accepts(refused), refused
    assert s.is_default("1.00") and not s.is_default("1.05")
    assert s.stored("1.5") == "1.50" and s.stored("0") == "0.00"
    assert not setting(kind="range", default="1.0").accepts("1.0")  # no min or max: nothing fits


def test_env_files_read_as_the_launcher_reads_them(tmp_path):
    path = tmp_path / "env.txt"
    path.write_text("# a comment\n  DM_FULLSCREEN = 1\nDM_OPMODE=0\r\n=no key\nno equals\nDM_OPMODE=1\n\tDM_LANG=ja  \n")

    assert game_settings.env_file(path) == {"DM_FULLSCREEN": " 1", "DM_OPMODE": "0", "DM_LANG": "ja"}
    assert game_settings.env_file(tmp_path / "missing.txt") == {}


def test_the_players_env_txt_comes_before_the_game_apps(tmp_path):
    app = tmp_path / "Game.app"
    (app / "Contents" / "Resources" / "dm").mkdir(parents=True)
    (app / "Contents" / "Resources" / "dm" / "env.txt").write_text("DM_OPMODE=1\nDM_LANG=fr\n")
    player = tmp_path / "env.txt"
    player.write_text("DM_OPMODE=0\n")

    assert game_settings.fixed_values(app, player) == {"DM_OPMODE": "0", "DM_LANG": "fr"}


def write_list(tmp_path, content):
    app = tmp_path / "Game.app"
    (app / "Contents" / "Resources" / "dm").mkdir(parents=True, exist_ok=True)
    path = app / "Contents" / "Resources" / "dm" / "launcher.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return app


ROW = {"env": "DM_OPMODE", "kind": "choice", "section": "Game", "label": "Console mode", "note": "", "default": "1",
       "options": [["1", "Docked"], ["0", "Handheld"]]}


@pytest.mark.parametrize("content, status", [
    ({"schema": 1, "game": "0100D9F01D474000", "settings": [ROW]}, "ok"),
    ({"settings": [ROW]}, "ok"),  # written before the schema field existed: schema 1
    ({"schema": 1, "settings": []}, "none"),
    ({"schema": 2, "settings": [ROW]}, "unsupported"),
    ({"schema": 1, "settings": [{**ROW, "kind": "colour"}]}, "unsupported"),
    ({"schema": 1, "settings": [{**ROW, "default": 1}]}, "unsupported"),
    ({"schema": 1, "settings": "Console mode"}, "unsupported"),
    ("{not json", "unsupported"),
])
def test_the_settings_list_says_whether_the_panel_can_draw_it(tmp_path, content, status):
    found, settings = game_settings.settings_list(write_list(tmp_path, content))

    assert found == status
    assert len(settings) == (1 if status == "ok" else 0)


def test_a_game_app_without_a_settings_list_has_none(tmp_path):
    assert game_settings.settings_list(tmp_path / "Game.app") == ("none", [])


def test_unknown_fields_are_passed_on_and_ignored(tmp_path):
    """Schema 1 grows by optional fields: the page gets them, nothing here depends on them."""
    app = write_list(tmp_path, {"schema": 1, "settings": [{**ROW, "future": {"any": "thing"}}]})
    status, settings = game_settings.settings_list(app)

    assert game_settings.rows(settings, {}, {})[0]["future"] == {"any": "thing"}


def test_changes_remove_defaults_keep_fixed_and_skip_what_wasnt_sent():
    settings = [game_settings.Setting(ROW),
                setting(env="DM_RUMBLE", kind="toggle", default="1"),
                setting(env="DM_FULLSCREEN", kind="choice", default="0", options=[["0", "Window"], ["1", "Fullscreen"]])]

    assert game_settings.changes(settings, {"DM_OPMODE": "0", "DM_RUMBLE": "1", "DM_FULLSCREEN": "1"},
                                 {"DM_FULLSCREEN": "0"}) == {
        "launcher.DM_OPMODE": "0",
        "launcher.DM_RUMBLE": None,
    }
    assert game_settings.changes(settings, {}, {}) == {}
