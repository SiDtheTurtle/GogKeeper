"""First run: fills an empty database from GOG's game list and what's already on the share.

Does nothing if the database already has any data. Nothing calls this automatically yet:
the always-on container will, once the login works. For development:
    python3 -m gogkeeper.first_run
"""

import sys
import time
from pathlib import Path

from gogkeeper import catalogue
from gogkeeper.db import add_changelog_if_new, add_file, add_game, open_db

DB_PATH = "/data/gogkeeper.db"
LIBRARY = Path("/library")  # the engine's --directory: the share itself


def has_data(db):
    games = db.execute("SELECT count(*) FROM games WHERE product_id != -1").fetchone()[0]
    files = db.execute("SELECT count(*) FROM files").fetchone()[0]
    changelogs = db.execute("SELECT count(*) FROM changelogs").fetchone()[0]
    return games + files + changelogs > 0


def import_catalogue(db, cat, library, now):
    """Add every game, DLC, file and changelog, in one transaction. Returns counts."""
    counts = {"games": 0, "dlc": 0, "files": 0, "on_share": 0, "changelogs": 0}
    db.execute("BEGIN")
    try:
        for game in catalogue.walk(cat):
            add_game(db, game)
            counts["games" if game["base_product_id"] == -1 else "dlc"] += 1
            counts["changelogs"] += add_changelog_if_new(db, game["product_id"], game["changelog"], now)
            for f in game["files"]:
                on_share = (library / game["share_dir"] / f["path"]).is_file()
                add_file(db, game["product_id"], f, "downloaded" if on_share else "not_downloaded")
                counts["files"] += 1
                counts["on_share"] += on_share
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return counts


def main():
    db = open_db(DB_PATH)
    try:
        if has_data(db):
            print("gogkeeper: first run already done, nothing to do", file=sys.stderr)
            return
        if not LIBRARY.is_dir():
            raise RuntimeError(f"library folder not found: {LIBRARY}. Is the share mounted?")
        catalogue.update_cache()
        c = import_catalogue(db, catalogue.load(), LIBRARY, int(time.time()))
        print(
            f"gogkeeper: first run done: {c['games']} games, {c['dlc']} DLC, {c['files']} files "
            f"({c['on_share']} already on the share), {c['changelogs']} changelogs",
            file=sys.stderr,
        )
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"gogkeeper: first run failed: {e}", file=sys.stderr)
        sys.exit(1)
