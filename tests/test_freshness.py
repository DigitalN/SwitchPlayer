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


def game_app(tmp_path, commit, chain="b-41"):
    folder = tmp_path / "Rhythm Heaven"
    contents = folder / "Rhythm Heaven Groove.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "DMTitleID": "0100D9F01D474000",
        "CFBundleDisplayName": "Rhythm Heaven Groove",
        "DMCommit": commit,
        "DMChain": chain,
    }))
    return read_app(folder / "Rhythm Heaven Groove.app")


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


def test_a_dirty_build_does_not_claim_to_be_current(tmp_path, upstream):
    built = upstream.commit("print('built')\n")
    upstream.push()
    copy = nxnative.Copy(tmp_path / "copy", own=True, upstream=str(upstream.bare))
    copy.update()
    app = game_app(tmp_path, built[:12] + "+dirty")

    assert copy.freshness([app])["0100D9F01D474000"] == {"out_of_date": True, "newer": 0}
