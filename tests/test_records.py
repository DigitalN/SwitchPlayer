import json

import records


def test_files_survive_a_relaunch(tmp_path):
    path = tmp_path / "data" / "build-records.json"
    records.Records(path).set_files("Rhythm Heaven", {"Rhythm Heaven.nsp": 100})

    again = records.Records(path)

    assert again.files("Rhythm Heaven") == {"Rhythm Heaven.nsp": 100}
    assert again.files("Other") is None
    assert not path.with_name(path.name + ".partial").exists()


def test_a_game_with_no_game_files_still_has_a_record(tmp_path):
    book = records.Records(tmp_path / "records.json")
    book.set_files("Empty", {})
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
