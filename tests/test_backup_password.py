"""Backup with a password (P2) and recovering a lost key: the backup is encrypted as a whole, can be restored on
another pc with its password, and the password brings the key back after a Windows reinstall."""
import json
import zipfile

import pytest

from app import backup, crypto, db
from tests.test_app import add_vehicle, client, new_service, pdf_bytes  # noqa: F401

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    """The real scrypt settings take seconds per call; test_crypto.py checks those. Stored with every wrapped
    key, so a lighter setting here works the same way."""
    monkeypatch.setattr(crypto, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})


def set_password(client, password=PASSWORD, repeat=None, understood="1"):
    return client.post("/backup/password", data={"password": password, "repeat": repeat or password,
                                                 "understood": understood}, follow_redirects=False)


def other_pc(monkeypatch, tmp_path):
    """A fresh installation elsewhere: its own empty data folder, so its own new master key."""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "other-pc")
    db.init()


def test_backup_is_encrypted_as_a_whole(client):
    add_vehicle(client, plate="AA-11-BB")
    with zipfile.ZipFile(backup.create()) as z:
        info = json.loads(z.read(backup.INFO))
        assert b"AA11BB" not in z.read(db.DB_NAME) and crypto.is_encrypted(z.read(db.DB_NAME))
    assert info["format"] == 2 and info["fingerprint"] == crypto.fingerprint(db.master_key())
    assert info["master_key"] is None  # no password yet: only this pc can open it
    assert set_password(client).status_code == 303
    with zipfile.ZipFile(backup.create()) as z:
        raw = z.read(backup.INFO).decode() + z.read(db.DB_NAME).hex()
        info = json.loads(z.read(backup.INFO))
    assert PASSWORD not in raw and crypto.unwrap_with_password(info["master_key"], PASSWORD) == db.master_key()


@pytest.mark.parametrize("password, repeat, understood, message", [
    ("kort", "kort", "1", "minstens 12"),
    (PASSWORD, PASSWORD + "x", "1", "niet gelijk"),
    (PASSWORD, PASSWORD, "", "begrijpt"),
])
def test_password_refused(client, password, repeat, understood, message):
    r = set_password(client, password, repeat, understood)
    assert r.status_code == 400 and message in r.text
    with db.connect() as conn:
        assert not db.setting(conn, "master_key_backup")


def test_warning_until_a_password_is_set(client):
    add_vehicle(client)
    assert "Stel een back-upwachtwoord in" in client.get("/").text
    assert "nog geen back-upwachtwoord" in client.get("/settings").text
    set_password(client)
    assert "Stel een back-upwachtwoord in" not in client.get("/").text
    assert "Er is een back-upwachtwoord ingesteld" in client.get("/settings").text


def test_restore_on_another_pc_with_the_password(client, monkeypatch, tmp_path):
    vid = add_vehicle(client, plate="AA-11-BB")
    new_service(client, vid, files=[("invoices", ("factuur.pdf", pdf_bytes(), "application/pdf"))])
    set_password(client)
    made, original_key = backup.create(), db.master_key()
    with db.connect() as conn:
        rel = conn.execute("SELECT path FROM invoice").fetchone()["path"]
    content = db.read_file(rel)

    other_pc(monkeypatch, tmp_path)
    assert db.master_key() != original_key
    with pytest.raises(backup.PasswordNeeded, match="andere pc"):
        backup.restore(made)
    with pytest.raises(backup.PasswordNeeded, match="Verkeerd wachtwoord"):
        backup.restore(made, "wrong horse battery")
    safety = backup.restore(made, PASSWORD)
    assert db.master_key() == original_key  # the key of the backup is now the key here, under DPAPI
    assert crypto.load_master(db.DATA_DIR) == original_key
    assert db.read_file(rel) == content
    with db.connect() as conn:
        assert conn.execute("SELECT license_plate FROM vehicle").fetchone()[0] == "AA11BB"
        assert crypto.unwrap_with_password(db.setting(conn, "master_key_backup"), PASSWORD) == original_key
    assert safety.exists()


def test_restore_from_the_list_asks_for_the_password(client, monkeypatch, tmp_path):
    add_vehicle(client)
    set_password(client)
    made = backup.create()
    other_pc(monkeypatch, tmp_path)
    with db.connect() as conn:
        db.set_setting(conn, "backup_folder", str(made.parent))
    r = client.post("/backup/restore", data={"name": made.name})
    assert r.status_code == 400 and "andere pc" in r.text and 'name="password"' in r.text
    r = client.post("/backup/restore", data={"name": made.name, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and "restored=" in r.headers["location"]
    r = client.post("/backup/restore-file", files={"file": ("b.zip", made.read_bytes(), "application/zip")},
                    follow_redirects=False)
    assert r.status_code == 303  # the key of that backup is the key here now: no password needed any more


def test_backup_without_password_only_on_this_pc(client, monkeypatch, tmp_path):
    add_vehicle(client)
    made = backup.create()
    other_pc(monkeypatch, tmp_path)
    with pytest.raises(backup.BackupError, match="zonder back-upwachtwoord"):
        backup.restore(made, PASSWORD)


def test_older_backup_keeps_the_current_password(client):
    add_vehicle(client)
    older = backup.create()  # made before the password was set
    set_password(client)
    backup.restore(older)
    with db.connect() as conn:
        assert crypto.unwrap_with_password(db.setting(conn, "master_key_backup"), PASSWORD) == db.master_key()


def test_lost_key_is_recovered_with_the_password(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("factuur.pdf", pdf_bytes(), "application/pdf"))])
    set_password(client)
    with db.connect() as conn:
        iid = conn.execute("SELECT id FROM invoice").fetchone()["id"]
    (db.DATA_DIR / crypto.KEY_FILE).unlink()  # e.g. Windows reinstalled: DPAPI cannot open it any more
    db.reset_master_key()

    for path in ("/", f"/vehicles/{vid}", f"/invoices/{iid}", "/settings"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/recover", path
    assert client.post(f"/vehicles/{vid}/delete", follow_redirects=False).headers["location"] == "/recover"
    page = client.get("/recover").text
    assert "Back-upwachtwoord" in page and "Ontgrendelen" in page
    r = client.post("/recover", data={"password": "wrong horse battery"})
    assert r.status_code == 400 and "Verkeerd wachtwoord" in r.text
    r = client.post("/recover", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get(f"/invoices/{iid}").content.startswith(b"%PDF")  # everything readable again
    assert client.get(f"/vehicles/{vid}").status_code == 200  # and nothing was deleted
    assert client.get("/recover", follow_redirects=False).headers["location"] == "/"


def test_lost_key_without_password_explains_and_deletes_nothing(client):
    add_vehicle(client)
    (db.DATA_DIR / crypto.KEY_FILE).unlink()
    db.reset_master_key()
    page = client.get("/recover").text
    assert "nooit een back-upwachtwoord" in page and "Ontgrendelen" not in page
    assert client.post("/recover", data={"password": PASSWORD}).status_code == 400
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM vehicle").fetchone()[0] == 1
    assert not (db.DATA_DIR / crypto.KEY_FILE).exists()  # and no new key next to the encrypted data
