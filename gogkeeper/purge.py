"""Deletes the old copies the last sync listed: the only thing in GogKeeper that deletes game files.

    python3 -m gogkeeper.purge

Started only from the page's Purge button, never on a schedule. It deletes only files that
are in old_files, the list the page showed, and checks each one again first: it must still be
a plain file inside the library, and not a live file, in case GOG has listed that path again
since. Expired rows for a deleted file become 'purged' and are kept. Folders are never removed.
The database is backed up first, and if that fails nothing is deleted.
"""

import os
import sys
import time

from gogkeeper import backup, catalogue, first_run, sync
from gogkeeper.db import open_db


def file_rows(db):
    """Every file row's path relative to the library, with its id and status."""
    rows = db.execute("""SELECT f.id, f.path, f.status, g.folder, b.folder
                         FROM files f
                         JOIN games g ON g.product_id = f.product_id
                         LEFT JOIN games b ON b.product_id = g.base_product_id AND g.base_product_id != -1""")
    return [(fid, f"{catalogue.share_dir(folder, base_folder)}/{path}", status)
            for fid, path, status, folder, base_folder in rows]


def safe_target(library, rel):
    """The file to delete, or None if the path could reach outside the library or isn't a plain file."""
    if rel.startswith("/") or ".." in rel.split("/"):
        return None
    target = os.path.join(library, rel)
    if os.path.islink(target) or not os.path.isfile(target):
        return None
    if os.path.commonpath([os.path.realpath(target), os.path.realpath(library)]) != os.path.realpath(library):
        return None
    return target


def purge(db, library):
    """Delete each listed old copy that still checks out. Returns (deleted, skipped, bytes freed)."""
    rows = file_rows(db)
    live = {rel for _fid, rel, status in rows if status in ("not_downloaded", "downloaded")}
    expired = {}
    for fid, rel, status in rows:
        if status == "expired":
            expired.setdefault(rel, []).append(fid)

    deleted = skipped = freed = 0
    for old_id, rel, size in db.execute("SELECT id, path, size FROM old_files ORDER BY path").fetchall():
        target = safe_target(library, rel)
        if rel in live or not target:
            reason = "it's a current file again" if rel in live else "it's gone or isn't a plain file in the library"
            print(f"gogkeeper: purge: skipped {rel}: {reason}", file=sys.stderr, flush=True)
            skipped += 1
            continue
        os.remove(target)
        # Recorded one file at a time, so a failure part-way leaves the database matching the share.
        db.execute("BEGIN")
        db.execute("DELETE FROM old_files WHERE id = ?", (old_id,))
        db.executemany("UPDATE files SET status = 'purged' WHERE id = ?", [(i,) for i in expired.get(rel, [])])
        db.execute("COMMIT")
        print(f"gogkeeper: purge: deleted {rel}", file=sys.stderr, flush=True)
        deleted += 1
        freed += size
    return deleted, skipped, freed


def main():
    if not first_run.LIBRARY.is_mount():
        raise RuntimeError(f"share not mounted at {first_run.LIBRARY}")
    db = open_db(first_run.DB_PATH)
    try:
        copy = backup.make_backup(db)  # raises if it fails, so nothing is deleted without one
        backup.log_result(copy)
        deleted, skipped, freed = purge(db, str(first_run.LIBRARY))
        # So the page's list is right straight away, not only after the next sync.
        sync.record_old_files(db, str(first_run.LIBRARY), int(time.time()))
    finally:
        db.close()
    print(f"gogkeeper: purge finished: {deleted} deleted ({freed / 1e9:.1f} GB), {skipped} skipped",
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"gogkeeper: purge failed: {e}", file=sys.stderr)
        sys.exit(1)
