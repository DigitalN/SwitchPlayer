import plistlib
import struct

import library

ZELDA, RHYTHM, MARIO = "01008CF01BAAC000", "0100D9F01D474000", "0100965017338000"
OCARINA = "0400486024DEC000"  # a Switch 2 game
BASE, UPDATE, DLC = 0, 1, 2


def make_nup(path, packages, toc_offset=0x80):
    """A NUP's header as far as Switch Player reads it: the superblock, the TOC's header and its
    package records, (title ID, kind, version) each, then some file data that isn't read."""
    records = b"".join(struct.pack("<QIIIIBBHIQI20x", tid, 0, 1, 2, version, kind, 0, 0, 0, 0, 0)
                       for tid, kind, version in packages)
    toc = struct.pack("<4sIQQQQQ", b"TOC\x01", len(packages), 1, 0, 0x30 + len(records), 0, 0) + records
    head = struct.pack("<4sHHQQ32sQQII16s32s", b"NUP\x01", 0x80, 1, toc_offset, len(toc), bytes(32),
                       toc_offset + len(toc), 0, len(packages), 0, bytes(16), bytes(32))
    path.write_bytes(head + bytes(toc_offset - len(head)) + toc + b"game files" * 100)
    return path


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


def test_switch_2_title_ids_count_everywhere(tmp_path):
    assert library.is_title_id(OCARINA) and library.is_title_id(OCARINA.lower()) and library.is_title_id(MARIO)
    assert not library.is_title_id("0200486024DEC000") and not library.is_title_id("0400486024DEC00")
    assert not library.is_title_id(None)
    assert library.base_id("0400486024DEC800") == OCARINA
    assert library.title_id_from_names([f"Ocarina of Time [{OCARINA[:-3]}800][v131072].nup"]) == OCARINA

    folder = tmp_path / "Ocarina of Time"
    (folder / f"{OCARINA}_build").mkdir(parents=True)
    (folder / "Ocarina of Time.nup").touch()
    make_app(folder, "The Legend of Zelda - Ocarina of Time.app", OCARINA, "The Legend of Zelda: Ocarina of Time",
             DMCommit="b2d46b8a1b2c")
    game, = library.scan(tmp_path)
    assert game.title_id == OCARINA
    assert game.app and game.app.title_id == OCARINA  # a Switch 2 Game app is this game's
    assert game.name() == "The Legend of Zelda: Ocarina of Time"


def test_a_nups_title_id_comes_from_its_header(tmp_path):
    folder = tmp_path / "Ocarina of Time"
    folder.mkdir()
    make_nup(folder / "Ocarina of Time.nup", [(int(OCARINA, 16), UPDATE, 131072)])  # a standalone update, as the maintainer's is
    make_nup(tmp_path / "jamboree.nup", [(0x0100965017339001, DLC, 0), (int(MARIO, 16), BASE, 0)])
    games = {g.id: g for g in library.scan(tmp_path)}
    assert games["Ocarina of Time"].title_id == OCARINA
    assert games["Ocarina of Time"].name() == "Ocarina of Time"  # the folder's, until titledb or a Build knows better
    assert games["jamboree.nup"].title_id == MARIO  # the base game's record first


def test_a_nups_update_or_dlc_record_gives_the_base_game(tmp_path):
    assert library.nup_title_id(make_nup(tmp_path / "u.nup", [(0x0400486024DEC800, UPDATE, 131072)])) == OCARINA
    assert library.nup_title_id(make_nup(tmp_path / "d.nup", [(0x0400486024DED002, DLC, 0)])) == OCARINA
    assert library.nup_title_id(make_nup(tmp_path / "far.nup", [(int(OCARINA, 16), BASE, 0)], toc_offset=0x1000)) == OCARINA


def test_anything_else_called_nup_has_no_title_id(tmp_path):
    assert library.nup_title_id(tmp_path / "missing.nup") is None
    (tmp_path / "empty.nup").touch()
    assert library.nup_title_id(tmp_path / "empty.nup") is None
    (tmp_path / "text.nup").write_text("not a container at all, just some text " * 10)
    assert library.nup_title_id(tmp_path / "text.nup") is None
    cut = make_nup(tmp_path / "cut.nup", [(int(OCARINA, 16), BASE, 0)])
    cut.write_bytes(cut.read_bytes()[:0x90])  # copied partly: the table isn't there
    assert library.nup_title_id(cut) is None
    assert library.nup_title_id(make_nup(tmp_path / "odd.nup", [(0x0123456789ABC000, BASE, 0)])) is None
    assert library.nup_title_id(make_nup(tmp_path / "none.nup", [])) is None


def test_a_nup_is_read_again_only_once_it_changes(tmp_path, monkeypatch):
    path = make_nup(tmp_path / "game.nup", [(int(OCARINA, 16), BASE, 0)])
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: (opened.append(p), real_open(p, *a, **k))[1])
    library.nup_title_id(path)
    library.nup_title_id(path)
    assert opened.count(str(path)) <= 1
    make_nup(path, [(int(MARIO, 16), BASE, 0)])
    import os
    os.utime(path, ns=(1, 1))
    assert library.nup_title_id(path) == MARIO


def test_an_untagged_nup_finds_the_game_app_built_beside_it(tmp_path):
    """Issue #21: a .nup in the Library folder itself, with no [title ID] in its name, is matched
    to its Game app through the title ID in its own header."""
    make_nup(tmp_path / "Ocarina of Time.nup", [(int(OCARINA, 16), UPDATE, 131072)])
    make_app(tmp_path, "The Legend of Zelda - Ocarina of Time.app", OCARINA, "The Legend of Zelda: Ocarina of Time")
    make_app(tmp_path, "Super Mario Party Jamboree.app", MARIO, "Super Mario Party Jamboree")  # another .nup's
    game, = library.scan(tmp_path)
    assert game.id == "Ocarina of Time.nup" and game.title_id == OCARINA
    assert game.app and game.app.name == "The Legend of Zelda: Ocarina of Time"


def test_a_switch_2_survey_folder_is_no_game_folder(tmp_path):
    """#22's survey folder, for a Switch 2 game nxnative has no profile for yet."""
    make_nup(tmp_path / "Star Fox.nup", [(0x0400E88025B1A000, BASE, 0)])
    (tmp_path / "0400E88025B1A000_survey").mkdir()
    game, = library.scan(tmp_path)
    assert game.id == "Star Fox.nup" and game.title_id == "0400E88025B1A000"
