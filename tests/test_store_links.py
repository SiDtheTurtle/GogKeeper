"""Store links: GOG's APIs and pages are faked, so these run without a network."""
import io
import json
import urllib.error

import pytest

from gogkeeper import first_run, store_links

S = "https://www.gog.com/"


class FakeGog:
    """Stands in for store_links.get. Each URL maps to JSON, a page it ends up on, an HTTP code, or an exception."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def __call__(self, url):
        self.asked.append(url)
        answer = self.routes.get(url, 404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "error", {}, None)
        if isinstance(answer, str):  # a page: where it ends up after redirects
            return Page(b"", answer)
        return Page(json.dumps(answer).encode(), url)


class Page(io.BytesIO):
    def __init__(self, body, final):
        super().__init__(body)
        self.final = final

    def geturl(self):
        return self.final


def v2(pid, href):
    return {f"{store_links.API_V2}{pid}": {"_links": {"store": {"href": href}}}}


def v1(*products):
    """The older API's answer for star_pilot, its DLC and quiet_harbour, in one batch."""
    return {f"{store_links.API}1001,1002,2001": [{"id": pid, "links": {"product_card": url}} for pid, url in products]}


@pytest.fixture
def gog(monkeypatch):
    monkeypatch.setattr(store_links, "PAUSE", 0)

    def use(routes):
        fake = FakeGog(routes)
        monkeypatch.setattr(store_links, "get", fake)
        return fake
    return use


@pytest.fixture
def games(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    return db


def links(db):
    return dict(db.execute("SELECT product_id, store_url FROM games WHERE product_id != -1"))


# find: one game.

def test_the_newer_apis_link_comes_first(gog):
    gog({**v2(1, S + "en/game/star_pilot_deluxe"), S + "en/game/star_pilot_deluxe": S + "en/game/star_pilot_deluxe",
         S + "game/star_pilot": S + "en/game/star_pilot"})
    assert store_links.find(1, S + "game/star_pilot", "unknown") == S + "en/game/star_pilot_deluxe"


def test_a_dead_link_falls_back_to_the_next(gog):
    gog({**v2(1, S + "en/game/gone"), S + "en/game/gone": 404,
         S + "game/star_pilot": S + "en/game/star_pilot"})
    assert store_links.find(1, S + "game/star_pilot", "unknown") == S + "en/game/star_pilot"


def test_a_link_that_lands_on_the_catalogue_is_dead(gog):
    gog({S + "game/star_pilot": S + "en/games?query=star_pilot", S + "en/game/old": S + "en/game/old"})
    assert store_links.find(1, S + "game/star_pilot", S + "en/game/old") == S + "en/game/old"


@pytest.mark.parametrize("href", ["https://evil.example/game/x", "http://www.gog.com/game/x",
                                  "https://www.gog.com.evil.example/game/x", 5])
def test_links_off_gogs_own_site_are_never_kept(gog, href):
    fake = gog({**v2(1, href), href if isinstance(href, str) else "": S + "en/game/x"})
    assert store_links.find(1, "", "unknown") == "none"
    assert all(url.startswith(store_links.API_V2) for url in fake.asked)  # never opened


def test_a_page_that_redirects_off_gogs_site_is_dead(gog):
    gog({S + "game/star_pilot": "https://evil.example/game/star_pilot"})
    assert store_links.find(1, S + "game/star_pilot", "unknown") == "none"


def test_no_working_link_gives_none(gog):
    gog({})
    assert store_links.find(1, S + "game/star_pilot", S + "game/star_pilot") == "none"


# old_links: the older API.

def test_the_older_api_drops_game_and_base_from_links_and_skips_other_sites(gog):
    gog(v1((1001, S + "game/star_pilot_game"), (1002, S + "game/star_pilot_moons_base"),
           (2001, "https://evil.example/game/quiet_harbour")))
    assert store_links.old_links([1001, 1002, 2001]) == {
        1001: S + "game/star_pilot", 1002: S + "game/star_pilot_moons"}


# update: the whole job.

def test_update_saves_changed_links_and_counts_them(games, gog):
    games.execute(f"UPDATE games SET store_url = '{S}en/game/quiet_harbour' WHERE product_id = 2001")
    gog({**v1((1001, S + "game/star_pilot"), (2001, S + "game/quiet_harbour")),
         S + "game/star_pilot": S + "en/game/star_pilot",
         S + "game/quiet_harbour": S + "en/game/quiet_harbour"})

    assert store_links.update(games, every_game=True) == 2  # quiet_harbour's link is unchanged
    assert links(games) == {1001: S + "en/game/star_pilot", 1002: "none", 2001: S + "en/game/quiet_harbour"}


def test_sync_looks_up_only_games_not_looked_up_yet(games, gog):
    games.execute(f"UPDATE games SET store_url = '{S}en/game/star_pilot' WHERE product_id IN (1001, 2001)")
    fake = gog({f"{store_links.API}1002": []})
    store_links.update(games, every_game=False)
    assert fake.asked[0] == f"{store_links.API}1002"
    assert links(games)[1002] == "none"


def test_when_the_lookup_fails_nothing_changes(games, gog):
    gog({f"{store_links.API}1001,1002,2001": urllib.error.URLError("no network")})
    before = links(games)
    assert store_links.update(games, every_game=True) == -1
    assert links(games) == before


def test_a_game_that_cant_be_reached_keeps_its_link(games, gog):
    games.execute(f"UPDATE games SET store_url = '{S}en/game/star_pilot' WHERE product_id = 1001")
    gog({**v1(), f"{store_links.API_V2}1001": 500})  # a server error, not a dead link
    store_links.update(games, every_game=True)
    assert links(games)[1001] == S + "en/game/star_pilot"
