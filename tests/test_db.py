"""Opening the database: creating it, reopening it, upgrading it, and refusing what it doesn't know."""
import sqlite3

import pytest

from conftest import FIXTURES
from gogkeeper import db as dbmod, first_run


def snapshot(path):
    """The schema and every row, to show that opening a database changed nothing."""
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {
            "version": conn.execute("PRAGMA user_version").fetchone()[0],
            "schema": conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(),
            "rows": {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables},
        }
    finally:
        conn.close()


def test_a_missing_folder_is_an_error_not_created(tmp_path):
    folder = tmp_path / "not_mounted"
    with pytest.raises(FileNotFoundError):
        dbmod.open_db(folder / "gogkeeper.db")
    assert not folder.exists()


def test_a_new_database_is_at_the_current_version(tmp_path):
    dbmod.open_db(tmp_path / "gogkeeper.db").close()
    s = snapshot(tmp_path / "gogkeeper.db")
    assert s["version"] == dbmod.SCHEMA_VERSION
    assert {"games", "files", "changelogs", "sync_runs", "old_files"} <= set(s["rows"])


def test_opening_again_changes_nothing(db, cat, share, tmp_path):
    first_run.import_catalogue(db, cat, share, 0)
    before = snapshot(tmp_path / "gogkeeper.db")
    dbmod.open_db(tmp_path / "gogkeeper.db").close()
    assert snapshot(tmp_path / "gogkeeper.db") == before


def make_v2(path):
    """A version-2 database, built from the schema as released, with a little data in it."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((FIXTURES / "schema_v2.sql").read_text())
    conn.executescript("""
        INSERT INTO games VALUES (1001, 'star_pilot', 'Star Pilot', 'star_pilot', -1);
        INSERT INTO games VALUES (1002, 'star_pilot_moons', 'Star Pilot: Moons', 'dlc/star_pilot_moons', 1001);
        INSERT INTO files VALUES (1, 1001, 'setup_star_pilot_1.0.exe', 'installer', 1, 1, '1.0',
                                  'unknown', 'expired', 100, 0, 'not_checked');
        INSERT INTO files VALUES (2, 1001, 'setup_star_pilot_1.1.exe', 'installer', 1, 1, '1.1',
                                  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'downloaded', 200, 300, 'passed');
        INSERT INTO files VALUES (3, 1002, 'extras/moons_map.pdf', 'extra', 1, 1, '',
                                  'none', 'not_downloaded', 0, 0, 'not_checked');
        INSERT INTO changelogs VALUES (1, 1001, 100, '<h4>1.1</h4>');
        INSERT INTO sync_runs VALUES (1, 100, 150, 'succeeded', 1, 2, 3, 0, 1);
        PRAGMA user_version = 2;
    """)
    conn.close()


def test_a_version_2_database_upgrades_with_its_data(tmp_path):
    path = tmp_path / "gogkeeper.db"
    make_v2(path)
    before = snapshot(path)["rows"]

    conn = dbmod.open_db(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # Every row is still there, with store_url added to games.
        assert conn.execute("SELECT * FROM files ORDER BY id").fetchall() == before["files"]
        assert conn.execute("SELECT * FROM changelogs").fetchall() == before["changelogs"]
        assert conn.execute("SELECT * FROM sync_runs").fetchall() == before["sync_runs"]
        assert conn.execute("SELECT product_id, gamename, title, folder, base_product_id FROM games"
                            " ORDER BY 1").fetchall() == before["games"]
        assert dict(conn.execute("SELECT product_id, store_url FROM games")) == {
            -1: "none", 1001: "unknown", 1002: "unknown"}
        # What version 3 added works: 'purged', and the old_files table.
        conn.execute("UPDATE files SET status = 'purged' WHERE id = 1")
        conn.execute("INSERT INTO old_files (product_id, path, size, found_at) VALUES (-1, 'a.txt', 1, 0)")
    finally:
        conn.close()


def test_an_upgrade_happens_only_once(tmp_path, capsys):
    path = tmp_path / "gogkeeper.db"
    make_v2(path)
    dbmod.open_db(path).close()
    dbmod.open_db(path).close()
    assert capsys.readouterr().err.count("database upgraded from version 2") == 1


@pytest.mark.parametrize("make", ["newer", "foreign"])
def test_an_unknown_database_is_refused_and_left_untouched(tmp_path, make):
    path = tmp_path / "gogkeeper.db"
    conn = sqlite3.connect(path)
    if make == "newer":
        conn.execute(f"PRAGMA user_version = {dbmod.SCHEMA_VERSION + 1}")
    conn.execute("CREATE TABLE something_else (x)")
    conn.commit()
    conn.close()
    before = snapshot(path)

    with pytest.raises(RuntimeError, match="Left untouched"):
        dbmod.open_db(path)
    assert snapshot(path) == before
