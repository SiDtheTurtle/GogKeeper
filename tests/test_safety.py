"""Checks that protect the share and the GOG login."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

from gogkeeper import catalogue, purge, web

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a POSIX system")


# purge.safe_target: Purge only ever deletes plain files inside the library.

@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "library"
    (lib / "game").mkdir(parents=True)
    (lib / "game" / "setup.exe").write_text("x")
    (tmp_path / "outside.txt").write_text("x")
    return lib


def test_safe_target_allows_a_plain_file_inside(library):
    assert purge.safe_target(str(library), "game/setup.exe") == os.path.join(str(library), "game/setup.exe")


@pytest.mark.parametrize("rel", ["/etc/passwd", "../outside.txt", "game/../../outside.txt"])
def test_safe_target_refuses_paths_out(library, rel):
    assert purge.safe_target(str(library), rel) is None


def test_safe_target_refuses_folders_and_missing_files(library):
    assert purge.safe_target(str(library), "game") is None
    assert purge.safe_target(str(library), "game/missing.exe") is None


@posix_only
def test_safe_target_refuses_a_symlinked_file(library):
    (library / "game" / "link.exe").symlink_to(library.parent / "outside.txt")
    assert purge.safe_target(str(library), "game/link.exe") is None


@posix_only
def test_safe_target_refuses_a_symlinked_folder_leading_out(library):
    (library.parent / "elsewhere").mkdir()
    (library.parent / "elsewhere" / "file.exe").write_text("x")
    (library / "escape").symlink_to(library.parent / "elsewhere")
    assert purge.safe_target(str(library), "escape/file.exe") is None


# catalogue.safe_name: names from GOG can't point outside the library.

@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "/", "a\\b", "\\", None, 5])
def test_safe_name_refuses(name):
    with pytest.raises(ValueError):
        catalogue.safe_name(name, "folder")


def test_safe_name_allows_a_plain_name():
    assert catalogue.safe_name("the_witcher_3", "folder") == "the_witcher_3"


# web.login_code: only the address GOG lands you on is accepted.

def test_login_code_accepts_gogs_address():
    assert web.login_code("https://embed.gog.com/on_login_success?origin=client&code=Ab1-_.~") == "Ab1-_.~"


@pytest.mark.parametrize("address", [
    "http://embed.gog.com/on_login_success?code=abc",          # not https
    "https://evil.example/on_login_success?code=abc",          # another host
    "https://embed.gog.com.evil.example/on_login_success?code=abc",
    "https://embed.gog.com/elsewhere?code=abc",                # another path
    "https://embed.gog.com/on_login_success?code=a&code=b",    # repeated code
    "https://embed.gog.com/on_login_success",                  # no code
    "https://embed.gog.com/on_login_success?code=a%20b",       # bad characters
    "https://embed.gog.com/on_login_success?code=a<b>",
    "not an address",
])
def test_login_code_refuses(address):
    assert web.login_code(address) is None


# Cross-site protection: forms sent from another website are refused.

@pytest.mark.parametrize("path", ["/sync", "/maintenance", "/purge", "/login", "/login/code"])
def test_posts_from_another_site_are_refused(path):
    client = TestClient(web.app)
    response = client.post(path, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
