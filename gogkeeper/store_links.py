"""Fills in each game's GOG store page link from GOG's public product API. No login needed.

Sync looks up only games not looked up yet; maintenance looks up every game, to catch changes.
A game GOG doesn't return keeps the link it had, so delisted games keep theirs; one that never
had a link becomes 'none'. A failed lookup changes nothing and never stops the job.
"""

import json
import sys
import urllib.request

API = "https://api.gog.com/products?ids="
BATCH = 50  # GOG's limit per request
STORE = "https://www.gog.com/"  # only links to GOG's own site are kept


def log(message):
    print(f"gogkeeper: {message}", file=sys.stderr, flush=True)


def fetch(ids):
    """GOG's store link for each id it knows, as {product_id: url}."""
    links = {}
    for i in range(0, len(ids), BATCH):
        with urllib.request.urlopen(API + ",".join(map(str, ids[i:i + BATCH])), timeout=30) as r:
            for product in json.load(r):
                url = product.get("links", {}).get("product_card", "")
                if isinstance(url, str) and url.startswith(STORE):
                    links[int(product["id"])] = url
    return links


def update(db, every_game):
    """Look up store links and save them. Returns how many changed, or -1 if the lookup failed."""
    where = "" if every_game else " AND store_url = 'unknown'"
    games = dict(db.execute("SELECT product_id, store_url FROM games WHERE product_id != -1" + where))
    if not games:
        return 0
    try:
        links = fetch(list(games))
    except Exception as e:
        log(f"warning: couldn't look up store links, will try next time: {e}")
        return -1
    changed = 0
    db.execute("BEGIN")
    try:
        for pid, old in games.items():
            new = links.get(pid) or ("none" if old == "unknown" else old)
            if new != old:
                db.execute("UPDATE games SET store_url = ? WHERE product_id = ?", (new, pid))
                changed += 1
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    log(f"store links: looked up {len(games)} games, {len(links)} found, {changed} changed")
    return changed
