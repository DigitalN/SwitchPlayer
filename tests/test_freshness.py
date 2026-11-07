import plistlib
import subprocess

import pytest

import nxnative
from library import read_app
from test_nxnative import Upstream


@pytest.fixture
def upstream(tmp_path):
    return Upstream(tmp_path)


def commit_file(upstream, path, contents):
    file = upstream.work / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(contents)
    subprocess.run(["/usr/bin/git", "-C", str(upstream.work), "add", path], check=True, capture_output=True)
    subprocess.run(["/usr/bin/git", "-C", str(upstream.work), "commit", "--quiet", "-m", f"Update {path}"],
                   check=True, capture_output=True)
    return subprocess.run(["/usr/bin/git", "-C", str(upstream.work), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def game_app(tmp_path, commit, chain="b-41", title_id="0100D9F01D474000", name="Rhythm Heaven Groove"):
    folder = tmp_path / title_id
    contents = folder / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    info = {"DMTitleID": title_id, "CFBundleDisplayName": name, "DMCommit": commit}
    if chain is not None:  # nxnative leaves DMChain out for a Vulkan or GL game
        info["DMChain"] = chain
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    return read_app(folder / f"{name}.app")


def test_only_changes_to_the_game_or_shader_chain_make_it_out_of_date(tmp_path, upstream):
    built = upstream.commit("print('built')\n")
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    app = game_app(tmp_path, built[:12])

    commit_file(upstream, "dm/host/other.c", "other game\n")
    upstream.push()
    status = copy.freshness([app])

    assert status == {"0100D9F01D474000": {"out_of_date": False, "newer": 1}}

    commit_file(upstream, "dm/games/0100D9F01D474000/profile.toml", "changed\n")
    upstream.push()
    status = copy.freshness([app])

    assert status == {"0100D9F01D474000": {"out_of_date": True, "newer": 2}}


def test_a_changed_shader_chain_makes_the_game_out_of_date(tmp_path, upstream):
    built = upstream.commit("print('built')\n")
    commit_file(upstream, "dm/gfx/nvn/dmsh_chain.h", '#define DMSH_CHAIN "b-41"\n')
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    app = game_app(tmp_path, built[:12])

    commit_file(upstream, "dm/gfx/nvn/dmsh_chain.h", '#define DMSH_CHAIN "b-42"\n')
    upstream.push()

    assert copy.freshness([app])["0100D9F01D474000"]["out_of_date"] is True


def test_a_players_checkout_is_compared_without_fetching(tmp_path, upstream, monkeypatch):
    built = upstream.commit("print('built')\n")
    upstream.push()
    copy = nxnative.Copy(upstream.work, own=False)
    app = game_app(tmp_path, built[:12])
    commit_file(upstream, "dm/games/0100D9F01D474000/profile.toml", "changed\n")
    monkeypatch.setattr(copy, "fetch", lambda: (_ for _ in ()).throw(AssertionError("must not fetch")))

    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": True, "newer": 1}


def test_game_files_added_since_the_build_make_it_out_of_date(tmp_path):
    import os
    from library import Game

    app = game_app(tmp_path, "123456789abc")
    game = Game(id="Rhythm Heaven", path=app.path.parent.parent, title_id=app.title_id, app=app)
    old = 1_000_000_000
    os.utime(app.path / "Contents" / "Info.plist", (old, old))

    assert nxnative.game_files_changed(game) is False

    update = game.path / "Update [0100D9F01D474800].nsp"
    update.touch()

    assert nxnative.game_files_changed(game) is True


def test_a_dirty_build_shows_only_the_count_from_the_commit_under_the_edits(tmp_path, upstream):
    """Uncommitted edits can't be compared. Nor can a Build always clear a yellow line for them:
    after committing the edit, a second Build reuses the Game app and keeps `X+dirty` (#20), while
    X..HEAD touches the game's folder. So a +dirty Game app is never yellow."""
    built = upstream.commit("print('built')\n")
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    app = game_app(tmp_path, built[:12] + "+dirty")

    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": False, "newer": 0}

    commit_file(upstream, "dm/games/0100D9F01D474000/profile.toml", "the edit, committed\n")
    commit_file(upstream, "dm/host/other.c", "other game\n")
    upstream.push()

    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": False, "newer": 2}


def test_a_game_app_without_a_shader_version_shows_only_the_count(tmp_path, upstream):
    """A Vulkan or GL game has no DMChain: its Game app isn't Out of date because nxnative has one."""
    built = upstream.commit("print('built')\n")
    commit_file(upstream, "dm/gfx/nvn/dmsh_chain.h", '#define DMSH_CHAIN "b-41"\n')
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    app = game_app(tmp_path, built[:12], chain=None)

    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": False, "newer": 1}

    commit_file(upstream, "dm/games/0100D9F01D474000/profile.toml", "changed\n")  # the game's own files too
    upstream.push()

    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": False, "newer": 2}


def test_offline_it_compares_with_what_was_fetched_before(tmp_path, upstream):
    built = upstream.commit("print('built')\n")
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    app = game_app(tmp_path, built[:12])
    commit_file(upstream, "dm/games/0100D9F01D474000/profile.toml", "changed\n")
    upstream.push()
    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": True, "newer": 1} and copy.fetch_error is None

    upstream.bare.rename(tmp_path / "offline.git")  # GitHub can't be reached
    status = copy.freshness([app])

    assert status == {"0100D9F01D474000": {"out_of_date": True, "newer": 1}}  # as of the last fetch
    assert copy.fetch_error  # and it says so


@pytest.mark.parametrize("commit", ["feedfacefeed", "not a commit", "--output=x"])
def test_a_commit_this_copy_lacks_is_a_missing_detail_for_that_game_alone(tmp_path, upstream, commit):
    built = upstream.commit("print('built')\n")
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    known = game_app(tmp_path, built[:12])
    unknown = game_app(tmp_path, commit, title_id="0100965017338000", name="Jamboree")
    commit_file(upstream, "dm/host/other.c", "other game\n")
    upstream.push()

    status = copy.freshness([unknown, known])

    assert status == {"0100965017338000": {"out_of_date": False, "newer": 0},
                      "0100D9F01D474000": {"out_of_date": False, "newer": 1}}


def test_a_broken_checkout_is_still_an_error(tmp_path):
    (tmp_path / "not-a-repo").mkdir()
    copy = nxnative.Copy(tmp_path / "not-a-repo", own=False)

    with pytest.raises(nxnative.NxnativeError):
        copy.freshness([game_app(tmp_path, "0123456789ab")])


def recorded_game(tmp_path):
    """A built Game folder with one game file, and what a Build that started now would record."""
    from library import Game

    app = game_app(tmp_path, "123456789abc")
    game = Game(id="Rhythm Heaven", path=app.path.parent.parent, title_id=app.title_id, app=app)
    (game.path / "Rhythm Heaven [0100D9F01D474000].nsp").write_bytes(b"b" * 100)
    return game, nxnative.game_files(game)


def test_a_file_copied_in_with_its_old_date_is_a_new_file(tmp_path):
    """Finder, `cp -c`, `ditto` and `mv` keep a file's modification date, so an update dropped in
    after the Build looks older than the Game app: the record of the Build's files catches it."""
    import os

    game, recorded = recorded_game(tmp_path)
    assert nxnative.game_files_changed(game, recorded) is False

    update = game.path / "Update [0100D9F01D474800].nsp"
    update.write_bytes(b"u" * 40)
    os.utime(update, (1_000_000_000, 1_000_000_000))
    os.utime(game.app.path / "Contents" / "Info.plist", None)  # the app is newer than the file

    assert nxnative.game_files_changed(game) is False  # by dates alone, as before records, it's missed
    assert nxnative.game_files_changed(game, recorded) is True


def test_nothing_that_adds_no_file_makes_it_out_of_date(tmp_path):
    """A rename, a Finder tag or the whole Library carried to another disk change a file's change
    time or name, not what the Game folder holds. Nor does a Build that reuses its Game app, which
    leaves Info.plist as it was while the files are newer than it."""
    import os

    game, recorded = recorded_game(tmp_path)
    os.utime(game.app.path / "Contents" / "Info.plist", (1_000_000_000, 1_000_000_000))
    base, = [p for p in game.path.iterdir() if p.suffix == ".nsp"]

    base.rename(game.path / "Rhythm Heaven Groove [base].nsp")
    base.parent.joinpath("notes.txt").write_text("not a game file")

    assert nxnative.game_files_changed(game) is True  # the dates alone would warn
    assert nxnative.game_files_changed(game, recorded) is False


def test_a_replaced_or_second_file_of_the_same_size_counts(tmp_path):
    game, recorded = recorded_game(tmp_path)
    base, = [p for p in game.path.iterdir() if p.suffix == ".nsp"]

    base.write_bytes(b"b" * 140)  # an updated download over the old one
    assert nxnative.game_files_changed(game, recorded) is True

    base.write_bytes(b"b" * 100)
    (game.path / "Another [0100D9F01D474001].nsp").write_bytes(b"c" * 100)  # a DLC of the same size
    assert nxnative.game_files_changed(game, recorded) is True


def test_a_nup_game_folder_is_its_one_file(tmp_path):
    from library import Game

    app = game_app(tmp_path, "123456789abc")
    nup = tmp_path / "Jamboree.nup"
    nup.write_bytes(b"n" * 50)
    game = Game(id="Jamboree.nup", path=nup, title_id=app.title_id, app=app)
    recorded = nxnative.game_files(game)

    assert recorded == {"Jamboree.nup": 50} and nxnative.game_files_changed(game, recorded) is False
    nup.write_bytes(b"n" * 60)  # replaced by a newer .nup
    assert nxnative.game_files_changed(game, recorded) is True
