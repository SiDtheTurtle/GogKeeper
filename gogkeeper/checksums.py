"""Checks downloaded files on the share against GOG's checksum.

    python3 -m gogkeeper.checksums                  # new file run
    python3 -m gogkeeper.checksums --maintenance    # maintenance run

The new file run checks each downloaded file that has GOG's checksum and has never been
checked. The maintenance run re-checks every one of them, to catch files that have
changed on the NAS since. Both read whole files, so they're slow over a slow link. Each
result is saved as soon as it's known, so stopping part-way only loses the file in progress.
"""

import argparse
import hashlib
import sys
import time

from gogkeeper import catalogue, first_run, store_links
from gogkeeper.db import open_db

CHECKABLE_SQL = catalogue.LIVE_FILES_SQL + """
    AND f.status = 'downloaded' AND f.gog_md5 NOT IN ('none', 'unknown')"""
NEW_FILES_SQL = CHECKABLE_SQL + " AND f.checksum_at = 0 ORDER BY f.id"
MAINTENANCE_SQL = CHECKABLE_SQL + " ORDER BY f.id"


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
    parser = argparse.ArgumentParser(prog="python3 -m gogkeeper.checksums")
    parser.add_argument("--maintenance", action="store_true",
                        help="re-check every downloaded file, not only those never checked")
    run = "maintenance run" if parser.parse_args().maintenance else "new file run"

    db = open_db(first_run.DB_PATH)
    try:
        if not first_run.LIBRARY.is_mount():
            raise RuntimeError(f"share not mounted at {first_run.LIBRARY}")
        if run == "maintenance run":
            store_links.update(db, every_game=True)
        rows = db.execute(MAINTENANCE_SQL if run == "maintenance run" else NEW_FILES_SQL).fetchall()
        print(f"gogkeeper: {run}: {len(rows)} files to check", file=sys.stderr, flush=True)
        c = check(db, rows, first_run.LIBRARY)
        print(f"gogkeeper: {run} done: {c['passed']} passed, {c['failed']} failed, "
              f"{c['missing']} missing", file=sys.stderr, flush=True)
    finally:
        db.close()
    return 1 if c["failed"] or c["missing"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"gogkeeper: checksum run failed: {e}", file=sys.stderr)
        sys.exit(1)
