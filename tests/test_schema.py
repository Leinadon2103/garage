"""Schemaversies en migraties (PRAGMA user_version)."""
import sqlite3
import zipfile

import pytest

from app import backup, db
from tests.test_app import client, voeg_voertuig_toe  # noqa: F401

# Oude database van vóór de migraties: zonder user_version, zonder later toegevoegde kolommen en tabellen
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
def leeg(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    return tmp_path / "onderhoud.db"


def kolommen(tabel):
    with db.connect() as conn:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({tabel})")}


def test_nieuwe_database_krijgt_laatste_versie(leeg):
    db.init()
    with db.connect() as conn:
        assert db.schemaversie(conn) == db.SCHEMAVERSIE
    for tabel, extra in db.KOLOMMEN_1.items():
        assert set(extra) <= kolommen(tabel)


def test_database_van_voor_de_migraties_wordt_bijgewerkt(leeg):
    oud = sqlite3.connect(leeg)
    oud.executescript(SCHEMA_1_2)
    oud.close()
    db.init()
    db.init()  # tweede keer: niets meer te doen
    with db.connect() as conn:
        assert db.schemaversie(conn) == db.SCHEMAVERSIE
        assert conn.execute("SELECT kilometerstand FROM beurt").fetchone()[0] == 12000
        assert conn.execute("SELECT gecontroleerd, origineel_pad FROM factuur").fetchone()[:] == (0, None)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'stalling'").fetchone()


def test_mislukte_migratie_wordt_teruggedraaid(leeg, monkeypatch):
    db.init()

    def kapot(conn):
        conn.execute("CREATE TABLE half (id INTEGER)")
        conn.execute("ALTER TABLE voertuig ADD COLUMN half TEXT")
        raise RuntimeError("halverwege mis")

    monkeypatch.setattr(db, "MIGRATIES", [*db.MIGRATIES, kapot])
    monkeypatch.setattr(db, "SCHEMAVERSIE", len(db.MIGRATIES))
    with pytest.raises(RuntimeError):
        db.init()
    with db.connect() as conn:
        assert db.schemaversie(conn) == len(db.MIGRATIES) - 1
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone()
    assert "half" not in kolommen("voertuig")


def test_gegevens_van_nieuwere_versie_worden_niet_aangeraakt(leeg):
    db.init()
    with db.connect() as conn:
        conn.execute(f"PRAGMA user_version = {db.SCHEMAVERSIE + 1}")
    with pytest.raises(db.SchemaFout, match="nieuwere versie"):
        db.init()


def test_back_up_van_nieuwere_versie_geweigerd(client, tmp_path):
    voeg_voertuig_toe(client)
    zip_pad = backup.maak()
    with zipfile.ZipFile(zip_pad) as z:
        z.extract("onderhoud.db", tmp_path / "uit")
    nieuw = sqlite3.connect(tmp_path / "uit" / "onderhoud.db")
    nieuw.execute(f"PRAGMA user_version = {db.SCHEMAVERSIE + 1}")
    nieuw.close()
    with zipfile.ZipFile(tmp_path / "nieuwer.zip", "w") as z:
        z.write(tmp_path / "uit" / "onderhoud.db", "onderhoud.db")
    with pytest.raises(backup.BackupFout, match="nieuwere versie"):
        backup.herstel(tmp_path / "nieuwer.zip")


def test_mislukte_migratie_bij_terugzetten_laat_alles_zoals_het_was(client, tmp_path, monkeypatch):
    vid = voeg_voertuig_toe(client, kenteken="ZZ-99-ZZ")
    oud = sqlite3.connect(tmp_path / "onderhoud.db")
    oud.executescript(SCHEMA_1_2)
    oud.close()
    with zipfile.ZipFile(tmp_path / "oud.zip", "w") as z:
        z.write(tmp_path / "onderhoud.db", "onderhoud.db")

    def kapot(conn):
        raise RuntimeError("migratie mislukt")

    monkeypatch.setattr(db, "MIGRATIES", [kapot])
    with pytest.raises(backup.BackupFout, match="oude gegevens zijn teruggezet"):
        backup.herstel(tmp_path / "oud.zip")
    with db.connect() as conn:
        assert conn.execute("SELECT kenteken FROM voertuig WHERE id = ?", (vid,)).fetchone()[0] == "ZZ99ZZ"
        assert db.schemaversie(conn) == db.SCHEMAVERSIE


def test_terugzetten_van_oude_back_up_migreert(client, tmp_path):
    oud = sqlite3.connect(tmp_path / "onderhoud.db")
    oud.executescript(SCHEMA_1_2)
    oud.close()
    with zipfile.ZipFile(tmp_path / "oud.zip", "w") as z:
        z.write(tmp_path / "onderhoud.db", "onderhoud.db")
    backup.herstel(tmp_path / "oud.zip")
    with db.connect() as conn:
        assert db.schemaversie(conn) == db.SCHEMAVERSIE
        assert conn.execute("SELECT merk FROM voertuig").fetchone()[0] == "Honda"
    assert "rdw_json" in kolommen("voertuig")
