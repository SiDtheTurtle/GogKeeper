"""Filling the database from GOG's list: the first import, and each sync's refresh after it."""
import pytest

from conftest import put
from gogkeeper import first_run, sync


def files(db):
    """(gamename, path) -> statuses, oldest row first."""
    out = {}
    for name, path, status in db.execute(
            "SELECT g.gamename, f.path, f.status FROM files f JOIN games g USING (product_id) ORDER BY f.id"):
        out.setdefault((name, path), []).append(status)
    return out


def count(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_first_import_adds_everything(db, cat, share):
    put(share, "star_pilot/setup_star_pilot_1.0.exe")
    put(share, "star_pilot/dlc/star_pilot_moons/extras/moons_map.pdf")

    counts = first_run.import_catalogue(db, cat, share, 0)

    assert counts == {"games": 2, "dlc": 1, "files": 6, "on_share": 2, "changelogs": 1}
    assert files(db) == {
        ("star_pilot", "setup_star_pilot_1.0.exe"): ["downloaded"],
        ("star_pilot", "extras/manual.pdf"): ["not_downloaded"],
        ("star_pilot", "languagepacks/setup_star_pilot_fr_1.0.exe"): ["not_downloaded"],
        ("star_pilot_moons", "setup_star_pilot_moons_1.0.exe"): ["not_downloaded"],
        ("star_pilot_moons", "extras/moons_map.pdf"): ["downloaded"],
        ("quiet_harbour", "setup_quiet_harbour.exe"): ["not_downloaded"],
    }
    dlc = db.execute("SELECT folder, base_product_id FROM games WHERE product_id = 1002").fetchone()
    assert dlc == ("dlc/star_pilot_moons", 1001)
    assert first_run.has_data(db)


def test_refresh_adds_new_games_and_files_and_expires_dropped_ones(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    star = cat["games"][0]
    star["installers"] = [{"path": "/star_pilot/setup_star_pilot_1.1.exe", "platform": 1, "language": 1,
                           "version": "1.1"}]
    cat["games"].append({"gamename": "glass_garden", "product_id": "3001", "title": "Glass Garden",
                         "installers": [{"path": "/glass_garden/setup_glass_garden.exe",
                                         "platform": 1, "language": 1}]})

    counts = sync.refresh(db, cat, 1)

    assert counts == {"new_games": 1, "new_files": 2, "expired": 1, "changelogs": 0}
    f = files(db)
    assert f[("star_pilot", "setup_star_pilot_1.0.exe")] == ["expired"]  # kept, not deleted
    assert f[("star_pilot", "setup_star_pilot_1.1.exe")] == ["not_downloaded"]
    assert f[("glass_garden", "setup_glass_garden.exe")] == ["not_downloaded"]


def test_refresh_stores_a_changelog_only_when_it_changes(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    assert sync.refresh(db, cat, 1)["changelogs"] == 0
    cat["games"][0]["changelog"] = "<h4>1.1</h4><ul><li>Fixes</li></ul>"
    assert sync.refresh(db, cat, 2)["changelogs"] == 1
    cat["games"][1]["changelog"] = "   "  # blank text is never stored
    assert sync.refresh(db, cat, 3)["changelogs"] == 0
    assert count(db, "changelogs") == 2


def test_refresh_leaves_known_games_as_they_are(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    cat["games"][1]["title"] = "Quiet Harbour: Remastered"
    sync.refresh(db, cat, 1)
    assert db.execute("SELECT title FROM games WHERE product_id = 2001").fetchone()[0] == "Quiet Harbour"


def test_a_purged_file_listed_again_comes_back_as_a_new_row(db, cat, share):
    first_run.import_catalogue(db, cat, share, 0)
    db.execute("UPDATE files SET status = 'purged' WHERE path = 'setup_quiet_harbour.exe'")

    assert sync.refresh(db, cat, 1)["new_files"] == 1
    assert files(db)[("quiet_harbour", "setup_quiet_harbour.exe")] == ["purged", "not_downloaded"]


@pytest.mark.parametrize("step", ["import", "refresh"])
def test_a_failure_part_way_saves_nothing(db, cat, share, step):
    if step == "refresh":
        first_run.import_catalogue(db, cat, share, 0)
        cat["games"][0]["installers"] = []  # would expire a file
    cat["games"].insert(1, {"gamename": "new_game", "product_id": "4001", "title": "New Game"})
    cat["games"].append({"gamename": "../escape", "product_id": "4002", "title": "Bad"})  # refused by safe_name
    before = {t: count(db, t) for t in ("games", "files", "changelogs")}
    statuses = files(db)

    with pytest.raises(ValueError):
        if step == "import":
            first_run.import_catalogue(db, cat, share, 0)
        else:
            sync.refresh(db, cat, 1)

    assert {t: count(db, t) for t in ("games", "files", "changelogs")} == before
    assert files(db) == statuses
