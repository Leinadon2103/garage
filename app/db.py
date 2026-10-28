import base64
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path

from . import crypto

# GARAGE_DATA overrides the data folder; ONDERHOUD_DATA is the old name and still works.
# resolve(): data_file() returns resolved paths; a short 8.3 path (DANIEL~1) or link here would break relative_to()
DATA_DIR = Path(os.environ.get("GARAGE_DATA") or os.environ.get("ONDERHOUD_DATA")
                or Path(__file__).resolve().parent.parent / "data").resolve()
DB_NAME = "onderhoud.db"  # file name stays Dutch: users see it in their data folder and in backups
DATA_FOLDERS = ("facturen", "beelden", "documenten")  # Dutch: users see them in their data folder

# Schema up to version 1.3.0 (migration 1), with the original Dutch names. Never change this: a change is a new migration.
SCHEMA_1 = """
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

# Columns added later, before migrations existed (up to 1.3.0); migration 1 adds them to old databases.
COLUMNS_1 = {
    "voertuig": {
        "beeld_type": "TEXT", "beeld_ref": "TEXT", "beeld_titel": "TEXT", "beeld_bron": "TEXT", "beeld_pad": "TEXT",
        "schema_bron": "TEXT", "rdw_json": "TEXT", "rdw_bijgewerkt": "TEXT",
    },
    "factuur": {"origineel_pad": "TEXT", "gecontroleerd": "INTEGER NOT NULL DEFAULT 0", "boxen": "TEXT"},
}

# Migration 2: Dutch -> English names. {old table: (new table, {old column: new column})}
RENAMES_2 = {
    "voertuig": ("vehicle", {
        "kenteken": "license_plate", "voertuigsoort": "vehicle_type", "merk": "make",
        "datum_eerste_toelating": "first_registration_date", "kleur": "color", "cilinderinhoud": "displacement_cc",
        "bijnaam": "nickname", "beeld_type": "image_type", "beeld_ref": "image_ref", "beeld_titel": "image_title",
        "beeld_bron": "image_source", "beeld_pad": "image_path", "schema_bron": "schedule_source",
        "rdw_bijgewerkt": "rdw_updated_on"}),
    "onderhoudstype": ("maintenance_type", {
        "naam": "name", "voertuigsoorten": "vehicle_types", "interval_maanden": "interval_months"}),
    "voertuig_interval": ("vehicle_interval", {"voertuig_id": "vehicle_id", "interval_maanden": "interval_months"}),
    "beurt": ("service", {
        "voertuig_id": "vehicle_id", "datum": "date", "kilometerstand": "odometer_km", "uitgevoerd_door": "performed_by",
        "kosten": "cost", "opmerking": "note"}),
    "beurt_type": ("service_maintenance_type", {"beurt_id": "service_id"}),
    "factuur": ("invoice", {
        "beurt_id": "service_id", "pad": "path", "originele_naam": "original_filename", "origineel_pad": "original_path",
        "gecontroleerd": "reviewed", "boxen": "redactions"}),
    "document": ("document", {
        "voertuig_id": "vehicle_id", "soort": "kind", "titel": "title", "pad": "path",
        "originele_naam": "original_filename", "vervaldatum": "expiry_date", "toegevoegd": "added_on"}),
    "km_stand": ("odometer_reading", {
        "voertuig_id": "vehicle_id", "datum": "date", "kilometerstand": "odometer_km", "opmerking": "note"}),
    "stalling": ("storage_period", {
        "voertuig_id": "vehicle_id", "van": "start_date", "tot": "end_date", "km_van": "start_km", "km_tot": "end_km"}),
    "spec": ("spec", {"voertuig_id": "vehicle_id", "sleutel": "key", "waarde": "value", "volgorde": "position"}),
    "instelling": ("setting", {"sleutel": "key", "waarde": "value"}),
}
SETTING_KEYS_2 = {
    "thema": "theme", "prive_termen": "private_terms", "herinneringen": "reminders",
    "melding_laatst": "last_notification", "sketchfab_auto": "sketchfab_autoload", "backup_auto": "backup_auto",
    "backup_map": "backup_folder", "backup_laatste": "backup_last", "backup_fout": "backup_error",
    "secure_delete_opgeschoond": "secure_delete_vacuumed",
}
SETTING_VALUES_2 = {"aan": "on", "uit": "off", "ja": "yes", "licht": "light", "donker": "dark", "systeem": "system"}
RDW_KEYS_2 = {
    "apk_vervaldatum": "apk_expiry_date", "terugroepactie": "open_recall", "tellerstandoordeel": "odometer_judgement",
    "jaar_tellerstand": "odometer_year", "catalogusprijs": "list_price",
    "datum_tenaamstelling_nl": "first_registration_nl_date", "massa_rijklaar": "curb_weight_kg", "brandstof": "fuel",
    "vermogen_kw": "power_kw",
}
IMAGE_TYPES_2 = {"foto": "photo", "eigen": "own"}

MOTORCYCLE = "Motorfiets,Bromfiets"
CAR = "Personenauto,Bedrijfsauto"

# (name, vehicle_types, interval_km, interval_months); names and vehicle types are shown in the app, so Dutch
DEFAULT_TYPES = [
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
    ("Ketting smeren/spannen", MOTORCYCLE, 1000, None),
    ("Ketting + tandwielen vervangen", MOTORCYCLE, None, None),
    ("Kleppen stellen", MOTORCYCLE, 24000, None),
    ("APK", CAR, None, 12),
    ("Distributieriem vervangen", CAR, 120000, 72),
    ("Interieurfilter vervangen", CAR, 15000, 12),
    ("Ruitenwissers vervangen", CAR, None, 12),
    ("Airco-service", CAR, None, 24),
    ("Lagers/verlichting controleren", "Aanhangwagen", None, 12),
    ("Overig", "*", None, None),
]
OTHER_TYPE = "Overig"  # the catch-all maintenance type, always listed last


@contextmanager
def connect():
    """Connection that commits on success, rolls back on an error and is always closed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # overwrite deleted data with zeros; otherwise it stays in free pages (and ends up in backups)
    conn.execute("PRAGMA secure_delete = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class SchemaError(Exception):
    pass


def _migration_1(conn: sqlite3.Connection) -> None:
    """Baseline: the schema of 1.3.0. Also works on databases from before migrations (user_version 0), which can
    miss tables or columns that were added later."""
    for statement in filter(str.strip, SCHEMA_1.split(";")):
        conn.execute(statement)
    for table, columns in COLUMNS_1.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _rename_json_keys(conn: sqlite3.Connection, table: str, column: str, convert) -> None:
    # table and column are fixed names from _migration_2 (a name cannot be a ?-parameter), never input
    for row in conn.execute(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall():  # nosec B608
        try:
            data = json.loads(row[1])
        except ValueError:
            continue  # unreadable JSON stays as it is; the app already treats it as empty
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?",  # nosec B608
                     (json.dumps(convert(data)), row[0]))


def _migration_2(conn: sqlite3.Connection) -> None:
    """Dutch -> English names for tables, columns and internal values. RENAME also updates foreign keys.
    Values the user sees (vehicle types, document kinds, maintenance type names) stay Dutch."""
    for old, (new, columns) in RENAMES_2.items():
        for old_column, new_column in columns.items():
            conn.execute(f'ALTER TABLE {old} RENAME COLUMN {old_column} TO "{new_column}"')
        if new != old:
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
    for old, new in SETTING_KEYS_2.items():
        conn.execute("UPDATE setting SET key = ? WHERE key = ?", (new, old))
    for old, new in SETTING_VALUES_2.items():
        conn.execute("UPDATE setting SET value = ? WHERE value = ? AND key != 'private_terms'", (new, old))
    conn.execute("DELETE FROM setting WHERE key = 'last_notification'")  # only limits repeats; rebuilt by itself

    def rdw(data):
        return {RDW_KEYS_2.get(k, k): v for k, v in data.items()} if isinstance(data, dict) else data

    def redactions(data):
        return [{("page" if k == "pagina" else k): v for k, v in b.items()} if isinstance(b, dict) else b
                for b in data] if isinstance(data, list) else data

    _rename_json_keys(conn, "vehicle", "rdw_json", rdw)
    _rename_json_keys(conn, "invoice", "redactions", redactions)
    for old, new in IMAGE_TYPES_2.items():
        conn.execute("UPDATE vehicle SET image_type = ? WHERE image_type = ?", (new, old))


# Migration n takes the database from version n-1 to n (PRAGMA user_version). Only append, never change an
# existing migration. A migration runs in one transaction; PRAGMA foreign_keys cannot change inside it, so
# rebuilding a table follows the steps in https://sqlite.org/lang_altertable.html.
def _migration_3(conn: sqlite3.Connection) -> None:
    """Encryption (P1): one key per data file, wrapped with the master key. Encrypting the existing files and
    fields is not a migration but encrypt_existing(): it needs the master key and works file by file."""
    conn.execute("CREATE TABLE file_key (path TEXT PRIMARY KEY, key BLOB NOT NULL)")


MIGRATIONS = [_migration_1, _migration_2, _migration_3]
SCHEMA_VERSION = len(MIGRATIONS)


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    """Runs the missing migrations, each with the new version in the same transaction: if anything goes wrong,
    the database stays at the last good version."""
    version = schema_version(conn)
    if version > SCHEMA_VERSION:
        raise SchemaError(f"De gegevens zijn van een nieuwere versie van Garage (schema {version}, deze versie kent "
                          f"tot {SCHEMA_VERSION}). Installeer de nieuwste versie.")
    for number in range(version + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN")
        try:
            MIGRATIONS[number - 1](conn)
            conn.execute(f"PRAGMA user_version = {number:d}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def init() -> None:
    """Migrations and defaults. Also after a restore: the database (and so the key fingerprint) may be another
    one now, so the master key is loaded again on first use."""
    reset_master_key()
    with connect() as conn:
        migrate(conn)
        if not setting(conn, "secure_delete_vacuumed"):
            # once: data deleted before secure_delete is still in free pages; VACUUM rewrites the file
            conn.execute("VACUUM")
            set_setting(conn, "secure_delete_vacuumed", "yes")
        if not conn.execute("SELECT 1 FROM maintenance_type LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO maintenance_type (name, vehicle_types, interval_km, interval_months) VALUES (?,?,?,?)",
                DEFAULT_TYPES,
            )


def types_for(conn: sqlite3.Connection, vehicle_type: str) -> list[sqlite3.Row]:
    """Maintenance types that apply to this vehicle type ('*' = all types)."""
    rows = conn.execute("SELECT * FROM maintenance_type ORDER BY name = ?, name", (OTHER_TYPE,)).fetchall()
    return [r for r in rows if r["vehicle_types"] == "*" or vehicle_type in r["vehicle_types"].split(",")]


def services(conn: sqlite3.Connection, vehicle_id: int) -> list[dict]:
    """Services of a vehicle, newest first, with their maintenance types and invoices."""
    result = []
    for s in conn.execute(
        "SELECT * FROM service WHERE vehicle_id = ? ORDER BY date DESC, id DESC", (vehicle_id,)
    ):
        d = dict(s)
        d["types"] = conn.execute(
            "SELECT t.* FROM maintenance_type t JOIN service_maintenance_type st ON st.type_id = t.id "
            "WHERE st.service_id = ? ORDER BY t.name", (s["id"],),
        ).fetchall()
        d["invoices"] = conn.execute("SELECT * FROM invoice WHERE service_id = ? ORDER BY id", (s["id"],)).fetchall()
        result.append(d)
    return result


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO setting (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def private_terms_text(conn: sqlite3.Connection) -> str:
    """The own redaction terms (name, address): stored encrypted, the most sensitive text in the database."""
    return decrypt_text(setting(conn, "private_terms"), "setting.private_terms") or ""


def set_private_terms(conn: sqlite3.Connection, text: str) -> None:
    set_setting(conn, "private_terms", encrypt_text(text, "setting.private_terms") if text else "")


def private_terms(conn: sqlite3.Connection) -> list[str]:
    return [t.strip() for t in private_terms_text(conn).splitlines() if len(t.strip()) >= 3]


def intervals(conn: sqlite3.Connection, vehicle_id: int) -> dict[int, tuple[int | None, int | None]]:
    """Custom intervals of a vehicle: {type_id: (km, months)}. A missing type uses the rule of thumb."""
    return {r["type_id"]: (r["interval_km"], r["interval_months"]) for r in conn.execute(
        "SELECT * FROM vehicle_interval WHERE vehicle_id = ?", (vehicle_id,))}


def odometer_history(conn: sqlite3.Connection, vehicle_id: int) -> list[dict]:
    """All known odometer readings (services, separate readings, storage), oldest first."""
    rows = conn.execute("""
        SELECT date, odometer_km, 'service' AS source, id FROM service
            WHERE vehicle_id = ? AND odometer_km IS NOT NULL
        UNION ALL SELECT date, odometer_km, 'reading', id FROM odometer_reading WHERE vehicle_id = ?
        UNION ALL SELECT start_date, start_km, 'storage_start', NULL FROM storage_period
            WHERE vehicle_id = ? AND start_km IS NOT NULL
        UNION ALL SELECT end_date, end_km, 'storage_end', NULL FROM storage_period
            WHERE vehicle_id = ? AND end_km IS NOT NULL
        ORDER BY date, odometer_km""", (vehicle_id,) * 4).fetchall()
    return [dict(r) for r in rows]


def current_km(conn: sqlite3.Connection, vehicle_id: int) -> int | None:
    return max((r["odometer_km"] for r in odometer_history(conn, vehicle_id)), default=None)


def active_storage(conn: sqlite3.Connection, vehicle_id: int):
    return conn.execute("SELECT * FROM storage_period WHERE vehicle_id = ? AND end_date IS NULL ORDER BY id DESC LIMIT 1",
                        (vehicle_id,)).fetchone()


# folder names stay Dutch: users see them in their data folder
VALID_PATH = re.compile(r"(facturen|beelden|documenten)/[\w.\-/]+")
MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}  # same as the types in web.ALLOWED


def data_file(rel: str | None) -> Path:
    """Path from the database -> file in the data folder. Refuses everything outside it (e.g. from a
    tampered backup: absolute paths, '..', other drives)."""
    if not rel or not VALID_PATH.fullmatch(rel) or ".." in rel.split("/"):
        raise ValueError(f"Ongeldig bestandspad: {rel!r}")
    path = (DATA_DIR / rel).resolve()
    if DATA_DIR.resolve() not in path.parents:
        raise ValueError(f"Bestandspad buiten de gegevensmap: {rel!r}")
    return path


# ---------- encryption (P1, design in docs/encryption.md) ----------

_master_keys: dict[Path, bytes] = {}  # per data folder (tests switch folders)
_master_lock = threading.Lock()
TEXT_PREFIX = "grge:"  # an encrypted text field: prefix + base64 of crypto.encrypt()


def _load_master(conn: sqlite3.Connection) -> bytes | None:
    """The master key of this data folder, or None if it is not available (other pc or Windows account, key file
    gone): then nothing can be read or written until it is recovered with the backup password.
    A new key is only made if nothing was ever encrypted here: a new key next to encrypted data would lose it."""
    expected = setting(conn, "master_key_fingerprint")
    try:
        key = crypto.load_master(DATA_DIR)
    except crypto.CryptoError:
        logging.warning("The key file cannot be opened (other pc or Windows account?)")
        return None
    if key is None:
        if expected:
            logging.warning("The key file is missing, but the data is encrypted")
            return None
        key = crypto.new_key()
        crypto.save_master(DATA_DIR, key)
    elif expected and expected != crypto.fingerprint(key):
        logging.warning("The key file belongs to other data (fingerprint differs)")
        return None
    if not expected:  # only write when needed: a caller may have a write transaction open on another connection
        set_setting(conn, "master_key_fingerprint", crypto.fingerprint(key))
    return key


def reset_master_key() -> None:
    """Load the master key again on first use (after a restore or a new garage.key)."""
    with _master_lock:
        _master_keys.pop(DATA_DIR, None)


def key_available() -> bool:
    try:
        master_key()
        return True
    except crypto.CryptoError:
        return False


def set_backup_password(password: str) -> None:
    """Stores the master key a second time, wrapped with the backup password (P2): it goes into every backup, so
    another pc can restore it, and it brings the key back here if garage.key can no longer be opened."""
    wrapped = crypto.wrap_with_password(master_key(), password)
    with connect() as conn:
        set_setting(conn, "master_key_backup", wrapped)


def unlock_with_password(password: str) -> None:
    """garage.key cannot be opened (other pc, Windows reinstalled): the backup password brings back the master
    key from this database and stores it again with DPAPI. Then everything is readable again."""
    with connect() as conn:
        wrapped, expected = setting(conn, "master_key_backup"), setting(conn, "master_key_fingerprint")
    if not wrapped:
        raise crypto.CryptoError("Er is nooit een back-upwachtwoord ingesteld. Zonder dat wachtwoord kan Garage "
                                 "de sleutel niet terughalen.")
    key = crypto.unwrap_with_password(wrapped, password)
    if expected and crypto.fingerprint(key) != expected:
        raise crypto.CryptoError("Dit wachtwoord hoort bij andere gegevens.")
    crypto.save_master(DATA_DIR, key)
    reset_master_key()


def master_key(conn: sqlite3.Connection | None = None) -> bytes:
    with _master_lock:
        if DATA_DIR not in _master_keys:
            with nullcontext(conn) if conn else connect() as c:
                if (key := _load_master(c)) is None:
                    raise crypto.CryptoError("De sleutel van je gegevens is niet beschikbaar op deze pc.")
            _master_keys[DATA_DIR] = key
        return _master_keys[DATA_DIR]


def encrypt_text(value: str | None, context: str) -> str | None:
    """A sensitive text field (original file name, redaction terms), encrypted with the master key."""
    if value is None or value.startswith(TEXT_PREFIX):
        return value
    return TEXT_PREFIX + base64.b64encode(crypto.encrypt(master_key(), value.encode(), context)).decode()


def decrypt_text(value: str | None, context: str) -> str | None:
    """Also takes a text that is not encrypted yet (before encrypt_existing() ran)."""
    if not value or not value.startswith(TEXT_PREFIX):
        return value
    return crypto.decrypt(master_key(), base64.b64decode(value[len(TEXT_PREFIX):]), context).decode()


def _file_key(conn: sqlite3.Connection, rel: str, create: bool) -> bytes:
    row = conn.execute("SELECT key FROM file_key WHERE path = ?", (rel,)).fetchone()
    if row:
        return crypto.decrypt(master_key(conn), row["key"], f"file-key:{rel}")
    if not create:
        raise crypto.CryptoError("Er is geen sleutel voor dit bestand.")
    key = crypto.new_key()
    conn.execute("INSERT INTO file_key (path, key) VALUES (?, ?)",
                 (rel, crypto.encrypt(master_key(conn), key, f"file-key:{rel}")))
    return key


# Every read, write and delete of a file in the data folder goes through these three functions (a test checks
# that), so nothing can bypass the encryption. Pass `conn` when a write transaction is open on it: a second
# connection would wait for its lock.

def read_file(rel: str | None) -> bytes:
    """Decrypted content of a data file. ValueError for an invalid path, FileNotFoundError if it is missing,
    crypto.CryptoError if it cannot be decrypted."""
    data = data_file(rel).read_bytes()
    if not crypto.is_encrypted(data):
        return data  # not encrypted yet: encrypt_existing() takes care of it
    with connect() as conn:
        key = _file_key(conn, rel, create=False)
    return crypto.decrypt(key, data, rel)


def write_file(rel: str, data: bytes, conn: sqlite3.Connection | None = None) -> None:
    """Encrypts with the file's own key and writes atomically: a crash never leaves half a file under the real
    name (a '.deel' file instead, which backup.clean_up() removes). The key is stored before the file exists."""
    path = data_file(rel)
    with nullcontext(conn) if conn else connect() as c:
        key = _file_key(c, rel, create=True)
    blob = crypto.encrypt(key, data, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".deel")
    try:
        part.write_bytes(blob)
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def delete_file(rel: str | None, conn: sqlite3.Connection | None = None) -> None:
    """The key goes first (with secure_delete): then every copy is unreadable, also an old version in OneDrive
    or a file that cannot be deleted because it is still open."""
    path = data_file(rel)
    with nullcontext(conn) if conn else connect() as c:
        c.execute("DELETE FROM file_key WHERE path = ?", (rel,))
    path.unlink(missing_ok=True)


def needs_encryption() -> bool:
    """Whether there are data files that are not encrypted yet (e.g. from before P1 or from an old backup) and
    the master key is there to do it."""
    try:
        master_key()
    except crypto.CryptoError:
        return False
    return any(_plain_files())


def _plain_files():
    for sub in DATA_FOLDERS:
        for path in sorted((DATA_DIR / sub).rglob("*")) if (DATA_DIR / sub).is_dir() else []:
            if path.is_file() and path.suffix != ".deel":
                with open(path, "rb") as f:
                    if not crypto.is_encrypted(f.read(len(crypto.MAGIC))):
                        yield path


def encrypt_existing() -> int:
    """Encrypts every file and text field that is not encrypted yet; returns the number of files. File by file
    with the key committed first, so an interruption is harmless: the next start does the rest. Also files
    without a database row (orphans): they are just as sensitive. Does nothing without a master key."""
    try:
        master_key()
    except crypto.CryptoError:
        return 0
    count = 0
    for path in list(_plain_files()):
        rel = path.relative_to(DATA_DIR).as_posix()
        try:
            write_file(rel, path.read_bytes())
            count += 1
        except (ValueError, OSError):  # a name the app never makes, or a file in use: try again next time
            logging.warning("Could not encrypt %s", rel)
    with connect() as conn:
        for table in ("invoice", "document"):
            # table is a fixed name from the tuple above, never input
            for row in conn.execute(f"SELECT id, original_filename FROM {table} "  # nosec B608
                                    "WHERE original_filename NOT LIKE ?", (TEXT_PREFIX + "%",)).fetchall():
                conn.execute(f"UPDATE {table} SET original_filename = ? WHERE id = ?",  # nosec B608
                             (encrypt_text(row["original_filename"], "original_filename"), row["id"]))
        if (terms := setting(conn, "private_terms")) and not terms.startswith(TEXT_PREFIX):
            set_private_terms(conn, terms)
    if count:
        logging.info("Encrypted %d existing files", count)
    return count


def invalid_paths(conn: sqlite3.Connection) -> list[str]:
    """All file paths in the database that data_file() refuses (check after a restore)."""
    paths = [r[0] for r in conn.execute(
        "SELECT path FROM invoice UNION ALL SELECT original_path FROM invoice WHERE original_path IS NOT NULL "
        "UNION ALL SELECT image_path FROM vehicle WHERE image_path IS NOT NULL UNION ALL SELECT path FROM document")]
    invalid = []
    for p in paths:
        try:
            data_file(p)
        except ValueError:
            invalid.append(p)
    return invalid
