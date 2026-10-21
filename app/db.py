import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("ONDERHOUD_DATA", Path(__file__).resolve().parent.parent / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS voertuig (
    id INTEGER PRIMARY KEY,
    kenteken TEXT NOT NULL,
    voertuigsoort TEXT NOT NULL,
    merk TEXT, model TEXT, datum_eerste_toelating TEXT,
    kleur TEXT, cilinderinhoud INTEGER, bijnaam TEXT
);
CREATE TABLE IF NOT EXISTS onderhoudstype (
    id INTEGER PRIMARY KEY,
    naam TEXT NOT NULL,
    voertuigsoorten TEXT NOT NULL DEFAULT '*',
    interval_km INTEGER,
    interval_maanden INTEGER
);
CREATE TABLE IF NOT EXISTS beurt (
    id INTEGER PRIMARY KEY,
    voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    datum TEXT NOT NULL,
    kilometerstand INTEGER,
    uitgevoerd_door TEXT, kosten REAL, opmerking TEXT
);
CREATE TABLE IF NOT EXISTS beurt_type (
    beurt_id INTEGER NOT NULL REFERENCES beurt(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES onderhoudstype(id),
    PRIMARY KEY (beurt_id, type_id)
);
CREATE TABLE IF NOT EXISTS factuur (
    id INTEGER PRIMARY KEY,
    beurt_id INTEGER NOT NULL REFERENCES beurt(id) ON DELETE CASCADE,
    pad TEXT NOT NULL, originele_naam TEXT NOT NULL, mime_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instelling (sleutel TEXT PRIMARY KEY, waarde TEXT);
CREATE TABLE IF NOT EXISTS km_stand (
    id INTEGER PRIMARY KEY,
    voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    datum TEXT NOT NULL, kilometerstand INTEGER NOT NULL, opmerking TEXT
);
CREATE TABLE IF NOT EXISTS document (
    id INTEGER PRIMARY KEY,
    voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    soort TEXT NOT NULL, titel TEXT, pad TEXT NOT NULL, originele_naam TEXT NOT NULL, mime_type TEXT NOT NULL,
    vervaldatum TEXT, toegevoegd TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spec (
    voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    sleutel TEXT NOT NULL, waarde TEXT NOT NULL, volgorde INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (voertuig_id, sleutel)
);
CREATE TABLE IF NOT EXISTS stalling (
    id INTEGER PRIMARY KEY,
    voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    van TEXT NOT NULL, tot TEXT, km_van INTEGER, km_tot INTEGER
);
CREATE TABLE IF NOT EXISTS voertuig_interval (
    voertuig_id INTEGER NOT NULL REFERENCES voertuig(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES onderhoudstype(id) ON DELETE CASCADE,
    interval_km INTEGER, interval_maanden INTEGER,
    PRIMARY KEY (voertuig_id, type_id)
);
"""

# Kolommen die later zijn toegevoegd; bestaande databases krijgen ze via init().
NIEUWE_KOLOMMEN = {
    "voertuig": {
        "beeld_type": "TEXT",    # '3d', 'foto' of 'eigen'
        "beeld_ref": "TEXT",     # Sketchfab-uid bij 3d
        "beeld_titel": "TEXT",
        "beeld_bron": "TEXT",    # link naar de bron (naamsvermelding)
        "beeld_pad": "TEXT",     # lokale afbeelding (ook offline-terugval voor 3d)
        "schema_bron": "TEXT",   # waar de eigen intervallen vandaan komen, bijv. instructieboekje
        "rdw_json": "TEXT",      # extra RDW-gegevens (APK, terugroepactie, tellerstandoordeel, ...)
        "rdw_bijgewerkt": "TEXT",
    },
    "factuur": {
        "origineel_pad": "TEXT",  # niet-gelakte versie; NULL = nog niet gelakt (pad is dan het origineel)
        "gecontroleerd": "INTEGER NOT NULL DEFAULT 0",
        "boxen": "TEXT",          # JSON van de toegepaste balken, zodat opnieuw lakken daarop voortbouwt
    },
}

MOTOR = "Motorfiets,Bromfiets"
AUTO = "Personenauto,Bedrijfsauto"

# (naam, voertuigsoorten, interval_km, interval_maanden)
STANDAARD_TYPES = [
    ("Olie vervangen", "*", 6000, 12),
    ("Oliefilter vervangen", "*", 6000, 12),
    ("Luchtfilter vervangen", "*", 12000, 24),
    ("Bougies vervangen", "*", 12000, 24),
    ("Remvloeistof vervangen", "*", None, 24),
    ("Koelvloeistof vervangen", "*", None, 24),
    ("Remblokken voor vervangen", "*", None, None),
    ("Remblokken achter vervangen", "*", None, None),
    ("Banden vervangen", "*", None, None),
    ("Accu vervangen", "*", None, None),
    ("Kleine beurt", "*", None, 12),
    ("Grote beurt", "*", None, 24),
    ("Ketting smeren/spannen", MOTOR, 1000, None),
    ("Ketting + tandwielen vervangen", MOTOR, None, None),
    ("Kleppen stellen", MOTOR, 24000, None),
    ("APK", AUTO, None, 12),
    ("Distributieriem vervangen", AUTO, 120000, 72),
    ("Interieurfilter vervangen", AUTO, 15000, 12),
    ("Ruitenwissers vervangen", AUTO, None, 12),
    ("Airco-service", AUTO, None, 24),
    ("Lagers/verlichting controleren", "Aanhangwagen", None, 12),
    ("Overig", "*", None, None),
]


@contextmanager
def connect():
    """Verbinding die bij succes commit, bij een fout terugdraait en altijd wordt gesloten."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "onderhoud.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for tabel, kolommen in NIEUWE_KOLOMMEN.items():
            bestaand = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabel})")}
            for kolom, soort in kolommen.items():
                if kolom not in bestaand:
                    conn.execute(f"ALTER TABLE {tabel} ADD COLUMN {kolom} {soort}")
        if not conn.execute("SELECT 1 FROM onderhoudstype LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO onderhoudstype (naam, voertuigsoorten, interval_km, interval_maanden) VALUES (?,?,?,?)",
                STANDAARD_TYPES,
            )


def types_voor(conn: sqlite3.Connection, voertuigsoort: str) -> list[sqlite3.Row]:
    """Onderhoudstypes die bij deze voertuigsoort horen ('*' = alle soorten)."""
    rows = conn.execute("SELECT * FROM onderhoudstype ORDER BY naam = 'Overig', naam").fetchall()
    return [r for r in rows if r["voertuigsoorten"] == "*" or voertuigsoort in r["voertuigsoorten"].split(",")]


def beurten(conn: sqlite3.Connection, voertuig_id: int) -> list[dict]:
    """Beurten van een voertuig, nieuwste eerst, met hun types en facturen."""
    result = []
    for b in conn.execute(
        "SELECT * FROM beurt WHERE voertuig_id = ? ORDER BY datum DESC, id DESC", (voertuig_id,)
    ):
        d = dict(b)
        d["types"] = conn.execute(
            "SELECT t.* FROM onderhoudstype t JOIN beurt_type bt ON bt.type_id = t.id WHERE bt.beurt_id = ? ORDER BY t.naam",
            (b["id"],),
        ).fetchall()
        d["facturen"] = conn.execute("SELECT * FROM factuur WHERE beurt_id = ? ORDER BY id", (b["id"],)).fetchall()
        result.append(d)
    return result


def instelling(conn: sqlite3.Connection, sleutel: str, standaard: str = "") -> str:
    rij = conn.execute("SELECT waarde FROM instelling WHERE sleutel = ?", (sleutel,)).fetchone()
    return rij["waarde"] if rij else standaard


def zet_instelling(conn: sqlite3.Connection, sleutel: str, waarde: str) -> None:
    conn.execute("INSERT INTO instelling (sleutel, waarde) VALUES (?, ?) "
                 "ON CONFLICT(sleutel) DO UPDATE SET waarde = excluded.waarde", (sleutel, waarde))


def prive_termen(conn: sqlite3.Connection) -> list[str]:
    return [t.strip() for t in instelling(conn, "prive_termen").splitlines() if len(t.strip()) >= 3]


def intervallen(conn: sqlite3.Connection, voertuig_id: int) -> dict[int, tuple[int | None, int | None]]:
    """Eigen intervallen van een voertuig: {type_id: (km, maanden)}. Ontbreekt een type, dan geldt de vuistregel."""
    return {r["type_id"]: (r["interval_km"], r["interval_maanden"]) for r in conn.execute(
        "SELECT * FROM voertuig_interval WHERE voertuig_id = ?", (voertuig_id,))}


def kmverloop(conn: sqlite3.Connection, voertuig_id: int) -> list[dict]:
    """Alle bekende km-standen (beurten, losse registraties, stalling), oud naar nieuw."""
    rijen = conn.execute("""
        SELECT datum, kilometerstand, 'beurt' AS bron, id FROM beurt
            WHERE voertuig_id = ? AND kilometerstand IS NOT NULL
        UNION ALL SELECT datum, kilometerstand, 'notitie', id FROM km_stand WHERE voertuig_id = ?
        UNION ALL SELECT van, km_van, 'stalling in', NULL FROM stalling WHERE voertuig_id = ? AND km_van IS NOT NULL
        UNION ALL SELECT tot, km_tot, 'stalling uit', NULL FROM stalling WHERE voertuig_id = ? AND km_tot IS NOT NULL
        ORDER BY datum, kilometerstand""", (voertuig_id,) * 4).fetchall()
    return [dict(r) for r in rijen]


def huidige_km(conn: sqlite3.Connection, voertuig_id: int) -> int | None:
    return max((r["kilometerstand"] for r in kmverloop(conn, voertuig_id)), default=None)


def actieve_stalling(conn: sqlite3.Connection, voertuig_id: int):
    return conn.execute("SELECT * FROM stalling WHERE voertuig_id = ? AND tot IS NULL ORDER BY id DESC LIMIT 1",
                        (voertuig_id,)).fetchone()


GELDIG_PAD = re.compile(r"(facturen|beelden|documenten)/[\w.\-/]+")


def bestand(rel: str | None) -> Path:
    """Pad uit de database -> bestand in de gegevensmap. Weigert alles daarbuiten (bijv. uit een
    gemanipuleerde back-up: absolute paden, '..', andere schijven)."""
    if not rel or not GELDIG_PAD.fullmatch(rel) or ".." in rel.split("/"):
        raise ValueError(f"Ongeldig bestandspad: {rel!r}")
    pad = (DATA_DIR / rel).resolve()
    if DATA_DIR.resolve() not in pad.parents:
        raise ValueError(f"Bestandspad buiten de gegevensmap: {rel!r}")
    return pad


def ongeldige_paden(conn: sqlite3.Connection) -> list[str]:
    """Alle bestandspaden in de database die niet door bestand() komen (controle na terugzetten)."""
    paden = [r[0] for r in conn.execute(
        "SELECT pad FROM factuur UNION ALL SELECT origineel_pad FROM factuur WHERE origineel_pad IS NOT NULL "
        "UNION ALL SELECT beeld_pad FROM voertuig WHERE beeld_pad IS NOT NULL UNION ALL SELECT pad FROM document")]
    fout = []
    for p in paden:
        try:
            bestand(p)
        except ValueError:
            fout.append(p)
    return fout
