import plistlib

import library

ZELDA, RHYTHM, MARIO = "01008CF01BAAC000", "0100D9F01D474000", "0100965017338000"


def make_app(folder, file_name, tid, name=None, **extra):
    contents = folder / file_name / "Contents"
    contents.mkdir(parents=True)
    info = {"DMTitleID": tid, "CFBundleName": name, "CFBundleDisplayName": name, **extra}
    (contents / "Info.plist").write_bytes(plistlib.dumps({k: v for k, v in info.items() if v is not None}))
    return folder / file_name


def test_base_id_of_updates_and_dlc():
    assert library.base_id(MARIO) == MARIO
    assert library.base_id("0100965017338800") == MARIO  # its update
    assert library.base_id("0100965017339001") == MARIO  # its first DLC
    assert library.base_id("010096501733900A") == MARIO


def test_title_id_from_names_prefers_the_base_game():
    names = ["The Legend of Zelda Echoes of Wisdom [01008CF01BAAC800][v65536].nsp",
             "The Legend of Zelda Echoes of Wisdom [01008CF01BAAC000][v0][US].xci"]
    assert library.title_id_from_names(names) == ZELDA
    assert library.title_id_from_names(names[:1]) == ZELDA  # only its update
    assert library.title_id_from_names(["Rhythm Heaven Groove.nsp"]) is None


def test_scans_like_the_maintainers_library(tmp_path):
    zelda = tmp_path / "Echoes of Wisdom"
    zelda.mkdir()
    (zelda / "The Legend of Zelda Echoes of Wisdom [01008CF01BAAC000][v0][US].xci").touch()
    (zelda / f"{ZELDA}_build" / "run" / "save").mkdir(parents=True)
    (zelda / f"{ZELDA}_build" / "start_mac.sh").touch()  # NX Play built it with nxnative's developer path
    rhythm = tmp_path / "Rhythm Heaven"
    (rhythm / f"{RHYTHM}_build").mkdir(parents=True)
    (rhythm / "Rhythm Heaven Groove.nsp").touch()
    (tmp_path / "prod.keys").touch()
    (tmp_path / "loose [0100000000010000].nsp").touch()  # loose .nsp files are ignored
    (tmp_path / ".hidden").mkdir()

    games = {g.id: g for g in library.scan(tmp_path)}
    assert set(games) == {"Echoes of Wisdom", "Rhythm Heaven"}
    assert games["Echoes of Wisdom"].title_id == ZELDA and games["Echoes of Wisdom"].nxplay_save
    assert games["Rhythm Heaven"].title_id == RHYTHM and not games["Rhythm Heaven"].nxplay_save
    assert games["Rhythm Heaven"].app is None
    assert games["Rhythm Heaven"].build_folder == rhythm / f"{RHYTHM}_build"
    assert games["Echoes of Wisdom"].nxplay_save_path() == zelda / f"{ZELDA}_build" / "run" / "save"
    assert games["Echoes of Wisdom"].nxplay_build
    assert not games["Rhythm Heaven"].nxplay_build  # a build folder alone could be Switch Player's own


def test_the_name_is_the_games_own(tmp_path):
    folder = tmp_path / "SMP Jamboree"
    folder.mkdir()
    (folder / "Super Mario Party Jamboree.nsp").touch()
    game, = library.scan(tmp_path)
    assert game.title_id is None
    assert game.name() == "SMP Jamboree"  # nothing else is known yet
    assert game.name("Super Mario Party Jamboree") == "Super Mario Party Jamboree"  # the eShop's

    make_app(folder, "Super Mario Party Jamboree.app", MARIO, "Super Mario Party Jamboree",
             DMCommit="8c80f39a1b2c", DMChain="b-41")
    game, = library.scan(tmp_path)
    assert game.title_id == MARIO  # from its Game app, the folder having no other clue
    assert game.app.commit == "8c80f39a1b2c" and game.app.chain == "b-41"
    assert game.name("Something the eShop calls it") == "Super Mario Party Jamboree"  # the game's own


def test_a_colon_in_the_name_survives(tmp_path):
    folder = tmp_path / "zelda"
    (folder / f"{ZELDA}_build").mkdir(parents=True)
    make_app(folder, "The Legend of Zelda - Echoes of Wisdom.app", ZELDA, "The Legend of Zelda: Echoes of Wisdom")
    game, = library.scan(tmp_path)
    assert game.name() == "The Legend of Zelda: Echoes of Wisdom"


def test_ignores_apps_that_arent_this_games(tmp_path):
    folder = tmp_path / "Rhythm Heaven"
    (folder / f"{RHYTHM}_build").mkdir(parents=True)
    make_app(folder, "Other Game.app", MARIO, "Other Game")
    (folder / "Not a game.app" / "Contents").mkdir(parents=True)
    (folder / "Rhythm Heaven Groove.app.partial" / "Contents").mkdir(parents=True)  # a Build in progress
    game, = library.scan(tmp_path)
    assert game.app is None and game.name() == "Rhythm Heaven"


def test_a_nup_and_what_its_builds_leave_beside_it(tmp_path):
    (tmp_path / f"Super Mario Party Jamboree [{MARIO}].nup").touch()
    make_app(tmp_path, "Super Mario Party Jamboree.app", MARIO, "Super Mario Party Jamboree")
    (tmp_path / f"{MARIO}_build" / "run" / "save").mkdir(parents=True)
    (tmp_path / "Rhythm Heaven Groove.app.partial").mkdir()
    game, = library.scan(tmp_path)
    assert game.id == f"Super Mario Party Jamboree [{MARIO}].nup"
    assert game.app.name == "Super Mario Party Jamboree" and game.nxplay_save


def test_a_survey_folder_beside_a_nup_is_not_a_game(tmp_path):
    """For a game nxnative has no profile for, the build script surveys it into <title ID>_survey
    in the folder holding the .nup: here the Library folder."""
    (tmp_path / f"Super Mario Party Jamboree [{MARIO}].nup").touch()
    (tmp_path / f"{MARIO}_survey").mkdir()
    (tmp_path / f"{RHYTHM.lower()}_survey").mkdir()  # in either case
    (tmp_path / "Rhythm Heaven").mkdir()  # a Game folder is still a Game folder

    assert [g.id for g in library.scan(tmp_path)] == ["Rhythm Heaven", f"Super Mario Party Jamboree [{MARIO}].nup"]


def test_a_nup_names_itself_by_its_file_until_more_is_known(tmp_path):
    (tmp_path / "jamboree.nup").touch()
    game, = library.scan(tmp_path)
    assert game.name() == "jamboree"


def test_sorts_like_music_does():
    names = ["The Legend of Zelda: Echoes of Wisdom", "Super Mario Party Jamboree", "Rhythm Heaven Groove", "theme park"]
    assert sorted(names, key=library.sort_key) == [
        "The Legend of Zelda: Echoes of Wisdom", "Rhythm Heaven Groove", "Super Mario Party Jamboree", "theme park"]


def test_a_game_app_tells_its_own_bundle_id(tmp_path):
    other = make_app(tmp_path, "Other.app", ZELDA, "Other", CFBundleIdentifier="com.example.zelda")
    default = make_app(tmp_path, "Default.app", MARIO, "Default", CFBundleIdentifier=f"org.nxnative.{MARIO.lower()}")
    bare = make_app(tmp_path, "Bare.app", RHYTHM, "Bare")

    assert library.read_app(other).bundle_id == "com.example.zelda"
    assert library.read_app(default).bundle_id == f"org.nxnative.{MARIO.lower()}"
    assert library.read_app(bare).bundle_id is None
