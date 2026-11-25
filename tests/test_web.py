"""The page, rendered from a seeded database. Settings come from conftest.py: Windows, English."""
import re

import pytest
from fastapi.testclient import TestClient

from conftest import put
from gogkeeper import first_run, web

CHANGELOG = ("<h4>1.1</h4><script>alert('notes')</script><p onclick=\"x()\">Fixes <b>crash</b></p>"
             "<a href=\"https://evil.example\">link</a><img src=x onerror=\"y()\"><style>p{}</style>"
             "<hr><h4>1.0</h4><ul><li>First release</li></ul>")


@pytest.fixture
def client(db, cat, share, tmp_path, monkeypatch):
    """The page over a library where star_pilot's DLC failed a check and quiet_harbour is complete."""
    cat["games"][0]["changelog"] = CHANGELOG
    cat["games"][1]["title"] = "Quiet <b>Harbour</b>"
    for rel in ("star_pilot/extras/manual.pdf",
                "star_pilot/dlc/star_pilot_moons/setup_star_pilot_moons_1.0.exe",
                "star_pilot/dlc/star_pilot_moons/extras/moons_map.pdf",
                "quiet_harbour/setup_quiet_harbour.exe"):
        put(share, rel)
    first_run.import_catalogue(db, cat, share, 0)  # star_pilot's installer is missing
    db.execute("UPDATE files SET checksum_result = 'failed' WHERE path = 'setup_star_pilot_moons_1.0.exe'")
    monkeypatch.setattr(web, "DB_PATH", (tmp_path / "gogkeeper.db").as_posix())
    return TestClient(web.app)


def status_of(html, title):
    """The status shown on a game's row."""
    row = re.search(rf"<summary class=row><span>{re.escape(title)}.*?</span><span>(.*?)</span>", html)
    return re.sub(r"<[^>]+>", "", row.group(1))


def test_the_page_shows_each_games_worst_status(client):
    html = client.get("/").text
    # Missing installer and a DLC that failed its check: the failure is worse, and counts for the game.
    assert status_of(html, "Star Pilot") == "Failed check"
    assert status_of(html, "Quiet &lt;b&gt;Harbour&lt;/b&gt;") == "OK"
    assert "2 games" in html  # the DLC isn't counted as a game
    assert "1 issue" in html


def test_game_titles_are_escaped(client):
    html = client.get("/").text
    assert "Quiet <b>Harbour</b>" not in html


def test_release_notes_keep_basic_formatting_only(client):
    notes = re.search(r"<div class=notes>(.*?)</div></details>", client.get("/").text, re.S).group(1)
    assert "<h4>1.1</h4>" in notes and "<b>crash</b>" in notes and "<li>First release</li>" in notes
    for unwanted in ("<script", "alert(", "<style", "<a ", "evil.example", "<img", "onerror", "onclick"):
        assert unwanted not in notes
    assert notes.count("<div class=release>") == 2  # split at GOG's <hr>


@pytest.mark.parametrize("path", ["/", "/healthz", "/style.css", "/page.js", "/jobs"])
def test_responses_carry_the_security_policy(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == web.CSP


@pytest.mark.parametrize("path", ["/favicon.svg", "/logo.svg"])
def test_the_icons_are_sent_without_it(client, path):
    # Chromium won't draw an SVG favicon sent with this policy.
    response = client.get(path)
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


def test_jobs_returns_the_job_state(client, monkeypatch):
    assert client.get("/jobs").json() == {"running": None, "waiting": []}  # no scheduler attached
    monkeypatch.setattr(web, "job_status", lambda: {"running": "sync", "waiting": ["purge"]})
    assert client.get("/jobs").json() == {"running": "sync", "waiting": ["purge"]}
