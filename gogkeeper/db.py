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
