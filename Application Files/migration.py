"""Move a game's save from NX Play without deleting either game's progress."""

import os
import shutil
from datetime import datetime
from pathlib import Path

from nxnative import NxnativeError


def _copy(source, destination, copy_tree):
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".importing")
    shutil.rmtree(part, ignore_errors=True)
    try:
        copy_tree(source, part)
        os.replace(part, destination)
    except OSError as e:
        shutil.rmtree(part, ignore_errors=True)
        raise NxnativeError(f"Switch Player couldn't copy the NX Play save: {e}") from None
    finally:
        shutil.rmtree(part, ignore_errors=True)


def first_build(source, destination, copy_tree=shutil.copytree):
    source, destination = Path(source), Path(destination)
    if destination.exists() or not source.is_dir():
        return False
    _copy(source, destination, copy_tree)
    return True


def modified(save):
    try:
        return max(path.stat().st_mtime for path in Path(save).rglob("*") if path.is_file())
    except (OSError, ValueError):
        return None


def import_details(source, destination, running):
    if running:
        raise NxnativeError("Quit the game before importing its NX Play save.")
    if not Path(source).is_dir():
        raise NxnativeError("There's no NX Play save to import.")
    return {"nxplay_date": modified(source), "current_date": modified(destination)}


def import_save(source, destination, copy_tree=shutil.copytree, now=datetime.now):
    source, destination = Path(source), Path(destination)
    if not source.is_dir():
        raise NxnativeError("There's no NX Play save to import.")
    backup = None
    if destination.exists():
        backup = destination.parent / "save-backups" / f"{now().strftime('%Y-%m-%d %H.%M.%S')} before NX Play import"
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, backup)
        except OSError as e:
            raise NxnativeError(f"Switch Player couldn't back up the current save: {e}") from None
    try:
        _copy(source, destination, copy_tree)
    except NxnativeError:
        if backup is not None and not destination.exists():
            os.replace(backup, destination)
        raise
    return backup
