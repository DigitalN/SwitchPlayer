import json

import records


def test_files_survive_a_relaunch(tmp_path):
    path = tmp_path / "data" / "build-records.json"
    records.Records(path).set_build("Rhythm Heaven", {"Rhythm Heaven.nsp": 100})

    again = records.Records(path)

    assert again.files("Rhythm Heaven") == {"Rhythm Heaven.nsp": 100}
    assert again.files("Other") is None
    assert not path.with_name(path.name + ".partial").exists()


def test_a_game_with_no_game_files_still_has_a_record(tmp_path):
    book = records.Records(tmp_path / "records.json")
    book.set_build("Empty", {})
    assert book.files("Empty") == {}


def test_a_damaged_or_hand_edited_file_starts_again(tmp_path):
    path = tmp_path / "records.json"
    path.write_text("{not json")
    assert records.Records(path).files("x") is None

    path.write_text(json.dumps({"games": {"ok": {"files": {"a.nsp": 1}}, "bad": {"files": {"a.nsp": "big"}},
                                          "worse": 3, "none": {}}}))
    book = records.Records(path)
    assert book.files("ok") == {"a.nsp": 1}
    assert book.files("bad") is None and book.files("worse") is None and book.files("none") is None

    path.write_text("[1, 2]")
    assert records.Records(path).files("ok") is None


def test_a_builds_commit_is_kept_beside_its_files(tmp_path):
    path = tmp_path / "records.json"
    book = records.Records(path)
    book.set_build("Rhythm Heaven", {"Rhythm Heaven.nsp": 100}, "581f9c1dbcee")
    book.set_build("Rhythm Heaven", None, "30fb21363578")  # the folder couldn't be read: its files stay
    book.set_build("Jamboree", None, "30fb21363578+dirty")
    book.set_build("Echoes", {"Echoes.nsp": 7}, None)

    again = records.Records(path)
    assert again.files("Rhythm Heaven") == {"Rhythm Heaven.nsp": 100}
    assert again.commit("Rhythm Heaven") == "30fb21363578"
    assert again.files("Jamboree") is None and again.commit("Jamboree") == "30fb21363578+dirty"
    assert again.files("Echoes") == {"Echoes.nsp": 7} and again.commit("Echoes") is None
    assert again.commit("Other") is None


def test_a_hand_edited_commit_is_ignored(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"games": {"a": {"files": {"a.nsp": 1}, "commit": 5}, "b": {"commit": ""},
                                          "c": {"commit": "581f9c1dbcee", "files": "x"}}}))
    book = records.Records(path)
    assert book.files("a") == {"a.nsp": 1} and book.commit("a") is None
    assert book.files("b") is None and book.commit("b") is None
    assert book.files("c") is None and book.commit("c") == "581f9c1dbcee"


def test_the_game_event_is_kept_beside_the_builds_record(tmp_path):
    path = tmp_path / "records.json"
    book = records.Records(path)
    book.set_build("Ocarina", {"Ocarina.nup": 9}, "581f9c1dbcee")
    book.set_game("Ocarina", "0400486024DEC000", "The Legend of Zelda: Ocarina of Time", "Ocarina.jpg")
    book.set_game("Echoes", "01008F6008C5E000", "Echoes", None)
    book.set_build("Echoes", {"Echoes.nsp": 7}, "30fb21363578")  # a finished Build keeps what the event said

    again = records.Records(path)
    assert again.game("Ocarina") == {"title_id": "0400486024DEC000", "name": "The Legend of Zelda: Ocarina of Time",
                                     "icon": "Ocarina.jpg"}
    assert again.files("Ocarina") == {"Ocarina.nup": 9} and again.commit("Ocarina") == "581f9c1dbcee"
    assert again.game("Echoes") == {"title_id": "01008F6008C5E000", "name": "Echoes", "icon": None}
    assert again.files("Echoes") == {"Echoes.nsp": 7}
    assert again.game("Other") is None

    again.set_game("Ocarina", "0400486024DEC000", "Zelda", None)  # a later Build's event replaces it
    assert records.Records(path).game("Ocarina") == {"title_id": "0400486024DEC000", "name": "Zelda", "icon": None}


def test_a_hand_edited_game_event_is_ignored(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"games": {
        "a": {"game": {"title_id": "0400486024DEC000", "name": "Zelda", "icon": "a.jpg", "extra": 1}},
        "b": {"game": {"title_id": "0400486024DEC000", "name": ""}},
        "c": {"game": {"title_id": 5, "name": "Zelda"}},
        "d": {"game": {"title_id": "0400486024DEC000", "name": "Zelda", "icon": 3}, "commit": "581f9c1dbcee"},
        "e": {"game": "Zelda"}}}))
    book = records.Records(path)
    assert book.game("a") == {"title_id": "0400486024DEC000", "name": "Zelda", "icon": "a.jpg"}
    assert book.game("b") is None and book.game("c") is None and book.game("e") is None
    assert book.game("d") is None and book.commit("d") == "581f9c1dbcee"
