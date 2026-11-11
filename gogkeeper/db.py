"""Opens the GogKeeper database, creating it on first use.

Safe to call on every start: an existing database is left as it is.
"""

import sqlite3
import sys
from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 4

# The statements that take a database from each version to the next. schema.sql is always
# the latest whole schema, used only for a new database.
UPGRADES = {
    1: [
        """CREATE TABLE sync_runs (
            id                  INTEGER PRIMARY KEY,
            started_at          INTEGER NOT NULL,
            finished_at         INTEGER NOT NULL,
            result              TEXT NOT NULL
                                CHECK (result IN ('running', 'succeeded', 'failed', 'interrupted')),
            downloaded          INTEGER NOT NULL,
            new_games           INTEGER NOT NULL,
            new_files           INTEGER NOT NULL,
            removed_files       INTEGER NOT NULL,
            changed_changelogs  INTEGER NOT NULL
        )""",
    ],
    # SQLite can only add a NOT NULL column with a default. Code always sets the value anyway.
    2: [
        "ALTER TABLE games ADD COLUMN store_url TEXT NOT NULL DEFAULT 'unknown'",
        "UPDATE games SET store_url = 'none' WHERE product_id = -1",
    ],
    # Allowed values move from CHECK lists into lookup tables, and 'purged' is added. SQLite
    # can't change a CHECK in place, so files and sync_runs are rebuilt as SQLite documents:
    # new table, copy every row, drop the old one, rename. No other table points at either.
    # Every copied row is checked against the new foreign keys, so a bad value stops the upgrade.
    3: [
        "CREATE TABLE file_statuses (value TEXT PRIMARY KEY) WITHOUT ROWID",
        "INSERT INTO file_statuses (value) VALUES ('not_downloaded'), ('downloaded'), ('expired'), ('purged')",
        "CREATE TABLE file_types (value TEXT PRIMARY KEY) WITHOUT ROWID",
        "INSERT INTO file_types (value) VALUES ('installer'), ('extra'), ('patch'), ('languagepack')",
        "CREATE TABLE checksum_results (value TEXT PRIMARY KEY) WITHOUT ROWID",
        "INSERT INTO checksum_results (value) VALUES ('not_checked'), ('passed'), ('failed')",
        "CREATE TABLE sync_results (value TEXT PRIMARY KEY) WITHOUT ROWID",
        "INSERT INTO sync_results (value) VALUES ('running'), ('succeeded'), ('failed'), ('interrupted')",

        """CREATE TABLE files_new (
            id               INTEGER PRIMARY KEY,
            product_id       INTEGER NOT NULL REFERENCES games(product_id) CHECK (product_id != -1),
            path             TEXT NOT NULL,
            file_type        TEXT NOT NULL REFERENCES file_types(value),
            platforms        INTEGER NOT NULL,
            languages        INTEGER NOT NULL,
            version          TEXT NOT NULL,
            gog_md5          TEXT NOT NULL
                             CHECK (gog_md5 IN ('none', 'unknown') OR length(gog_md5) = 32),
            status           TEXT NOT NULL REFERENCES file_statuses(value),
            downloaded_at    INTEGER NOT NULL,
            checksum_at      INTEGER NOT NULL,
            checksum_result  TEXT NOT NULL REFERENCES checksum_results(value)
        )""",
        """INSERT INTO files_new (id, product_id, path, file_type, platforms, languages, version, gog_md5,
                                  status, downloaded_at, checksum_at, checksum_result)
           SELECT id, product_id, path, file_type, platforms, languages, version, gog_md5,
                  status, downloaded_at, checksum_at, checksum_result FROM files""",
        "DROP TABLE files",  # also drops files_one_live
        "ALTER TABLE files_new RENAME TO files",
        "CREATE UNIQUE INDEX files_one_live ON files (product_id, path) WHERE status NOT IN ('expired', 'purged')",

        """CREATE TABLE sync_runs_new (
            id                  INTEGER PRIMARY KEY,
            started_at          INTEGER NOT NULL,
            finished_at         INTEGER NOT NULL,
            result              TEXT NOT NULL REFERENCES sync_results(value),
            downloaded          INTEGER NOT NULL,
            new_games           INTEGER NOT NULL,
            new_files           INTEGER NOT NULL,
            removed_files       INTEGER NOT NULL,
            changed_changelogs  INTEGER NOT NULL
        )""",
        """INSERT INTO sync_runs_new (id, started_at, finished_at, result, downloaded, new_games, new_files,
                                      removed_files, changed_changelogs)
           SELECT id, started_at, finished_at, result, downloaded, new_games, new_files,
                  removed_files, changed_changelogs FROM sync_runs""",
        "DROP TABLE sync_runs",
        "ALTER TABLE sync_runs_new RENAME TO sync_runs",

        """CREATE TABLE old_files (
            id               INTEGER PRIMARY KEY,
            product_id       INTEGER NOT NULL REFERENCES games(product_id),
            path             TEXT NOT NULL UNIQUE,
            size             INTEGER NOT NULL,
            found_at         INTEGER NOT NULL
        )""",
    ],
}


def upgrade(db):
    """Bring an older database up to SCHEMA_VERSION, all or nothing.

    Takes the write lock before re-reading the version, so if two jobs start together
    the second finds the work already done.
    """
    db.execute("BEGIN IMMEDIATE")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for step in range(version, SCHEMA_VERSION):
        for statement in UPGRADES[step]:
            db.execute(statement)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.execute("COMMIT")
    if version < SCHEMA_VERSION:
        print(f"gogkeeper: database upgraded from version {version} to {SCHEMA_VERSION}", file=sys.stderr, flush=True)


def open_db(path):
    """Return a connection to the database at path, creating its tables if it's new.

    The folder must already exist. A missing folder, such as an unmounted volume,
    raises an error instead of being created.
    """
    folder = Path(path).parent
    if not folder.is_dir():
        raise FileNotFoundError(f"Database folder doesn't exist: {folder}")

    db = sqlite3.connect(path, isolation_level=None)  # we manage transactions ourselves
    db.execute("PRAGMA foreign_keys = ON")

    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return db

    tables = db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    if version == 0 and tables == 0:
        # One transaction, so a crash part-way leaves an empty file to retry from.
        try:
            db.executescript(
                "BEGIN;\n"
                + SCHEMA_FILE.read_text()
                + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
            )
        except BaseException:
            db.close()  # rolls back and releases the lock
            raise
        return db

    if 1 <= version < SCHEMA_VERSION:
        try:
            upgrade(db)
        except BaseException:
            db.close()  # rolls back and releases the lock
            raise
        return db

    db.close()
    raise RuntimeError(
        f"{path} isn't a GogKeeper database this version understands "
        f"(version {version}, {tables} objects). Left untouched."
    )


def add_game(db, game):
    """Insert a game or DLC from catalogue.walk(). Its store link isn't fetched yet."""
    db.execute(
        "INSERT INTO games (product_id, gamename, title, folder, base_product_id, store_url)"
        " VALUES (?, ?, ?, ?, ?, 'unknown')",
        (game["product_id"], game["gamename"], game["title"], game["folder"], game["base_product_id"]),
    )


def add_file(db, product_id, f, status):
    """Insert a file from catalogue.walk(): never downloaded or checked, GOG checksum not fetched."""
    db.execute(
        "INSERT INTO files (product_id, path, file_type, platforms, languages, version, gog_md5,"
        " status, downloaded_at, checksum_at, checksum_result)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'not_checked')",
        (
            product_id, f["path"], f["file_type"], f["platforms"], f["languages"], f["version"],
            "none" if f["file_type"] == "extra" else "unknown",
            status,
        ),
    )


def add_changelog_if_new(db, product_id, changelog, now):
    """Store a game's changelog if it isn't empty and differs from its latest copy. Returns True if stored."""
    if not changelog.strip():
        return False
    latest = db.execute(
        "SELECT changelog FROM changelogs WHERE product_id = ? ORDER BY id DESC LIMIT 1", (product_id,)
    ).fetchone()
    if latest and latest[0] == changelog:
        return False
    db.execute(
        "INSERT INTO changelogs (product_id, fetched_at, changelog) VALUES (?, ?, ?)",
        (product_id, now, changelog),
    )
    return True


if __name__ == "__main__":
    # Run at container start: python3 -m gogkeeper.db /data/gogkeeper.db
    # Messages go to stderr, so the engine's normal output stays clean.
    db_path = sys.argv[1]
    existed = Path(db_path).is_file() and Path(db_path).stat().st_size > 0
    try:
        open_db(db_path).close()
    except Exception as e:
        print(f"gogkeeper: database error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"gogkeeper: database {'opened' if existed else 'created'}: {db_path}", file=sys.stderr)
