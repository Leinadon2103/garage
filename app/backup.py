"""Backup and restore of all data as one zip: database + invoices + images + documents."""
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from . import db

# Names of folders and files the user sees (data folder, backup folder) stay Dutch.
FOLDERS = ("facturen", "beelden", "documenten")
PREFIX = "Garage-backup-"
SAFETY_PREFIX = "Garage-veiligheidskopie-"  # made automatically just before a restore
DEFAULT_FOLDER_NAME = "Garage back-ups"
RESTORE_TMP = ".herstel-tmp"
OLD_PREFIX = ".oud-"
KEEP_SAFETY = 3
MAX_SIZE = 4 * 1024 ** 3  # unpacked size; protection against 'zip bombs'
_LOCK = threading.RLock()  # never two backups, or a backup during a restore
KEEP = 10          # this many backups stay in the backup folder
INTERVAL_DAYS = 7  # automatic backup
WARN_DAYS = 30     # message in the app if the last backup is older


class BackupError(Exception):
    pass


def default_folder() -> Path:
    """OneDrive if there is one (then the backup is in the cloud as well), otherwise the user folder."""
    onedrive = os.environ.get("OneDrive")
    base = Path(onedrive) if onedrive and Path(onedrive).is_dir() else Path.home()
    return base / DEFAULT_FOLDER_NAME


def target_folder(conn) -> Path:
    return Path(db.setting(conn, "backup_folder") or default_folder())


def check_target_folder(folder: Path) -> Path:
    folder = Path(folder).expanduser()
    text = str(folder)
    if text.startswith(("\\\\", "//")) or folder.drive.startswith(("\\\\", "//")):
        raise BackupError("Een netwerkmap (\\\\server\\...) kan niet; kies een map op deze pc, een USB-stick of je OneDrive-map.")
    if not folder.is_absolute():
        raise BackupError("Kies een volledige map, bijvoorbeeld D:\\Back-ups.")
    data = db.DATA_DIR.resolve()
    if folder.resolve() == data or data in folder.resolve().parents:
        raise BackupError("De back-upmap mag niet in de gegevensmap van Garage zelf liggen.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupError(f"Map kan niet worden gemaakt: {e.strerror or e}")
    return folder


def list_backups(folder: Path, prefix: str = PREFIX) -> list[dict]:
    if not folder.is_dir():
        return []
    zips = sorted(folder.glob(f"{prefix}*.zip"), key=lambda p: p.name, reverse=True)
    return [{"name": p.name, "path": p, "size": p.stat().st_size, "safety": prefix == SAFETY_PREFIX,
             "date": datetime.fromtimestamp(p.stat().st_mtime)} for p in zips]


def all_backups(folder: Path) -> list[dict]:
    """Normal backups and safety copies (from before a restore), newest first."""
    return sorted(list_backups(folder) + list_backups(folder, SAFETY_PREFIX), key=lambda b: b["date"], reverse=True)


def create(folder: Path | None = None, prefix: str = PREFIX, keep_also: Path | None = None) -> Path:
    """Makes a backup; writes to a temporary file first, so there is never half a zip left behind."""
    with _LOCK:
        with db.connect() as conn:
            folder = check_target_folder(folder or target_folder(conn))
        base = f"{prefix}{datetime.now():%Y-%m-%d_%H%M%S}"
        name, sequence = f"{base}.zip", 1
        while (folder / name).exists():  # never overwrite an existing backup (e.g. two in the same second)
            sequence += 1
            name = f"{base}_{sequence}.zip"
        part = folder / (name + ".deel")
        count = 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / db.DB_NAME
                _copy_db(db.DATA_DIR / db.DB_NAME, copy)  # consistent copy, even while the app is writing
                with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(copy, db.DB_NAME)
                    for sub in FOLDERS:
                        for file in sorted((db.DATA_DIR / sub).rglob("*")) if (db.DATA_DIR / sub).is_dir() else []:
                            if file.is_file():
                                z.write(file, file.relative_to(db.DATA_DIR).as_posix())
                                count += 1
                    z.writestr("garage-backup.json", json.dumps({"created": datetime.now().isoformat(timespec="seconds"),
                                                                 "files": count}))
            os.replace(part, folder / name)
        except Exception as e:
            part.unlink(missing_ok=True)
            if isinstance(e, BackupError):
                raise
            raise BackupError(f"Back-up mislukt: {e}") from e
        # remove old ones: safety copies count separately, so they never push out good backups
        for old in list_backups(folder, prefix)[KEEP if prefix == PREFIX else KEEP_SAFETY:]:
            if keep_also is None or old["path"].resolve() != Path(keep_also).resolve():
                try:
                    old["path"].unlink(missing_ok=True)
                except OSError:
                    logging.warning("Old backup %s could not be deleted", old["name"])
        if prefix == PREFIX:
            with db.connect() as conn:
                db.set_setting(conn, "backup_last", datetime.now().isoformat(timespec="seconds"))
                db.set_setting(conn, "backup_error", "")
        return folder / name


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts and ":" not in name and "\\" not in name
            and (name in (db.DB_NAME, "garage-backup.json") or path.parts[0] in FOLDERS))


def _check_database(path: Path) -> None:
    """Checks the unpacked database of a backup, after migrating it to the current schema (it is our own
    temporary copy, and backups of every older version must keep working)."""
    test = sqlite3.connect(path)
    test.row_factory = sqlite3.Row
    try:
        if db.schema_version(test) > db.SCHEMA_VERSION:
            raise BackupError("Deze back-up komt van een nieuwere versie van Garage. Installeer eerst de nieuwste versie.")
        # a Garage database has a vehicle table (Dutch name before migration 2); otherwise migration 1 would
        # simply create the tables in any SQLite file
        ok = test.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and test.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name IN ('voertuig', 'vehicle')").fetchone() is not None
        if ok:
            db.migrate(test)
            ok = not db.invalid_paths(test) and all(r[0] in db.MIME_TYPES for r in test.execute(
                "SELECT mime_type FROM invoice UNION ALL SELECT mime_type FROM document"))
    except (sqlite3.DatabaseError, db.SchemaError):
        ok = False
    finally:
        test.close()
    if not ok:
        raise BackupError("De database in deze back-up is beschadigd of bevat ongeldige gegevens.")


def restore(zip_path: Path) -> Path:
    """Restores a backup; returns the safety copy of the current state.

    Order: unpack and check -> safety copy -> move the current folders aside (fails fast if a file is open)
    -> place the new folders + database. If anything goes wrong on the way, everything is rolled back
    to the old state."""
    with _LOCK:
        try:
            z = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError):
            raise BackupError("Dit is geen geldige Garage-back-up (geen zip-bestand).")
        tmp = db.DATA_DIR / RESTORE_TMP
        with z:
            names = z.namelist()
            if db.DB_NAME not in names or not all(_safe_name(n) for n in names):
                raise BackupError("Dit is geen geldige Garage-back-up.")
            if sum(i.file_size for i in z.infolist()) > MAX_SIZE:
                raise BackupError("Deze back-up is onverwacht groot en wordt niet uitgepakt.")
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                z.extractall(tmp)
                _check_database(tmp / db.DB_NAME)
                safety = create(prefix=SAFETY_PREFIX, keep_also=zip_path)
                _swap(tmp)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        with db.connect() as conn:
            db.set_setting(conn, "backup_last", datetime.now().isoformat(timespec="seconds"))
        return safety


def _copy_db(source: Path, target: Path) -> None:
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def _swap(new: Path) -> None:
    data = db.DATA_DIR
    with db.connect() as conn:  # these settings belong to this pc, not to the backup
        local = {k: db.setting(conn, k) for k in ("backup_folder", "backup_auto", "backup_last")}
    old_db = new / "old-database.db"  # to restore if something below goes wrong
    _copy_db(data / db.DB_NAME, old_db)
    # marker for clean_up() after a crash: from here on the old state may be needed (with the folders that existed)
    (new / "started").write_text(json.dumps([sub for sub in FOLDERS if (data / sub).exists()]), encoding="utf-8")
    aside = []
    try:
        for sub in FOLDERS:
            if (data / sub).exists():
                os.replace(data / sub, data / f"{OLD_PREFIX}{sub}")
                aside.append(sub)
    except OSError:
        for sub in aside:
            os.replace(data / f"{OLD_PREFIX}{sub}", data / sub)
        raise BackupError("Terugzetten kan niet: er staat nog een factuur of document open. "
                          "Sluit die (bijvoorbeeld in je PDF-lezer) en probeer het opnieuw. Er is niets veranderd.")
    placed = []
    try:
        for sub in FOLDERS:
            if (new / sub).exists():
                os.replace(new / sub, data / sub)
                placed.append(sub)
        _copy_db(new / db.DB_NAME, data / db.DB_NAME)
        db.init()  # backup of an older version: migrate first
        with db.connect() as conn:
            for k, v in local.items():
                db.set_setting(conn, k, v or "")
    except Exception as e:
        _copy_db(old_db, data / db.DB_NAME)
        for sub in placed:
            shutil.rmtree(data / sub, ignore_errors=True)
        for sub in aside:
            os.replace(data / f"{OLD_PREFIX}{sub}", data / sub)
        raise BackupError(f"Terugzetten mislukt; je oude gegevens zijn teruggezet. ({e})") from e
    (new / "done").touch()  # the new state is complete; the old folders may go
    for sub in aside:
        shutil.rmtree(data / f"{OLD_PREFIX}{sub}", ignore_errors=True)


def clean_up() -> None:
    """At startup, before db.init(): clean up the leftovers of an interrupted restore. They contain full copies
    of the data (including original invoices). If the restore was interrupted halfway, first put back the old
    state: the folder .oud-facturen can then be the only copy. An error here must not stop the startup."""
    data, tmp = db.DATA_DIR, db.DATA_DIR / RESTORE_TMP
    with _LOCK:
        try:
            if (tmp / "started").exists() and not (tmp / "done").exists():
                existed = json.loads((tmp / "started").read_text(encoding="utf-8"))
                _copy_db(tmp / "old-database.db", data / db.DB_NAME)
                for sub in FOLDERS:
                    if (data / f"{OLD_PREFIX}{sub}").exists():
                        shutil.rmtree(data / sub, ignore_errors=True)
                        os.replace(data / f"{OLD_PREFIX}{sub}", data / sub)
                    elif sub not in existed:  # only placed from the backup
                        shutil.rmtree(data / sub, ignore_errors=True)
                logging.warning("Interrupted restore found: the state from before it has been put back")
            for sub in FOLDERS:
                old = data / f"{OLD_PREFIX}{sub}"
                if old.exists() and not (data / sub).exists():  # without a marker (from before 1.4): never throw away
                    os.replace(old, data / sub)
                shutil.rmtree(old, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001 - the leftovers stay; try again at the next start
            logging.exception("Cleaning up the leftovers of a restore failed")


def _remove_partial_backups(folder: Path) -> None:
    """'.deel' files of a backup that was interrupted (e.g. pc switched off while zipping)."""
    for part in [*folder.glob(f"{PREFIX}*.zip.deel"), *folder.glob(f"{SAFETY_PREFIX}*.zip.deel")]:
        try:
            part.unlink()
        except OSError:
            logging.warning("Partial backup %s could not be deleted", part.name)


def status(conn) -> dict:
    last = db.setting(conn, "backup_last")
    last_dt = datetime.fromisoformat(last) if last else None
    return {
        "folder": target_folder(conn),
        "auto": db.setting(conn, "backup_auto", "on") == "on",
        "last": last_dt,
        "error": db.setting(conn, "backup_error"),
        "too_old": last_dt is None or datetime.now() - last_dt > timedelta(days=WARN_DAYS),
        "same_drive": not os.environ.get("OneDrive") and
                      Path(target_folder(conn)).anchor.lower() == db.DATA_DIR.resolve().anchor.lower(),
    }


def automatic() -> None:
    """At startup: make a backup if the last one is older than INTERVAL_DAYS. Only log errors."""
    try:
        with db.connect() as conn:  # read the state at startup in one go
            folder = target_folder(conn)
            auto = db.setting(conn, "backup_auto", "on") == "on"
            last = db.setting(conn, "backup_last")
            has_data = conn.execute("SELECT 1 FROM vehicle LIMIT 1").fetchone()
        with _LOCK:  # never the .deel file of a backup that is running now
            if folder.is_dir():
                _remove_partial_backups(folder)
        if not auto or not has_data or (last and datetime.now() - datetime.fromisoformat(last) < timedelta(days=INTERVAL_DAYS)):
            return
        create()
        logging.info("Automatic backup made")
    except Exception as e:  # noqa: BLE001 - a failed backup must never stop the app
        logging.exception("Automatic backup failed")
        with db.connect() as conn:
            db.set_setting(conn, "backup_error", str(e)[:300])
