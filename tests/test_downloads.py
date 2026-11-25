"""Recording downloads: reading the engine's "Download complete" lines, and finding files it didn't report.

The line format is lgogdownloader's (v3.20, processDownloadQueue in src/downloader.cpp):
    <timestamp> [Thread #<n>] Download complete: <file name> (@ <rate>)
A failed transfer prints "Download complete (<error>): <file name>" instead.
"""
import pytest

from conftest import put
from gogkeeper import first_run, sync

LINE = "2026-Nov-07 12:00:00 [Thread #1] Download complete: {} (@ 50.2 MiB/s)"


class FakeEngine:
    """Stands in for subprocess.Popen: hands out the given output chunks, then exits with the code."""

    def __init__(self, chunks, code=0):
        self.chunks, self.code = list(chunks), code

    def __call__(self, *args, **kwargs):
        engine = self

        class Stdout:
            def read1(self, _size):
                return engine.chunks.pop(0) if engine.chunks else b""

        self.stdout = Stdout()
        return self

    def wait(self):
        return self.code


@pytest.fixture
def run_engine(db, share, monkeypatch):
    """Run sync.download() against the share with the engine faked out."""
    monkeypatch.setattr(first_run, "LIBRARY", share)

    def run(chunks, code=0):
        monkeypatch.setattr(sync.subprocess, "Popen", FakeEngine(chunks, code))
        sync.download(db, ["lgogdownloader"])
    return run


def row(db, product_id, path):
    return db.execute("SELECT status, downloaded_at, checksum_at, checksum_result FROM files"
                      " WHERE product_id = ? AND path = ? ORDER BY id DESC", (product_id, path)).fetchone()


def add_second_manual(cat):
    """Give quiet_harbour an extra with the same file name as star_pilot's."""
    cat["games"][1]["extras"] = [{"path": "/quiet_harbour/manual.pdf", "platform": 1, "language": 1}]


# The pattern.

@pytest.mark.parametrize("line, name", [
    (LINE.format("setup_star_pilot_1.0.exe"), "setup_star_pilot_1.0.exe"),
    (LINE.format("setup_game_(123).exe"), "setup_game_(123).exe"),   # brackets in the name
    (LINE.format("setup game 2.bin") + "  ", "setup game 2.bin"),     # spaces, trailing space
    ("2026-Nov-07 12:00:00 [Thread #3] Download complete: patch.zip (@ 812.0 KiB/s)", "patch.zip"),
])
def test_the_pattern_finds_the_file_name(line, name):
    assert sync.DOWNLOAD_COMPLETE.search(line).group(1) == name


@pytest.mark.parametrize("line", [
    "2026-Nov-07 12:00:00 [Thread #1] Download complete (Timeout was reached (28)): setup.exe",
    "2026-Nov-07 12:00:00 [Thread #1] Begin download: setup.exe",
    "\033[32m" + LINE.format("setup.exe") + "\033[0m",  # coloured output isn't matched
])
def test_the_pattern_ignores_other_lines(line):
    assert sync.DOWNLOAD_COMPLETE.search(line) is None


def test_the_engine_is_run_without_colour():
    # Coloured lines end in a colour code, so with colour on no download would ever be recorded.
    assert "--no-color" in sync.download_args("w", "en")


# Recording a finished file.

def test_a_unique_file_name_is_recorded_and_its_checksum_reset(db, cat, share, run_engine):
    first_run.import_catalogue(db, cat, share, 0)
    db.execute("UPDATE files SET checksum_at = 5, checksum_result = 'passed' WHERE path = 'setup_star_pilot_1.0.exe'")

    run_engine([(LINE.format("setup_star_pilot_1.0.exe") + "\n").encode()])

    status, downloaded_at, checksum_at, result = row(db, 1001, "setup_star_pilot_1.0.exe")
    assert (status, checksum_at, result) == ("downloaded", 0, "not_checked")
    assert downloaded_at > 0


def test_a_line_split_across_chunks_and_a_last_line_without_newline_are_read(db, cat, share, run_engine):
    first_run.import_catalogue(db, cat, share, 0)
    first = LINE.format("setup_star_pilot_1.0.exe").encode()
    last = LINE.format("setup_quiet_harbour.exe").encode()

    run_engine([b"progress\r" + first[:30], first[30:] + b"\r\n" + last[:10], last[10:]])

    assert row(db, 1001, "setup_star_pilot_1.0.exe")[0] == "downloaded"
    assert row(db, 2001, "setup_quiet_harbour.exe")[0] == "downloaded"


def test_with_a_shared_name_the_file_that_has_just_appeared_wins(db, cat, share, run_engine):
    add_second_manual(cat)
    first_run.import_catalogue(db, cat, share, 0)
    put(share, "quiet_harbour/extras/manual.pdf")

    run_engine([(LINE.format("manual.pdf") + "\n").encode()])

    assert row(db, 2001, "extras/manual.pdf")[0] == "downloaded"
    assert row(db, 1001, "extras/manual.pdf")[0] == "not_downloaded"


@pytest.mark.parametrize("on_share", [[], ["star_pilot", "quiet_harbour"]])
def test_with_a_shared_name_nothing_is_guessed(db, cat, share, run_engine, capsys, on_share):
    add_second_manual(cat)
    first_run.import_catalogue(db, cat, share, 0)
    for folder in on_share:
        put(share, f"{folder}/extras/manual.pdf")

    run_engine([(LINE.format("manual.pdf") + "\n").encode()])

    assert row(db, 1001, "extras/manual.pdf")[0] == "not_downloaded"
    assert row(db, 2001, "extras/manual.pdf")[0] == "not_downloaded"
    assert "could be any of 2 files" in capsys.readouterr().err


def test_an_unknown_file_name_is_not_recorded(db, cat, share, run_engine, capsys):
    first_run.import_catalogue(db, cat, share, 0)
    run_engine([(LINE.format("not_in_the_database.exe") + "\n").encode()])
    assert "matches no file" in capsys.readouterr().err
    assert db.execute("SELECT count(*) FROM files WHERE status = 'downloaded'").fetchone()[0] == 0


def test_an_engine_error_is_raised_after_recording_what_finished(db, cat, share, run_engine):
    first_run.import_catalogue(db, cat, share, 0)
    with pytest.raises(RuntimeError):
        run_engine([(LINE.format("setup_star_pilot_1.0.exe") + "\n").encode()], code=1)
    assert row(db, 1001, "setup_star_pilot_1.0.exe")[0] == "downloaded"


# Files the engine finished without reporting.

def test_found_files_are_marked_and_others_left(db, cat, share, monkeypatch):
    monkeypatch.setattr(first_run, "LIBRARY", share)
    put(share, "star_pilot/setup_star_pilot_1.0.exe")  # downloaded at the first run
    first_run.import_catalogue(db, cat, share, 0)
    put(share, "star_pilot/dlc/star_pilot_moons/extras/moons_map.pdf")  # arrived unreported

    assert sync.record_found_files(db, 7) == 1
    assert row(db, 1002, "extras/moons_map.pdf") == ("downloaded", 7, 0, "not_checked")
    assert row(db, 1001, "setup_star_pilot_1.0.exe")[:2] == ("downloaded", 0)  # left as it was
    assert row(db, 2001, "setup_quiet_harbour.exe")[0] == "not_downloaded"
