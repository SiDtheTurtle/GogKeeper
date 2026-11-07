"""Reads the engine's gamedetails.json: only the fields GogKeeper stores.

Serials, CD keys and everything else in the file are never read.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

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


def load():
    return json.loads(cache_file().read_text())


def safe_name(name, what):
    """A single folder or file name from GOG, refused if it could point outside the library."""
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"unusable {what} from GOG: {name!r}")
    return name


def walk(catalogue):
    """Yield one dict per game and DLC, base games before their DLC.

    Keys: product_id, gamename, title, folder, base_product_id, share_dir (the folder relative
    to the library), changelog, and files: a list of dicts with path, file_type, platforms,
    languages and version.
    """
    for game in catalogue["games"]:
        yield from _walk_game(game, None)


def _walk_game(game, base):
    gamename = safe_name(game["gamename"], "game name")
    if base is None:
        folder, base_id, share_dir = gamename, -1, gamename
    else:
        folder, base_id = f"dlc/{gamename}", base["product_id"]
        share_dir = f"{base['share_dir']}/{folder}"

    files = []
    for list_name, (file_type, subdir) in FILE_LISTS.items():
        # The engine leaves a list out entirely when the game has no files of that type.
        for f in game.get(list_name, []):
            files.append({
                "path": subdir + safe_name(f["path"].rsplit("/", 1)[-1], "file name"),
                "file_type": file_type,
                "platforms": int(f["platform"]),
                "languages": int(f["language"]),
                "version": f.get("version", ""),  # the engine leaves it out when GOG gives none
            })

    entry = {
        "product_id": int(game["product_id"]),
        "gamename": gamename,
        "title": game["title"],
        "folder": folder,
        "base_product_id": base_id,
        "share_dir": share_dir,
        "changelog": game.get("changelog", ""),
        "files": files,
    }
    yield entry
    for dlc in game.get("dlcs", []):
        yield from _walk_game(dlc, entry)
