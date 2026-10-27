"""Schema versions and migrations (PRAGMA user_version)."""
import json
import sqlite3
import zipfile

import pytest

from app import backup, db
from tests.test_app import add_vehicle, client  # noqa: F401

# Old database from before migrations: no user_version, no columns and tables that were added later.
# Names are Dutch, as they were then.
SCHEMA_1_2 = """
CREATE TABLE voertuig (id INTEGER PRIMARY KEY, kenteken TEXT NOT NULL, voertuigsoort TEXT NOT NULL,
    merk TEXT, model TEXT, datum_eerste_toelating TEXT, kleur TEXT, cilinderinhoud INTEGER, bijnaam TEXT);
CREATE TABLE onderhoudstype (id INTEGER PRIMARY KEY, naam TEXT NOT NULL, voertuigsoorten TEXT NOT NULL DEFAULT '*',
    interval_km INTEGER, interval_maanden INTEGER);
CREATE TABLE beurt (id INTEGER PRIMARY KEY, voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    datum TEXT NOT NULL, kilometerstand INTEGER, uitgevoerd_door TEXT, kosten REAL, opmerking TEXT);
CREATE TABLE factuur (id INTEGER PRIMARY KEY, beurt_id INTEGER NOT NULL REFERENCES beurt(id) ON DELETE CASCADE,
    pad TEXT NOT NULL, originele_naam TEXT NOT NULL, mime_type TEXT NOT NULL);
INSERT INTO voertuig (kenteken, voertuigsoort, merk) VALUES ('MD12AB', 'Motorfiets', 'Honda');
INSERT INTO beurt (voertuig_id, datum, kilometerstand) VALUES (1, '2024-05-01', 12000);
INSERT INTO factuur (beurt_id, pad, originele_naam, mime_type) VALUES (1, 'facturen/1/a.pdf', 'a.pdf', 'application/pdf');
"""


@pytest.fixture
def empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    return tmp_path / "onderhoud.db"


def columns(table):
    with db.connect() as conn:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def old_database(path, extra_sql=""):
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_1_2 + extra_sql)
    old.close()


def test_new_database_gets_latest_version(empty):
    db.init()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
    for old_table, (table, renamed) in db.RENAMES_2.items():
        assert set(renamed.values()) <= columns(table)


def test_database_from_before_migrations_is_updated(empty):
    old_database(empty)
    db.init()
    db.init()  # second time: nothing left to do
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT odometer_km FROM service").fetchone()[0] == 12000
        assert conn.execute("SELECT reviewed, original_path FROM invoice").fetchone()[:] == (0, None)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'storage_period'").fetchone()


def test_migration_2_converts_names_and_internal_values(empty):
    old_database(empty, """
        CREATE TABLE instelling (sleutel TEXT PRIMARY KEY, waarde TEXT);
        INSERT INTO instelling VALUES ('thema', 'donker'), ('herinneringen', 'uit'), ('prive_termen', 'aan'),
                                      ('melding_laatst', '{"datum": "2026-01-01"}'), ('backup_map', 'D:\\Back-ups');
        UPDATE voertuig SET bijnaam = 'De Groene';
        ALTER TABLE voertuig ADD COLUMN beeld_type TEXT;
        ALTER TABLE voertuig ADD COLUMN rdw_json TEXT;
        UPDATE voertuig SET beeld_type = 'foto', rdw_json = '{"apk_vervaldatum": "2027-01-01", "brandstof": "Benzine"}';
        ALTER TABLE factuur ADD COLUMN boxen TEXT;
        UPDATE factuur SET boxen = '[{"pagina": 0, "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}]';
    """)
    db.init()
    with db.connect() as conn:
        settings = dict(conn.execute("SELECT key, value FROM setting").fetchall())
        v = conn.execute("SELECT * FROM vehicle").fetchone()
        redactions = json.loads(conn.execute("SELECT redactions FROM invoice").fetchone()[0])
        fk = conn.execute("SELECT * FROM pragma_foreign_key_list('service')").fetchone()
    assert settings["theme"] == "dark" and settings["reminders"] == "off" and settings["backup_folder"] == "D:\\Back-ups"
    assert settings["private_terms"] == "aan"  # the user's own text is never 'translated'
    assert "last_notification" not in settings  # only limits repeats; rebuilds itself
    assert v["nickname"] == "De Groene" and v["image_type"] == "photo" and v["vehicle_type"] == "Motorfiets"
    assert json.loads(v["rdw_json"]) == {"apk_expiry_date": "2027-01-01", "fuel": "Benzine"}
    assert redactions == [{"page": 0, "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}]
    assert fk["table"] == "vehicle"  # foreign keys follow the renamed table


def test_failed_migration_is_rolled_back(empty, monkeypatch):
    db.init()

    def broken(conn):
        conn.execute("CREATE TABLE half (id INTEGER)")
        conn.execute("ALTER TABLE vehicle ADD COLUMN half TEXT")
        raise RuntimeError("halfway wrong")

    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, broken])
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(db.MIGRATIONS))
    with pytest.raises(RuntimeError):
        db.init()
    with db.connect() as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS) - 1
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone()
    assert "half" not in columns("vehicle")


def test_data_of_a_newer_version_is_not_touched(empty):
    db.init()
    with db.connect() as conn:
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    with pytest.raises(db.SchemaError, match="nieuwere versie"):
        db.init()


def test_backup_of_a_newer_version_refused(client, tmp_path):
    add_vehicle(client)
    zip_path = backup.create()
    with zipfile.ZipFile(zip_path) as z:
        z.extract("onderhoud.db", tmp_path / "out")
    newer = sqlite3.connect(tmp_path / "out" / "onderhoud.db")
    newer.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    newer.close()
    with zipfile.ZipFile(tmp_path / "newer.zip", "w") as z:
        z.write(tmp_path / "out" / "onderhoud.db", "onderhoud.db")
    with pytest.raises(backup.BackupError, match="nieuwere versie"):
        backup.restore(tmp_path / "newer.zip")


def test_failed_migration_during_restore_leaves_everything_as_it_was(client, tmp_path, monkeypatch):
    vid = add_vehicle(client, plate="ZZ-99-ZZ")
    old_database(tmp_path / "onderhoud.db")
    with zipfile.ZipFile(tmp_path / "old.zip", "w") as z:
        z.write(tmp_path / "onderhoud.db", "onderhoud.db")

    real_migrate = db.migrate
    calls = []

    def migrate_fails_the_second_time(conn):  # 1st: checking the backup copy; 2nd: the live database
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("migration failed")
        return real_migrate(conn)

    monkeypatch.setattr(db, "migrate", migrate_fails_the_second_time)
    with pytest.raises(backup.BackupError, match="oude gegevens zijn teruggezet"):
        backup.restore(tmp_path / "old.zip")
    monkeypatch.setattr(db, "migrate", real_migrate)
    with db.connect() as conn:
        assert conn.execute("SELECT license_plate FROM vehicle WHERE id = ?", (vid,)).fetchone()[0] == "ZZ99ZZ"
        assert db.schema_version(conn) == db.SCHEMA_VERSION


def test_restoring_an_old_backup_migrates(client, tmp_path):
    old_database(tmp_path / "onderhoud.db")
    with zipfile.ZipFile(tmp_path / "old.zip", "w") as z:
        z.write(tmp_path / "onderhoud.db", "onderhoud.db")
    backup.restore(tmp_path / "old.zip")
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT make FROM vehicle").fetchone()[0] == "Honda"
    assert "rdw_json" in columns("vehicle")
