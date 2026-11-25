"""Checksums: which files are checked, the check itself, and copying GOG's checksum from its XML."""
import hashlib

from conftest import put
from gogkeeper import checksums, first_run, sync

INSTALLER = "star_pilot/setup_star_pilot_1.0.exe"
CONTENT = b"star pilot installer"
MD5 = hashlib.md5(CONTENT).hexdigest()

# GOG's XML has an "available" attribute; the engine's own (Util::createXML) has only
# name, chunks, total_size and md5.
GOG_XML = '<file name="{name}" available="1" notavailablemsg="" md5="{md5}" chunks="1" total_size="20"></file>'
ENGINE_XML = '<file name="{name}" chunks="1" total_size="20" md5="{md5}"></file>'


def row(db, path):
    return db.execute("SELECT gog_md5, checksum_at, checksum_result FROM files WHERE path = ?", (path,)).fetchone()


def checkable(db, sql):
    return [r[1] for r in db.execute(sql)]


def setup_downloaded(db, cat, share):
    """Every file on the share at the first run, with GOG's checksum known for the base installer."""
    target = put(share, INSTALLER, CONTENT)
    put(share, "star_pilot/extras/manual.pdf")
    put(share, "quiet_harbour/setup_quiet_harbour.exe")
    first_run.import_catalogue(db, cat, share, 0)
    db.execute("UPDATE files SET gog_md5 = ? WHERE path = 'setup_star_pilot_1.0.exe'", (MD5,))
    return target


# Which files are checked.

def test_only_downloaded_files_with_gogs_checksum_are_checked(db, cat, share):
    setup_downloaded(db, cat, share)
    db.execute("UPDATE files SET gog_md5 = ? WHERE path = 'setup_quiet_harbour.exe'", ("a" * 32,))
    db.execute("UPDATE files SET status = 'expired' WHERE path = 'setup_quiet_harbour.exe'")
    # Left out: the extra (gog_md5 'none'), files still 'unknown' or not downloaded, and expired ones.
    assert checkable(db, checksums.NEW_FILES_SQL) == ["setup_star_pilot_1.0.exe"]


def test_the_new_file_run_skips_checked_files_and_maintenance_does_not(db, cat, share):
    setup_downloaded(db, cat, share)
    db.execute("UPDATE files SET checksum_at = 5, checksum_result = 'passed'")
    assert checkable(db, checksums.NEW_FILES_SQL) == []
    assert checkable(db, checksums.MAINTENANCE_SQL) == ["setup_star_pilot_1.0.exe"]


# The check.

def test_a_matching_file_passes(db, cat, share):
    setup_downloaded(db, cat, share)
    counts = checksums.check(db, db.execute(checksums.NEW_FILES_SQL).fetchall(), share)
    assert counts == {"passed": 1, "failed": 0, "missing": 0}
    _md5, checked_at, result = row(db, "setup_star_pilot_1.0.exe")
    assert result == "passed" and checked_at > 0


def test_a_changed_file_fails(db, cat, share):
    setup_downloaded(db, cat, share).write_bytes(b"changed on the NAS")
    counts = checksums.check(db, db.execute(checksums.NEW_FILES_SQL).fetchall(), share)
    assert counts == {"passed": 0, "failed": 1, "missing": 0}
    assert row(db, "setup_star_pilot_1.0.exe")[2] == "failed"


def test_a_missing_file_is_counted_with_no_date(db, cat, share, capsys):
    setup_downloaded(db, cat, share).unlink()
    counts = checksums.check(db, db.execute(checksums.NEW_FILES_SQL).fetchall(), share)
    assert counts == {"passed": 0, "failed": 0, "missing": 1}
    assert row(db, "setup_star_pilot_1.0.exe") == (MD5, 0, "not_checked")
    assert "isn't on the share" in capsys.readouterr().err


# GOG's checksum from its XML.

def test_gogs_xml_is_trusted(tmp_path):
    xml = tmp_path / "a.xml"
    xml.write_text(GOG_XML.format(name="a.exe", md5=MD5.upper()))
    assert sync.gog_md5_from_xml(xml) == MD5


def test_the_engines_own_xml_is_not_trusted(tmp_path):
    xml = tmp_path / "a.xml"
    xml.write_text(ENGINE_XML.format(name="a.exe", md5=MD5))
    assert sync.gog_md5_from_xml(xml) is None


def test_xml_without_a_usable_md5_or_root_is_not_trusted(tmp_path):
    short = tmp_path / "short.xml"
    short.write_text(GOG_XML.format(name="a.exe", md5="abc123"))
    other_root = tmp_path / "root.xml"
    other_root.write_text(f'<files available="1" md5="{MD5}"></files>')
    assert sync.gog_md5_from_xml(short) is None
    assert sync.gog_md5_from_xml(other_root) is None


def test_unreadable_xml_is_skipped_with_a_warning(tmp_path, capsys):
    broken = tmp_path / "broken.xml"
    broken.write_text("<file available=")
    assert sync.gog_md5_from_xml(broken) is None
    assert sync.gog_md5_from_xml(tmp_path / "missing.xml") is None
    assert capsys.readouterr().err.count("warning: can't read") == 2


def test_fill_checksums_copies_gogs_checksum_for_games_and_dlc(db, cat, share, tmp_path):
    first_run.import_catalogue(db, cat, share, 0)
    xml_root = tmp_path / "xml"
    dlc_md5 = "b" * 32
    for folder, name, md5 in [("star_pilot", "setup_star_pilot_1.0.exe", MD5),
                              ("star_pilot_moons", "setup_star_pilot_moons_1.0.exe", dlc_md5),  # the DLC's own name
                              ("quiet_harbour", "setup_quiet_harbour.exe", "c" * 32)]:
        put(xml_root, f"{folder}/{name}.xml", GOG_XML.format(name=name, md5=md5).encode())
    put(xml_root, "star_pilot/manual.pdf.xml", GOG_XML.format(name="manual.pdf", md5="d" * 32).encode())
    db.execute("UPDATE files SET status = 'expired' WHERE path = 'setup_quiet_harbour.exe'")

    assert sync.fill_checksums(db, xml_root) == 2
    assert row(db, "setup_star_pilot_1.0.exe")[0] == MD5
    assert row(db, "setup_star_pilot_moons_1.0.exe")[0] == dlc_md5
    assert row(db, "setup_quiet_harbour.exe")[0] == "unknown"  # expired: left alone
    assert row(db, "extras/manual.pdf")[0] == "none"            # extras have no checksum from GOG
