"""Makes a safe copy of the database, even while a job is writing to it.

    python3 -m gogkeeper.backup

Copies go in /data/backups, one per day, and the newest 7 are kept. Each is written under a
temporary name and renamed when complete, so anything named gogkeeper-<date>.db is whole and
safe to copy at any time. A sync makes one before it changes anything.
"""

import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from gogkeeper import first_run
from gogkeeper.db import open_db

BACKUP_DIR = Path(first_run.DB_PATH).parent / "backups"
KEEP = 7
COPY_NAME = re.compile(r"gogkeeper-\d{4}-\d{2}-\d{2}\.db")


def make_backup(db):
    """Copy the database and drop the oldest copies. Returns the new copy, or None if there's no data.

    An empty database is never copied: after losing the database, it would push out good copies.
    """
    if not first_run.has_data(db):
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    temporary = BACKUP_DIR / "in-progress.tmp"
    temporary.unlink(missing_ok=True)  # left by a copy that was interrupted
    copy = sqlite3.connect(temporary)
    try:
        db.backup(copy)
        check = copy.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        copy.close()
    if check != "ok":
        raise RuntimeError(f"the copy failed its integrity check: {check}")

    target = BACKUP_DIR / f"gogkeeper-{date.today():%Y-%m-%d}.db"
    os.replace(temporary, target)
    copies = sorted(p for p in BACKUP_DIR.iterdir() if COPY_NAME.fullmatch(p.name))
    for old in copies[:-KEEP]:
        old.unlink()
    return target


def log_result(target):
    message = f"backed up the database to {target}" if target else "nothing to back up: the database is empty"
    print(f"gogkeeper: {message}", file=sys.stderr, flush=True)


def before_sync(db):
    """Back up without stopping the sync if it fails: keeping the games current matters more."""
    try:
        log_result(make_backup(db))
    except Exception as e:
        print(f"gogkeeper: WARNING: the database backup failed: {e}. Carrying on with the sync.",
              file=sys.stderr, flush=True)


def main():
    db = open_db(first_run.DB_PATH)
    try:
        log_result(make_backup(db))
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"gogkeeper: backup failed: {e}", file=sys.stderr)
        sys.exit(1)
