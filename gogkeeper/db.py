"""Opens the GogKeeper database, creating it on first use.

Safe to call on every start: an existing database is left as it is.
"""

import sqlite3
import sys
from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


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

    db.close()
    raise RuntimeError(
        f"{path} isn't a GogKeeper database this version understands "
        f"(version {version}, {tables} objects). Left untouched."
    )


def add_game(db, game):
    """Insert a game or DLC from catalogue.walk()."""
    db.execute(
        "INSERT INTO games (product_id, gamename, title, folder, base_product_id) VALUES (?, ?, ?, ?, ?)",
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
