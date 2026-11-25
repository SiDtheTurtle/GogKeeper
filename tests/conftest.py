import json
import os
from pathlib import Path

import pytest

# web.py reads these when it's imported, so they must be set before any test imports it.
os.environ.setdefault("GOGKEEPER_PLATFORM", "w")
os.environ.setdefault("GOGKEEPER_LANGUAGE", "en")

from gogkeeper.db import open_db  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def cat():
    """A small library in the engine's gamedetails.json shape. Invented games only.

    star_pilot (1001): installer, extra, language pack, and a DLC, star_pilot_moons (1002).
    quiet_harbour (2001): one installer, no other lists, no changelog, no version.
    """
    return json.loads((FIXTURES / "gamedetails.json").read_text())


@pytest.fixture
def db(tmp_path):
    """A real, empty GogKeeper database, so the SQL is tested too."""
    conn = open_db(tmp_path / "gogkeeper.db")
    yield conn
    conn.close()


@pytest.fixture
def share(tmp_path):
    """An empty library folder, standing in for the share."""
    lib = tmp_path / "share"
    lib.mkdir()
    return lib


def put(share, rel, data=b"x"):
    """Create a file on the share at a path relative to the library."""
    target = share / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target
