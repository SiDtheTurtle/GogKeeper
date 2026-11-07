"""The sync: keeps the database in line with GOG, then downloads what's missing.

Step 1, built so far: refresh() updates the database from the engine's latest gamedetails.json.
Downloading and copying GOG's checksums come next.
"""

from gogkeeper import catalogue
from gogkeeper.db import add_changelog_if_new, add_file, add_game


def refresh(db, cat, now):
    """Bring the database in line with GOG's list, in one transaction. Returns counts.

    New games, DLC and files are added; new files start as not_downloaded. Files GOG no
    longer lists are marked expired and kept. Games already in the database are left as
    they are. A changelog is stored when its text has changed.
    """
    counts = {"new_games": 0, "new_files": 0, "expired": 0, "changelogs": 0}
    known_games = {r[0] for r in db.execute("SELECT product_id FROM games")}
    live = {  # (product_id, path) of every file that isn't expired
        (r[0], r[1]) for r in db.execute("SELECT product_id, path FROM files WHERE status != 'expired'")
    }
    listed = set()

    db.execute("BEGIN")
    try:
        for game in catalogue.walk(cat):
            pid = game["product_id"]
            if pid not in known_games:
                add_game(db, game)
                known_games.add(pid)
                counts["new_games"] += 1
            counts["changelogs"] += add_changelog_if_new(db, pid, game["changelog"], now)
            for f in game["files"]:
                listed.add((pid, f["path"]))
                if (pid, f["path"]) not in live:
                    add_file(db, pid, f, "not_downloaded")
                    counts["new_files"] += 1

        for pid, path in live - listed:
            db.execute(
                "UPDATE files SET status = 'expired' WHERE product_id = ? AND path = ? AND status != 'expired'",
                (pid, path),
            )
            counts["expired"] += 1
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return counts
