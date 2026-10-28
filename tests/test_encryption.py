"""Encryption of the data files and sensitive fields (P1): on disk, in the database, when deleting and when
migrating existing data. Design: docs/encryption.md."""
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import backup, crypto, db
from app.main import app
from tests.test_app import add_vehicle, client, new_service, pdf_bytes, review_all  # noqa: F401


def invoice():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM invoice ORDER BY id").fetchone()


def raw_db() -> bytes:
    return (db.DATA_DIR / db.DB_NAME).read_bytes()


def make_plain(rel: str, content: bytes) -> None:
    """A data file as before P1: readable on disk, without a key."""
    with db.connect() as conn:
        conn.execute("DELETE FROM file_key WHERE path = ?", (rel,))
    (db.DATA_DIR / rel).write_bytes(content)


def test_files_and_fields_are_encrypted_but_work_as_before(client):
    vid = add_vehicle(client)
    client.post("/settings/private", data={"terms": "Jan Jansenius\nDorpsstraat 12"})
    new_service(client, vid, files=[("invoices", ("factuur Jansenius.pdf", pdf_bytes(), "application/pdf"))])
    review_all(client)
    f = invoice()
    for rel in (f["path"], f["original_path"]):
        on_disk = (db.DATA_DIR / rel).read_bytes()
        assert crypto.is_encrypted(on_disk) and b"%PDF" not in on_disk
    assert client.get(f"/invoices/{f['id']}").content.startswith(b"%PDF")
    assert client.get(f"/invoices/{f['id']}/original").content.startswith(b"%PDF")

    # name and redaction terms: encrypted in the database, readable in the app
    assert f["original_filename"].startswith(db.TEXT_PREFIX)
    assert b"Jansenius" not in raw_db() and b"Dorpsstraat" not in raw_db()
    assert "factuur Jansenius.pdf" in client.get(f"/vehicles/{vid}").text
    assert "Dorpsstraat 12" in client.get("/settings").text
    with db.connect() as conn:
        assert db.private_terms(conn) == ["Jan Jansenius", "Dorpsstraat 12"]
        assert conn.execute("SELECT COUNT(*) FROM file_key").fetchone()[0] == 2  # original + redacted


def test_deleting_erases_the_key_first(client, monkeypatch):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    f = invoice()
    with monkeypatch.context() as m:  # not monkeypatch.undo(): that would also undo the data folder of `client`
        m.setattr(Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(PermissionError("open in a PDF reader")))
        client.post(f"/invoices/{f['id']}/delete")
    assert (db.DATA_DIR / f["path"]).exists()  # could not be deleted ...
    with pytest.raises(crypto.CryptoError):  # ... but without its key it is unreadable
        db.read_file(f["path"])
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_key").fetchone()[0] == 0


def test_existing_data_is_encrypted_at_startup_after_a_backup(client, tmp_path):
    """Data from before P1: readable files and fields. The next start makes a backup first, then encrypts."""
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("factuur Pietersma.pdf", pdf_bytes(), "application/pdf"))])
    f, content = invoice(), pdf_bytes()
    make_plain(f["path"], content)
    (db.DATA_DIR / "documenten" / "1").mkdir(parents=True)
    (db.DATA_DIR / "documenten" / "1" / "orphan.pdf").write_bytes(b"%PDF orphan")  # file without a database row
    with db.connect() as conn:
        conn.execute("UPDATE invoice SET original_filename = 'factuur Pietersma.pdf'")
        db.set_setting(conn, "private_terms", "Piet Pietersma")
        db.set_setting(conn, "backup_last", "")
    assert db.needs_encryption()

    with TestClient(app):  # the next start
        app.state.backup_thread.join()
    [made] = backup.list_backups(tmp_path / "backups")
    with zipfile.ZipFile(made["path"]) as z:  # the backup still has the state from before
        assert z.read(f["path"]).startswith(b"%PDF")
    assert not db.needs_encryption()
    for rel in (f["path"], "documenten/1/orphan.pdf"):
        assert crypto.is_encrypted((db.DATA_DIR / rel).read_bytes())
    assert db.read_file(f["path"]) == content and db.read_file("documenten/1/orphan.pdf") == b"%PDF orphan"
    assert b"Pietersma" not in raw_db()
    with db.connect() as conn:
        assert db.private_terms_text(conn) == "Piet Pietersma"
    # once: OneDrive keeps the readable old versions for a while
    assert "OneDrive bewaart oude versies" in client.get("/").text
    client.post("/notice/encrypted")
    assert "OneDrive bewaart oude versies" not in client.get("/").text


def test_interrupted_encryption_continues_with_the_stored_key(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    rel = invoice()["path"]
    with db.connect() as conn:
        key_row = conn.execute("SELECT key FROM file_key WHERE path = ?", (rel,)).fetchone()["key"]
    content = pdf_bytes()
    (db.DATA_DIR / rel).write_bytes(content)  # crash after storing the key, before replacing the file
    assert db.encrypt_existing() == 1
    with db.connect() as conn:
        assert conn.execute("SELECT key FROM file_key WHERE path = ?", (rel,)).fetchone()["key"] == key_row
    assert db.read_file(rel) == content


@pytest.mark.parametrize("problem", ["key file gone", "key file of other data"])
def test_never_a_new_key_next_to_encrypted_data(client, problem):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    key_file = db.DATA_DIR / crypto.KEY_FILE
    if problem == "key file gone":
        key_file.unlink()
    else:
        crypto.save_master(db.DATA_DIR, crypto.new_key())
    before = key_file.read_bytes() if key_file.exists() else None
    db.init()  # e.g. the next start
    with pytest.raises(crypto.CryptoError):
        db.master_key()
    assert (key_file.read_bytes() if key_file.exists() else None) == before  # nothing overwritten or made
    assert not db.needs_encryption() and db.encrypt_existing() == 0
    with pytest.raises(crypto.CryptoError):
        db.write_file("facturen/1/new.pdf", pdf_bytes())  # never a readable file instead
    assert not (db.DATA_DIR / "facturen" / "1" / "new.pdf").exists()


def test_restoring_a_backup_from_before_encryption_encrypts_it(client, tmp_path):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    rel, content = invoice()["path"], pdf_bytes()
    make_plain(rel, content)
    old = backup.create()  # like a backup made with version 1.3
    db.encrypt_existing()
    backup.restore(old)
    assert crypto.is_encrypted((db.DATA_DIR / rel).read_bytes()) and db.read_file(rel) == content


def test_key_file_is_not_in_the_backup(client):
    add_vehicle(client)
    with zipfile.ZipFile(backup.create()) as z:
        assert crypto.KEY_FILE not in z.namelist()  # worthless on another pc; the password-wrapped key is (P2)
