"""The sync: keeps the database in line with GOG, then downloads what's missing.

    python3 -m gogkeeper.sync [--game REGEX]

Needs GOGKEEPER_PLATFORM and GOGKEEPER_LANGUAGE, in the engine's format, e.g. w and en, or w+l.
--game limits the download to matching games, for testing. Copying GOG's checksums comes next.
"""

import argparse
import os
import re
import subprocess
import sys
import time

from gogkeeper import catalogue, first_run
from gogkeeper.db import add_changelog_if_new, add_file, add_game, open_db

# The engine prints this when a file finishes, e.g.
#   20261107T120000 [Thread #1] Download complete: setup_game_(123).exe (@ 50.2 MB/s)
# A failed transfer prints "Download complete (<error>): ..." instead, which this doesn't match.
DOWNLOAD_COMPLETE = re.compile(r"Download complete: (.+) \(@ [^)]*\)\s*$")


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


def setting(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} isn't set. It has no default: choose it in .env")
    return value


def download_args(platform, language, game=None):
    """The engine's download command: the full pull's arguments, with platform and language from settings."""
    args = [
        "lgogdownloader", "--download", "--use-cache", "--automatic-xml-creation",
        "--directory", str(first_run.LIBRARY), "--platform", platform, "--language", language,
        "--exclude", "bp,dp",  # no patches for now
        "--progress-interval", "10000", "--no-color", "--no-unicode", "--no-window-progress",
    ]
    if game:
        args += ["--game", game]
    return args


def record_download(db, by_name, filename, now):
    """Mark the file a "Download complete" line names as downloaded. Returns True if recorded.

    The line gives only the file name, and several games can have a file with the same name.
    Then the file that has just appeared on the share is the one: not downloaded before, and
    there now. If that still isn't exactly one file, it isn't guessed.
    """
    candidates = by_name.get(filename, [])
    if len(candidates) > 1:
        candidates = [c for c in candidates if c["status"] == "not_downloaded" and c["on_share"].is_file()]
    if len(candidates) != 1:
        problem = ("matches no file in the database" if filename not in by_name
                   else f"could be any of {len(by_name[filename])} files with that name")
        print(f"gogkeeper: warning: downloaded {filename}, but it {problem}. Not recorded.",
              file=sys.stderr, flush=True)
        return False
    # New contents, so any earlier checksum no longer applies.
    db.execute(
        "UPDATE files SET status = 'downloaded', downloaded_at = ?, checksum_at = 0,"
        " checksum_result = 'not_checked' WHERE id = ?",
        (now, candidates[0]["id"]),
    )
    candidates[0]["status"] = "downloaded"
    return True


def download(db, args):
    """Run the engine's download, showing its output and recording each file as it finishes."""
    by_name = {}  # file name -> live files with that name, and where each sits on the share
    for fid, path, status, folder, base_folder in db.execute("""
            SELECT f.id, f.path, f.status, g.folder, b.folder FROM files f
            JOIN games g ON g.product_id = f.product_id
            LEFT JOIN games b ON b.product_id = g.base_product_id AND g.base_product_id != -1
            WHERE f.status != 'expired'"""):
        share_dir = f"{base_folder}/{folder}" if base_folder else folder
        by_name.setdefault(path.rsplit("/", 1)[-1], []).append(
            {"id": fid, "status": status, "on_share": first_run.LIBRARY / share_dir / path})

    recorded = 0
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pending = b""
    while chunk := proc.stdout.read1(65536):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        *lines, pending = re.split(rb"[\r\n]", pending + chunk)
        for line in lines:
            m = DOWNLOAD_COMPLETE.search(line.decode("utf-8", "replace"))
            if m:
                recorded += record_download(db, by_name, m.group(1), int(time.time()))
    m = DOWNLOAD_COMPLETE.search(pending.decode("utf-8", "replace"))
    if m:
        recorded += record_download(db, by_name, m.group(1), int(time.time()))

    code = proc.wait()
    print(f"gogkeeper: download finished: {recorded} files recorded", file=sys.stderr, flush=True)
    if code != 0:
        raise RuntimeError(f"the download ended with an error (exit {code})")


def main():
    parser = argparse.ArgumentParser(prog="python3 -m gogkeeper.sync")
    parser.add_argument("--game", help="only download games matching this regex, for testing")
    game = parser.parse_args().game
    platform, language = setting("GOGKEEPER_PLATFORM"), setting("GOGKEEPER_LANGUAGE")

    db = open_db(first_run.DB_PATH)
    try:
        if not first_run.LIBRARY.is_dir():
            raise RuntimeError(f"library folder not found: {first_run.LIBRARY}. Is the share mounted?")
        catalogue.update_cache()
        cat, now = catalogue.load(), int(time.time())
        if first_run.has_data(db):
            c = refresh(db, cat, now)
            print(f"gogkeeper: refreshed: {c['new_games']} new games or DLC, {c['new_files']} new files, "
                  f"{c['expired']} expired, {c['changelogs']} changed changelogs", file=sys.stderr, flush=True)
        else:
            c = first_run.import_catalogue(db, cat, first_run.LIBRARY, now)
            print(f"gogkeeper: empty database, so did the first run: {c['files']} files, "
                  f"{c['on_share']} already on the share", file=sys.stderr, flush=True)
        download(db, download_args(platform, language, game))
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"gogkeeper: sync failed: {e}", file=sys.stderr)
        sys.exit(1)
