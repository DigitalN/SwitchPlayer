from datetime import datetime

import pytest

import migration
from nxnative import NxnativeError

TID = "0100D9F01D474000"


def saves(tmp_path):
    game = tmp_path / "Rhythm Heaven"
    source = game / f"{TID}_build" / "run" / "save"
    (source / "slot 1").mkdir(parents=True)
    (source / "slot 1" / "progress.dat").write_bytes(b"NX Play progress")
    destination = tmp_path / "support" / TID / "save"
    return source, destination


def test_first_build_copies_the_save_and_leaves_the_original(tmp_path):
    source, destination = saves(tmp_path)

    migration.first_build(source, destination)

    assert (destination / "slot 1" / "progress.dat").read_bytes() == b"NX Play progress"
    assert (source / "slot 1" / "progress.dat").read_bytes() == b"NX Play progress"


def test_an_existing_save_is_never_replaced_by_a_first_build(tmp_path):
    source, destination = saves(tmp_path)
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")

    migration.first_build(source, destination)

    assert (destination / "current").read_text() == "Switch Player"
    assert not (destination / "slot 1").exists()


def test_a_failed_copy_does_not_leave_a_save_that_blocks_the_next_build(tmp_path):
    source, destination = saves(tmp_path)

    def fail(source, destination):
        destination.mkdir(parents=True)
        (destination / "partial").write_text("partial")
        raise OSError("disk full")

    with pytest.raises(NxnativeError, match="couldn't copy the NX Play save"):
        migration.first_build(source, destination, copy_tree=fail)

    assert not destination.exists()


def test_import_reports_both_save_dates_and_backs_up_the_current_save(tmp_path):
    source, destination = saves(tmp_path)
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")
    source_time = datetime(2026, 10, 1, 12, 0)
    destination_time = datetime(2026, 11, 1, 12, 0)
    (source / "slot 1" / "progress.dat").touch()
    import os
    os.utime(source / "slot 1" / "progress.dat", (source_time.timestamp(), source_time.timestamp()))
    os.utime(destination / "current", (destination_time.timestamp(), destination_time.timestamp()))

    details = migration.import_details(source, destination, running=False)
    assert details["nxplay_date"] == source_time.timestamp()
    assert details["current_date"] == destination_time.timestamp()

    backup = migration.import_save(source, destination, now=lambda: datetime(2026, 11, 6, 14, 30, 0))

    assert backup == destination.parent / "save-backups" / "2026-11-06 14.30.00 before NX Play import"
    assert (backup / "current").read_text() == "Switch Player"
    assert (destination / "slot 1" / "progress.dat").read_bytes() == b"NX Play progress"
    assert (source / "slot 1" / "progress.dat").read_bytes() == b"NX Play progress"


def test_import_refuses_a_missing_source_before_changing_the_current_save(tmp_path):
    source, destination = saves(tmp_path)
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")
    source.rename(source.with_name("old-save"))

    with pytest.raises(NxnativeError, match="no NX Play save"):
        migration.import_details(source, destination, running=False)

    assert (destination / "current").read_text() == "Switch Player"


def test_an_existing_backup_is_not_replaced(tmp_path):
    source, destination = saves(tmp_path)
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")
    now = lambda: datetime(2026, 11, 6, 14, 30, 0)
    backup = migration.import_save(source, destination, now=now)
    (destination / "newer").write_text("Newer Switch Player progress")

    with pytest.raises(NxnativeError, match="couldn't back up the current save"):
        migration.import_save(source, destination, now=now)

    assert (destination / "newer").read_text() == "Newer Switch Player progress"
    assert (backup / "current").read_text() == "Switch Player"


def test_a_failed_import_restores_the_current_save(tmp_path):
    source, destination = saves(tmp_path)
    destination.mkdir(parents=True)
    (destination / "current").write_text("Switch Player")

    def fail(source, destination):
        destination.mkdir(parents=True)
        (destination / "partial").write_text("partial")
        raise OSError("disk full")

    with pytest.raises(NxnativeError, match="couldn't copy the NX Play save"):
        migration.import_save(source, destination, copy_tree=fail)

    assert (destination / "current").read_text() == "Switch Player"
    assert not (destination / "partial").exists()


def test_import_refuses_to_replace_a_save_while_the_game_runs(tmp_path):
    source, destination = saves(tmp_path)

    with pytest.raises(NxnativeError, match="Quit the game before importing"):
        migration.import_details(source, destination, running=True)

    assert not destination.exists()
