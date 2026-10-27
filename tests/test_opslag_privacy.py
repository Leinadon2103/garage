"""Wat verwijderd of tijdelijk is, moet ook echt weg (P9, P10, G9)."""
import io
import json
import os
import sqlite3

from pypdf import PdfReader

from app import backup, db
from tests.test_app import client, nieuwe_beurt, pdf_bytes, voeg_voertuig_toe  # noqa: F401


def db_bytes() -> bytes:
    return (db.DATA_DIR / "onderhoud.db").read_bytes()


# ---------- P9: secure_delete ----------

def test_verwijderd_voertuig_staat_niet_meer_in_het_databasebestand(client):
    vid = voeg_voertuig_toe(client)
    with db.connect() as conn:
        conn.execute("UPDATE voertuig SET bijnaam = 'GeheimeBijnaamXyz' WHERE id = ?", (vid,))
    assert b"GeheimeBijnaamXyz" in db_bytes()
    client.post(f"/voertuigen/{vid}/verwijderen")
    assert b"GeheimeBijnaamXyz" not in db_bytes()


def test_eerder_verwijderde_gegevens_worden_eenmalig_opgeschoond(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    oud = sqlite3.connect(tmp_path / "onderhoud.db")  # zoals vóór P9: zonder secure_delete
    oud.execute("PRAGMA secure_delete = OFF")
    oud.execute("INSERT INTO voertuig (kenteken, voertuigsoort, bijnaam) VALUES ('AB12CD', 'Overig', 'OudGeheimXyz')")
    oud.commit()
    oud.execute("DELETE FROM voertuig")
    oud.execute("DELETE FROM instelling WHERE sleutel = 'secure_delete_opgeschoond'")
    oud.commit()
    oud.close()
    assert b"OudGeheimXyz" in db_bytes()
    db.init()
    assert b"OudGeheimXyz" not in db_bytes()


# ---------- P10: geen bestandsnaam in de verkoop-PDF ----------

def test_foutpagina_noemt_de_oorspronkelijke_bestandsnaam_niet(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("factuur Jansen.pdf", pdf_bytes(), "application/pdf"))])
    export = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content))
    tekst = "".join(p.extract_text() for p in export.pages)
    assert "nog niet gecontroleerd" in tekst and "Jansen" not in tekst


# ---------- G9: restanten van een afgebroken terugzetting ----------

def afgebroken_terugzetting(klaar: bool):
    """Stand na een crash midden in _wissel: oude mappen opzij, nieuwe geplaatst, nieuwe database erin."""
    data, tmp = db.DATA_DIR, db.DATA_DIR / ".herstel-tmp"
    tmp.mkdir()
    backup._kopieer_db(data / "onderhoud.db", tmp / "oud-onderhoud.db")
    (tmp / "begonnen").write_text(json.dumps(["facturen"]), encoding="utf-8")
    (data / "facturen").mkdir(exist_ok=True)
    os.replace(data / "facturen", data / ".oud-facturen")
    (data / "facturen").mkdir()
    (data / "facturen" / "uit-back-up.pdf").write_bytes(b"%PDF")
    (data / "documenten").mkdir()  # bestond niet: alleen uit de back-up
    with db.connect() as conn:
        conn.execute("DELETE FROM voertuig")
    if klaar:
        (tmp / "klaar").touch()


def test_afgebroken_terugzetting_zet_bij_het_starten_de_oude_stand_terug(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    oude_bestanden = sorted(p.name for p in (db.DATA_DIR / "facturen").rglob("*.pdf"))
    afgebroken_terugzetting(klaar=False)
    backup.opruimen()
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM voertuig").fetchone()[0] == vid
    assert sorted(p.name for p in (db.DATA_DIR / "facturen").rglob("*.pdf")) == oude_bestanden
    assert not (db.DATA_DIR / "documenten").exists()
    assert not (db.DATA_DIR / ".oud-facturen").exists() and not (db.DATA_DIR / ".herstel-tmp").exists()


def test_voltooide_terugzetting_ruimt_alleen_de_restanten_op(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    afgebroken_terugzetting(klaar=True)
    backup.opruimen()
    assert (db.DATA_DIR / "facturen" / "uit-back-up.pdf").exists()
    assert not (db.DATA_DIR / ".oud-facturen").exists() and not (db.DATA_DIR / ".herstel-tmp").exists()


def test_restant_zonder_markering_wordt_nooit_weggegooid(client):
    (db.DATA_DIR / ".oud-documenten" / "1").mkdir(parents=True)
    (db.DATA_DIR / ".oud-documenten" / "1" / "polis.pdf").write_bytes(b"%PDF")
    backup.opruimen()
    assert (db.DATA_DIR / "documenten" / "1" / "polis.pdf").exists()
    assert not (db.DATA_DIR / ".oud-documenten").exists()


def test_opruimen_mislukt_zonder_de_start_tegen_te_houden(client, monkeypatch):
    afgebroken_terugzetting(klaar=False)
    monkeypatch.setattr(backup, "_kopieer_db", lambda *a: (_ for _ in ()).throw(OSError("op slot")))
    backup.opruimen()  # geen fout naar buiten
    assert (db.DATA_DIR / ".herstel-tmp" / "oud-onderhoud.db").exists()  # niets weggegooid: volgende start opnieuw


def test_halve_back_up_wordt_opgeruimd(client, tmp_path):
    voeg_voertuig_toe(client)
    map_ = tmp_path / "backups"
    map_.mkdir(exist_ok=True)
    (map_ / "Garage-backup-2026-01-01_000000.zip.deel").write_bytes(b"half")
    (map_ / "iets-anders.zip.deel").write_bytes(b"niet van Garage")
    with db.connect() as conn:
        db.zet_instelling(conn, "backup_auto", "uit")
    backup.automatisch()
    assert not (map_ / "Garage-backup-2026-01-01_000000.zip.deel").exists()
    assert (map_ / "iets-anders.zip.deel").exists()


def test_terugzetten_laat_geen_restanten_achter(client):
    voeg_voertuig_toe(client)
    zip_pad = backup.maak()
    backup.herstel(zip_pad)
    restanten = [p.name for p in db.DATA_DIR.iterdir() if p.name.startswith(".")]
    assert restanten == []
