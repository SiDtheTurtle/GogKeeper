"""First run: fills an empty database from GOG's game list and what's already on the share.

Does nothing if the database already has any data. Nothing calls this automatically yet:
the always-on container will, once the login works. For development:
    python3 -m gogkeeper.first_run
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from gogkeeper.db import open_db

DB_PATH = "/data/gogkeeper.db"
LIBRARY = Path("/library/library")  # the engine's --directory: the share's library/ folder

# Which list a file is in gives its type, and the engine's default subfolder for it
# (see makeFilepath in lgogdownloader's src/gamedetails.cpp). Installers sit in the game's folder.
FILE_LISTS = {
    "installers": ("installer", ""),
    "extras": ("extra", "extras/"),
    "patches": ("patch", "patches/"),
    "languagepacks": ("languagepack", "languagepacks/"),
}


def cache_file():
    return Path(os.environ["XDG_CACHE_HOME"]) / "lgogdownloader" / "gamedetails.json"


def has_data(db):
    games = db.execute("SELECT count(*) FROM games WHERE product_id != -1").fetchone()[0]
    files = db.execute("SELECT count(*) FROM files").fetchone()[0]
    changelogs = db.execute("SELECT count(*) FROM changelogs").fetchone()[0]
    return games + files + changelogs > 0


def update_cache():
    """Have the engine fetch the whole library from GOG into gamedetails.json."""
    print("gogkeeper: fetching the game list from GOG, which takes a few minutes", file=sys.stderr, flush=True)
    # No keyboard input, so a missing login fails at once instead of waiting.
    result = subprocess.run(["lgogdownloader", "--update-cache"], stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(
            f"fetching the game list failed (exit {result.returncode}). "
            "If you're not logged in: docker compose run --rm gogkeeper --browser-login"
        )


def safe_name(name, what):
    """A single folder or file name from GOG, refused if it could point outside the library."""
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"unusable {what} from GOG: {name!r}")
    return name


def import_catalogue(db, catalogue, library, now):
    """Add every game, DLC, file and changelog, in one transaction. Returns counts."""
    counts = {"games": 0, "dlc": 0, "files": 0, "on_share": 0, "changelogs": 0}
    db.execute("BEGIN")
    try:
        for game in catalogue["games"]:
            _add_game(db, game, None, library, now, counts)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return counts


def _add_game(db, game, base, library, now, counts):
    """Add one game, or a DLC when base is (base product ID, base game's folder on the share)."""
    product_id = int(game["product_id"])
    gamename = safe_name(game["gamename"], "game name")
    if base is None:
        folder, base_id, game_dir = gamename, -1, library / gamename
        counts["games"] += 1
    else:
        folder, base_id = f"dlc/{gamename}", base[0]
        game_dir = base[1] / folder
        counts["dlc"] += 1

    db.execute(
        "INSERT INTO games (product_id, gamename, title, folder, base_product_id) VALUES (?, ?, ?, ?, ?)",
        (product_id, gamename, game["title"], folder, base_id),
    )

    changelog = game.get("changelog", "")
    if changelog.strip():
        db.execute(
            "INSERT INTO changelogs (product_id, fetched_at, changelog) VALUES (?, ?, ?)",
            (product_id, now, changelog),
        )
        counts["changelogs"] += 1

    for list_name, (file_type, subdir) in FILE_LISTS.items():
        # The engine leaves a list out entirely when the game has no files of that type.
        for f in game.get(list_name, []):
            path = subdir + safe_name(f["path"].rsplit("/", 1)[-1], "file name")
            on_share = (game_dir / path).is_file()
            db.execute(
                "INSERT INTO files (product_id, path, file_type, platforms, languages, version, gog_md5,"
                " status, downloaded_at, checksum_at, checksum_result)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'not_checked')",
                (
                    product_id, path, file_type, int(f["platform"]), int(f["language"]),
                    f.get("version", ""),  # the engine leaves it out when GOG gives none
                    "none" if file_type == "extra" else "unknown",
                    "downloaded" if on_share else "not_downloaded",
                ),
            )
            counts["files"] += 1
            counts["on_share"] += on_share

    for dlc in game.get("dlcs", []):
        _add_game(db, dlc, (product_id, game_dir), library, now, counts)


def main():
    db = open_db(DB_PATH)
    try:
        if has_data(db):
            print("gogkeeper: first run already done, nothing to do", file=sys.stderr)
            return
        if not LIBRARY.is_dir():
            raise RuntimeError(f"library folder not found: {LIBRARY}. Is the share mounted?")
        update_cache()
        catalogue = json.loads(cache_file().read_text())
        c = import_catalogue(db, catalogue, LIBRARY, int(time.time()))
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
