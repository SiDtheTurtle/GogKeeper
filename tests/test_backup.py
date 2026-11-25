"""Database backups: what's copied, which copies are kept, and that a failure doesn't stop a sync."""
import sqlite3
from datetime import date

import pytest

from gogkeeper import backup, first_run


@pytest.fixture
def backups(tmp_path, monkeypatch):
    folder = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", folder)
    return folder


def today_copy(backups):
    return backups / f"gogkeeper-{date.today():%Y-%m-%d}.db"


def test_an_empty_database_is_never_copied(db, backups):
    assert backup.make_backup(db) is None
    assert not backups.exists()


def test_the_copy_is_a_whole_database(db, cat, share, backups):
    first_run.import_catalogue(db, cat, share, 0)

    assert backup.make_backup(db) == today_copy(backups)
    copy = sqlite3.connect(today_copy(backups))
    try:
        assert copy.execute("SELECT count(*) FROM files").fetchone()[0] == 6
    finally:
        copy.close()


def test_the_newest_7_are_kept_and_other_files_ignored(db, cat, share, backups):
    first_run.import_catalogue(db, cat, share, 0)
    backups.mkdir()
    for day in range(1, 9):
        (backups / f"gogkeeper-2000-01-0{day}.db").write_bytes(b"old")
    for other in ("notes.txt", "gogkeeper-manual.db", "gogkeeper-1999-12-31.db.bak"):
        (backups / other).write_bytes(b"keep me")

    backup.make_backup(db)

    left = sorted(p.name for p in backups.iterdir())
    assert left == sorted([f"gogkeeper-2000-01-0{day}.db" for day in range(3, 9)]
                          + [today_copy(backups).name, "notes.txt", "gogkeeper-manual.db",
                             "gogkeeper-1999-12-31.db.bak"])


def test_a_second_copy_on_the_same_day_replaces_the_first(db, cat, share, backups):
    first_run.import_catalogue(db, cat, share, 0)
    backup.make_backup(db)
    backup.make_backup(db)
    assert [p.name for p in backups.iterdir()] == [today_copy(backups).name]


def test_a_leftover_temporary_file_is_replaced(db, cat, share, backups):
    first_run.import_catalogue(db, cat, share, 0)
    backups.mkdir()
    (backups / "in-progress.tmp").write_bytes(b"half a copy from an interrupted run")

    backup.make_backup(db)

    assert not (backups / "in-progress.tmp").exists()
    assert today_copy(backups).exists()


def test_a_failed_backup_does_not_stop_a_sync(db, cat, share, tmp_path, monkeypatch, capsys):
    first_run.import_catalogue(db, cat, share, 0)
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "missing" / "backups")  # can't be created

    backup.before_sync(db)  # doesn't raise

    assert "WARNING: the database backup failed" in capsys.readouterr().err
