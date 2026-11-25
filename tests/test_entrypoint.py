"""The entrypoint's guards, which stop lgogdownloader writing outside the share or deleting files.

Every case here exits before the script touches the database, so no Docker is needed.
"""
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).parent.parent / "docker-entrypoint.sh"
FULL = ["--directory", "/library", "--platform", "w", "--language", "en"]


def run(*args):
    return subprocess.run(["bash", str(ENTRYPOINT), *args], capture_output=True, text=True)


@pytest.mark.parametrize("arg", ["--delete-orphans", "--del", "--delete"])
def test_delete_orphans_is_refused(arg):
    result = run(arg)
    assert result.returncode == 2
    assert "blocked" in result.stderr


@pytest.mark.parametrize("missing", ["--directory", "--platform", "--language"])
def test_download_needs_each_core_parameter(missing):
    args = FULL + ["--include", "i"]
    i = args.index(missing)
    del args[i:i + 2]
    result = run("--download", *args)
    assert result.returncode == 2
    assert missing in result.stderr


def test_download_needs_include_or_exclude():
    result = run("--download", *FULL)
    assert result.returncode == 2
    assert "--include or --exclude" in result.stderr


@pytest.mark.parametrize("arg", ["--down", "--repair", "--status"])
def test_file_commands_need_core_parameters(arg):
    assert run(arg).returncode == 2


@pytest.mark.parametrize("directory", ["/tmp", "/", "library", "/library-other",
                                       "/library/..", "/library/../etc", "/library/a/../../etc"])
def test_directory_must_stay_inside_library(directory):
    result = run("--repair", "--directory", directory, "--platform", "w", "--language", "en")
    assert result.returncode == 2


def test_directory_with_equals_sign_is_checked():
    result = run("--repair", "--directory=/tmp", "--platform", "w", "--language", "en")
    assert result.returncode == 2
