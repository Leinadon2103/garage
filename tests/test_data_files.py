"""Data files only through db.read_file / write_file / delete_file (the gate for P1), and OCR without a
temporary file (P8)."""
import re
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import backup, db, ocr, web
from tests.test_app import add_vehicle, client, new_service, pdf_bytes  # noqa: F401

APP = Path(__file__).resolve().parent.parent / "app"


def test_tests_never_use_the_real_data_folder():
    """conftest.py: without a test's own folder, db.DATA_DIR is a throwaway folder, never GARAGE_DATA of this pc."""
    assert db.DATA_DIR.name.startswith("garage-tests-")


def test_backups_of_test_data_go_to_their_own_folder(monkeypatch, tmp_path):
    """start.bat sets GARAGE_BACKUPS: otherwise the test data is backed up between the real backups in OneDrive,
    and with at most 10 kept, it would push out a real one."""
    monkeypatch.setenv("GARAGE_BACKUPS", str(tmp_path))
    assert backup.default_folder() == tmp_path


def test_only_db_and_backup_touch_the_data_folder():
    """Everything else goes through db.read_file/write_file/delete_file, so encryption (P1) cannot be bypassed.
    backup.py zips and restores the folder as a whole; the files go in as they are."""
    offenders = [f"{p.name}:{nr}" for p in APP.glob("*.py") if p.name not in ("db.py", "backup.py")
                 for nr, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if re.search(r"DATA_DIR|data_file\(", line)]
    assert offenders == []


def test_write_file_is_atomic(client, monkeypatch):
    db.write_file("facturen/1/a.pdf", b"old")
    monkeypatch.setattr(db.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        db.write_file("facturen/1/a.pdf", b"new")
    assert db.read_file("facturen/1/a.pdf") == b"old"  # never half a file under the real name
    assert [p.name for p in (db.DATA_DIR / "facturen" / "1").iterdir()] == ["a.pdf"]
    with pytest.raises(ValueError):
        db.write_file("../buiten.pdf", b"x")
    with pytest.raises(FileNotFoundError):
        db.read_file("facturen/1/missing.pdf")


def test_half_written_files_are_cleaned_up_and_not_backed_up(client, tmp_path):
    add_vehicle(client)
    db.write_file("documenten/1/a.pdf", pdf_bytes())
    (db.DATA_DIR / "documenten" / "1" / "b.pdf.deel").write_bytes(b"half")  # e.g. the pc switched off
    with zipfile.ZipFile(backup.create()) as z:
        assert z.namelist().count("documenten/1/a.pdf") == 1 and not any(n.endswith(".deel") for n in z.namelist())
    backup.clean_up()
    assert [p.name for p in (db.DATA_DIR / "documenten" / "1").iterdir()] == ["a.pdf"]


def test_invoice_opens_inline_under_any_original_name(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("factuur été.pdf", pdf_bytes(), "application/pdf"))])
    with db.connect() as conn:
        iid, path = conn.execute("SELECT id, path FROM invoice").fetchone()
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == "inline; filename*=utf-8''factuur%20%C3%A9t%C3%A9.pdf"
    # quotes or a line break in a (restored) name cannot break or add a header
    tricky = web.file_response(path, 'a"b\r\nX-Evil: 1.pdf')
    assert tricky.headers["content-disposition"] == "inline; filename*=utf-8''a%22b%0D%0AX-Evil%3A%201.pdf"
    with db.connect() as conn:
        db.delete_file(conn.execute("SELECT path FROM invoice").fetchone()[0])
    assert client.get(f"/invoices/{iid}").status_code == 404


@pytest.mark.skipif(not ocr.available(), reason="Windows OCR not available")
def test_ocr_writes_no_temporary_file(monkeypatch):
    """A temporary image of an invoice would stay behind after a crash; the image goes through stdin."""
    import tempfile

    def refuse(*args, **kwargs):
        raise AssertionError("OCR must not write a temporary file")
    for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryDirectory"):
        monkeypatch.setattr(tempfile, name, refuse)
    ocr._cache.clear()
    im = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(im).text((50, 120), "Totaal 54,00", fill="black", font_size=48)
    assert any("54" in w["text"] for w in ocr.recognize(im))  # OCR really ran
