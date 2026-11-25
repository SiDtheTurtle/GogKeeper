"""What Sync lists as old, and what Purge then deletes.

If a current file were wrongly listed as old, Purge would delete it, so these come first.
"""
import sys

import pytest

from conftest import put
from gogkeeper import first_run, purge, sync

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a POSIX system")

BASE = "star_pilot"
DLC = "star_pilot/dlc/star_pilot_moons"


def old_files(db):
    return {path: pid for pid, path in db.execute("SELECT product_id, path FROM old_files")}


def status(db, path):
    return [r[0] for r in db.execute("SELECT status FROM files WHERE path = ? ORDER BY id", (path,))]


def test_current_files_are_never_listed(db, cat, share):
    # On the share before the first run, so these start as downloaded.
    put(share, f"{BASE}/setup_star_pilot_1.0.exe")
    put(share, f"{DLC}/setup_star_pilot_moons_1.0.exe")
    first_run.import_catalogue(db, cat, share, 0)
    # Arrive afterwards, so these are still not_downloaded but on the share.
    put(share, f"{BASE}/extras/manual.pdf")
    put(share, f"{DLC}/extras/moons_map.pdf")
    assert status(db, "extras/manual.pdf") == ["not_downloaded"]

    assert sync.record_old_files(db, str(share), 1) == 0
    assert old_files(db) == {}


def test_unknown_and_expired_files_are_listed_with_the_deepest_game(db, cat, share):
    put(share, "quiet_harbour/setup_quiet_harbour.exe")
    first_run.import_catalogue(db, cat, share, 0)
    db.execute("UPDATE files SET status = 'expired' WHERE path = 'setup_quiet_harbour.exe'")
    put(share, f"{BASE}/setup_star_pilot_0.9.exe")
    put(share, f"{DLC}/setup_star_pilot_moons_0.9.exe")
    put(share, "stray.txt")
    put(share, "unknown_game/setup.exe")

    assert sync.record_old_files(db, str(share), 1) == 5
    assert old_files(db) == {
        "quiet_harbour/setup_quiet_harbour.exe": 2001,
        f"{BASE}/setup_star_pilot_0.9.exe": 1001,
        f"{DLC}/setup_star_pilot_moons_0.9.exe": 1002,  # the DLC, not its base game
        "stray.txt": -1,
        "unknown_game/setup.exe": -1,
    }


@posix_only
def test_symlinks_are_never_listed(db, cat, share, tmp_path):
    first_run.import_catalogue(db, cat, share, 0)
    (tmp_path / "outside.exe").write_bytes(b"x")
    (share / BASE).mkdir()
    (share / BASE / "link.exe").symlink_to(tmp_path / "outside.exe")

    assert sync.record_old_files(db, str(share), 1) == 0


def test_running_again_replaces_the_list(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    put(share, "a.txt")
    gone = put(share, "b.txt")
    sync.record_old_files(db, str(share), 1)
    gone.unlink()

    assert sync.record_old_files(db, str(share), 2) == 1
    assert old_files(db) == {"a.txt": -1}
    assert db.execute("SELECT found_at FROM old_files").fetchone()[0] == 2


def test_purge_deletes_listed_files_and_marks_expired_rows_purged(db, cat, share):
    expired = put(share, "quiet_harbour/setup_quiet_harbour.exe", b"12345")
    unknown = put(share, f"{BASE}/setup_star_pilot_0.9.exe", b"123")
    current = put(share, f"{BASE}/setup_star_pilot_1.0.exe")
    first_run.import_catalogue(db, cat, share, 0)
    db.execute("UPDATE files SET status = 'expired' WHERE path = 'setup_quiet_harbour.exe'")
    sync.record_old_files(db, str(share), 1)

    assert purge.purge(db, str(share)) == (2, 0, 8)
    assert not expired.exists() and not unknown.exists()
    assert current.exists()
    assert status(db, "setup_quiet_harbour.exe") == ["purged"]
    assert old_files(db) == {}


def test_purge_skips_a_file_that_is_current_again(db, cat, share):
    target = put(share, "quiet_harbour/setup_quiet_harbour.exe")
    first_run.import_catalogue(db, cat, share, 0)
    db.execute("UPDATE files SET status = 'expired' WHERE path = 'setup_quiet_harbour.exe'")
    sync.record_old_files(db, str(share), 1)
    # GOG lists it again before Purge runs, so a refresh adds a new live row.
    sync.refresh(db, cat, 2)
    assert status(db, "setup_quiet_harbour.exe") == ["expired", "not_downloaded"]

    assert purge.purge(db, str(share)) == (0, 1, 0)
    assert target.exists()
    assert status(db, "setup_quiet_harbour.exe") == ["expired", "not_downloaded"]


def test_purge_skips_a_file_that_has_gone(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    target = put(share, "stray.txt")
    sync.record_old_files(db, str(share), 1)
    target.unlink()

    assert purge.purge(db, str(share)) == (0, 1, 0)
