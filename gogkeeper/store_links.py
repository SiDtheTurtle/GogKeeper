"""Fills in each game's GOG store page link from GOG's public APIs. No login needed.

The newer API gives the page GOG sells a game on now, which may be the edition that replaced
it. The older API's link is the fallback, without the '_game' or '_base' GOG sometimes adds,
then the link the game already has. Each one is opened to check it, as GOG sends a dead link
to its catalogue page rather than an error; the first that reaches a game page is kept. A game
with none becomes 'none', so the page shows no link. Maintenance checks every game again, so a
link comes back if GOG fixes it. If GOG can't be reached, a game keeps the link it had.

Sync looks up only games not looked up yet; maintenance looks up every game. A failed lookup
changes nothing and never stops the job.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.gog.com/products?ids="
API_V2 = "https://api.gog.com/v2/games/"
BATCH = 50  # the older API's limit per request
STORE = "https://www.gog.com/"  # only links to GOG's own site are kept
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GogKeeper)"}
TIMEOUT = 10  # seconds per request, so one slow page can't hold up the job
PAUSE = 0.5  # seconds between games, to go easy on GOG


def log(message):
    print(f"gogkeeper: {message}", file=sys.stderr, flush=True)


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=TIMEOUT)


def old_links(ids):
    """The older API's link for each id it knows, as {product_id: url}, with '_game' or '_base' taken off."""
    links = {}
    for i in range(0, len(ids), BATCH):
        with get(API + ",".join(map(str, ids[i:i + BATCH]))) as r:
            for product in json.load(r):
                url = product.get("links", {}).get("product_card", "")
                if isinstance(url, str) and url.startswith(STORE):
                    links[int(product["id"])] = re.sub(r"_(game|base)$", "", url)
    return links


def new_link(pid):
    """The newer API's link for one game, or '' if it has none."""
    try:
        with get(API_V2 + str(pid)) as r:
            url = json.load(r).get("_links", {}).get("store", {}).get("href", "")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        raise
    return url if isinstance(url, str) and url.startswith(STORE) else ""


def working(url):
    """Where the link ends up if that's a game page, or '' if it's dead."""
    try:
        with get(url) as r:
            final = r.geturl()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        raise
    return final if final.startswith(STORE) and "/game/" in final else ""


def find(pid, old, stored):
    """The first link that works: newer API, older API, then the one already saved. 'none' if none do."""
    for url in dict.fromkeys([new_link(pid), old, stored]):
        if url.startswith(STORE):
            final = working(url)
            if final:
                return final
        time.sleep(PAUSE)
    return "none"


def update(db, every_game):
    """Look up store links and save them. Returns how many changed, or -1 if the lookup failed."""
    where = "" if every_game else " AND store_url = 'unknown'"
    games = dict(db.execute("SELECT product_id, store_url FROM games WHERE product_id != -1" + where))
    if not games:
        return 0
    try:
        old = old_links(list(games))
    except Exception as e:
        log(f"warning: couldn't look up store links, will try next time: {e}")
        return -1
    links, unreachable = {}, 0
    for pid, stored in games.items():
        try:
            links[pid] = find(pid, old.get(pid, ""), stored)
        except Exception:
            unreachable += 1  # keeps the link it has
        time.sleep(PAUSE)
    changed = 0
    db.execute("BEGIN")
    try:
        for pid, new in links.items():
            if new != games[pid]:
                db.execute("UPDATE games SET store_url = ? WHERE product_id = ?", (new, pid))
                changed += 1
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    working_now = sum(url != "none" for url in links.values())
    log(f"store links: checked {len(games)} games, {working_now} work, {changed} changed"
        + (f", {unreachable} couldn't be reached and kept their link" if unreachable else ""))
    return changed
