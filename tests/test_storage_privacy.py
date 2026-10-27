"""What is deleted or temporary must really be gone (P9, P10, G9)."""
import io
import json
import os
import sqlite3

from pypdf import PdfReader

from app import backup, db
from tests.test_app import add_vehicle, client, new_service, pdf_bytes  # noqa: F401


def db_bytes() -> bytes:
    return (db.DATA_DIR / "onderhoud.db").read_bytes()


# ---------- P9: secure_delete ----------

def test_deleted_vehicle_is_no_longer_in_the_database_file(client):
    vid = add_vehicle(client)
    with db.connect() as conn:
        conn.execute("UPDATE vehicle SET nickname = 'SecretNicknameXyz' WHERE id = ?", (vid,))
    assert b"SecretNicknameXyz" in db_bytes()
    client.post(f"/vehicles/{vid}/delete")
    assert b"SecretNicknameXyz" not in db_bytes()


def test_data_deleted_earlier_is_cleaned_up_once(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    old = sqlite3.connect(tmp_path / "onderhoud.db")  # as before P9: without secure_delete
    old.execute("PRAGMA secure_delete = OFF")
    old.execute("INSERT INTO vehicle (license_plate, vehicle_type, nickname) VALUES ('AB12CD', 'Overig', 'OldSecretXyz')")
    old.commit()
    old.execute("DELETE FROM vehicle")
    old.execute("DELETE FROM setting WHERE key = 'secure_delete_vacuumed'")
    old.commit()
    old.close()
    assert b"OldSecretXyz" in db_bytes()
    db.init()
    assert b"OldSecretXyz" not in db_bytes()


# ---------- P10: no file name in the sale PDF ----------

def test_error_page_does_not_name_the_original_file_name(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("factuur Jansen.pdf", pdf_bytes(), "application/pdf"))])
    export = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content))
    text = "".join(p.extract_text() for p in export.pages)
    assert "nog niet gecontroleerd" in text and "Jansen" not in text


# ---------- G9: leftovers of an interrupted restore ----------

def interrupted_restore(done: bool):
    """State after a crash in the middle of _swap: old folders aside, new ones placed, new database in."""
    data, tmp = db.DATA_DIR, db.DATA_DIR / backup.RESTORE_TMP
    tmp.mkdir()
    backup._copy_db(data / "onderhoud.db", tmp / "old-database.db")
    (tmp / "started").write_text(json.dumps(["facturen"]), encoding="utf-8")
    (data / "facturen").mkdir(exist_ok=True)
    os.replace(data / "facturen", data / ".oud-facturen")
    (data / "facturen").mkdir()
    (data / "facturen" / "from-backup.pdf").write_bytes(b"%PDF")
    (data / "documenten").mkdir()  # did not exist: only from the backup
    with db.connect() as conn:
        conn.execute("DELETE FROM vehicle")
    if done:
        (tmp / "done").touch()


def test_interrupted_restore_puts_back_the_old_state_at_startup(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    old_files = sorted(p.name for p in (db.DATA_DIR / "facturen").rglob("*.pdf"))
    interrupted_restore(done=False)
    backup.clean_up()
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM vehicle").fetchone()[0] == vid
    assert sorted(p.name for p in (db.DATA_DIR / "facturen").rglob("*.pdf")) == old_files
    assert not (db.DATA_DIR / "documenten").exists()
    assert not (db.DATA_DIR / ".oud-facturen").exists() and not (db.DATA_DIR / backup.RESTORE_TMP).exists()


def test_completed_restore_only_cleans_up_the_leftovers(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    interrupted_restore(done=True)
    backup.clean_up()
    assert (db.DATA_DIR / "facturen" / "from-backup.pdf").exists()
    assert not (db.DATA_DIR / ".oud-facturen").exists() and not (db.DATA_DIR / backup.RESTORE_TMP).exists()


def test_leftover_without_marker_is_never_thrown_away(client):
    (db.DATA_DIR / ".oud-documenten" / "1").mkdir(parents=True)
    (db.DATA_DIR / ".oud-documenten" / "1" / "polis.pdf").write_bytes(b"%PDF")
    backup.clean_up()
    assert (db.DATA_DIR / "documenten" / "1" / "polis.pdf").exists()
    assert not (db.DATA_DIR / ".oud-documenten").exists()


def test_failed_clean_up_does_not_stop_the_startup(client, monkeypatch):
    interrupted_restore(done=False)
    monkeypatch.setattr(backup, "_copy_db", lambda *a: (_ for _ in ()).throw(OSError("locked")))
    backup.clean_up()  # no error to the outside
    assert (db.DATA_DIR / backup.RESTORE_TMP / "old-database.db").exists()  # nothing thrown away: try again next start


def test_partial_backup_is_cleaned_up(client, tmp_path):
    add_vehicle(client)
    folder = tmp_path / "backups"
    folder.mkdir(exist_ok=True)
    (folder / "Garage-backup-2026-01-01_000000.zip.deel").write_bytes(b"half")
    (folder / "something-else.zip.deel").write_bytes(b"not from Garage")
    with db.connect() as conn:
        db.set_setting(conn, "backup_auto", "off")
    backup.automatic()
    assert not (folder / "Garage-backup-2026-01-01_000000.zip.deel").exists()
    assert (folder / "something-else.zip.deel").exists()


def test_restore_leaves_no_leftovers(client):
    add_vehicle(client)
    zip_path = backup.create()
    backup.restore(zip_path)
    leftovers = [p.name for p in db.DATA_DIR.iterdir() if p.name.startswith(".")]
    assert leftovers == []
