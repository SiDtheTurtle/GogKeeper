"""Checks downloaded files on the share against GOG's checksum.

    python3 -m gogkeeper.checksums

This is the new file run: each downloaded file that has GOG's checksum and has never been
checked. It reads the whole file, so it's slow over a slow link. Each result is saved as
soon as it's known, so stopping part-way only loses the file in progress.
The monthly maintenance run, which re-checks everything, comes later.
"""

import hashlib
import sys
import time

from gogkeeper import catalogue, first_run
from gogkeeper.db import open_db

NEW_FILES_SQL = catalogue.LIVE_FILES_SQL + """
    AND f.status = 'downloaded' AND f.checksum_at = 0 AND f.gog_md5 NOT IN ('none', 'unknown')
    ORDER BY f.id"""


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def check(db, rows, library):
    """Hash each file, compare it with GOG's checksum and record the result. Returns counts."""
    counts = {"passed": 0, "failed": 0, "missing": 0}
    for fid, path, _status, gog_md5, gamename, folder, base_folder in rows:
        name = f"{gamename}/{path}"
        on_share = library / catalogue.share_dir(folder, base_folder) / path
        if not on_share.is_file():
            # No date: nothing was compared.
            print(f"gogkeeper: warning: {name} is marked downloaded but isn't on the share", file=sys.stderr, flush=True)
            counts["missing"] += 1
            continue
        print(f"gogkeeper: checking {name}", file=sys.stderr, flush=True)
        result = "passed" if md5_of(on_share) == gog_md5 else "failed"
        db.execute(
            "UPDATE files SET checksum_at = ?, checksum_result = ? WHERE id = ?",
            (int(time.time()), result, fid),
        )
        counts[result] += 1
        if result == "failed":
            print(f"gogkeeper: FAILED: {name} doesn't match GOG's checksum", file=sys.stderr, flush=True)
    return counts


def main():
    db = open_db(first_run.DB_PATH)
    try:
        if not first_run.LIBRARY.is_dir():
            raise RuntimeError(f"library folder not found: {first_run.LIBRARY}. Is the share mounted?")
        rows = db.execute(NEW_FILES_SQL).fetchall()
        print(f"gogkeeper: new file run: {len(rows)} files to check", file=sys.stderr, flush=True)
        c = check(db, rows, first_run.LIBRARY)
        print(f"gogkeeper: new file run done: {c['passed']} passed, {c['failed']} failed, "
              f"{c['missing']} missing", file=sys.stderr, flush=True)
    finally:
        db.close()
    return 1 if c["failed"] or c["missing"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"gogkeeper: new file run failed: {e}", file=sys.stderr)
        sys.exit(1)
