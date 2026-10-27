"""Back-up en terugzetten van alle gegevens als één zip: database + facturen + afbeeldingen + documenten."""
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

MAPPEN = ("facturen", "beelden", "documenten")
VOORVOEGSEL = "Garage-backup-"
VEILIGHEID = "Garage-veiligheidskopie-"  # automatisch gemaakt vlak voor een terugzetting
BEWAAR_VEILIGHEID = 3
MAX_GROOTTE = 4 * 1024 ** 3  # uitgepakte grootte; bescherming tegen 'zip-bommen'
_SLOT = threading.RLock()     # nooit twee back-ups of een back-up tijdens een terugzetting
BEWAAR = 10           # zoveel back-ups blijven er in de back-upmap staan
INTERVAL_DAGEN = 7    # automatische back-up
WAARSCHUW_DAGEN = 30  # melding in de app als de laatste back-up ouder is


class BackupFout(Exception):
    pass


def standaard_map() -> Path:
    """OneDrive als die er is (dan staat de back-up ook in de cloud), anders de gebruikersmap."""
    onedrive = os.environ.get("OneDrive")
    basis = Path(onedrive) if onedrive and Path(onedrive).is_dir() else Path.home()
    return basis / "Garage back-ups"


def doelmap(conn) -> Path:
    return Path(db.instelling(conn, "backup_map") or standaard_map())


def controleer_doelmap(map_: Path) -> Path:
    map_ = Path(map_).expanduser()
    tekst = str(map_)
    if tekst.startswith(("\\\\", "//")) or map_.drive.startswith(("\\\\", "//")):
        raise BackupFout("Een netwerkmap (\\\\server\\...) kan niet; kies een map op deze pc, een USB-stick of je OneDrive-map.")
    if not map_.is_absolute():
        raise BackupFout("Kies een volledige map, bijvoorbeeld D:\\Back-ups.")
    data = db.DATA_DIR.resolve()
    if map_.resolve() == data or data in map_.resolve().parents:
        raise BackupFout("De back-upmap mag niet in de gegevensmap van Garage zelf liggen.")
    try:
        map_.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupFout(f"Map kan niet worden gemaakt: {e.strerror or e}")
    return map_


def lijst(map_: Path, voorvoegsel: str = VOORVOEGSEL) -> list[dict]:
    if not map_.is_dir():
        return []
    zips = sorted(map_.glob(f"{voorvoegsel}*.zip"), key=lambda p: p.name, reverse=True)
    return [{"naam": p.name, "pad": p, "grootte": p.stat().st_size, "veiligheid": voorvoegsel == VEILIGHEID,
             "datum": datetime.fromtimestamp(p.stat().st_mtime)} for p in zips]


def alle(map_: Path) -> list[dict]:
    """Gewone back-ups en veiligheidskopieën (van vóór een terugzetting), nieuwste eerst."""
    return sorted(lijst(map_) + lijst(map_, VEILIGHEID), key=lambda b: b["datum"], reverse=True)


def maak(map_: Path | None = None, voorvoegsel: str = VOORVOEGSEL, behalve: Path | None = None) -> Path:
    """Maakt een back-up; schrijft eerst naar een tijdelijk bestand zodat er nooit een halve zip achterblijft."""
    with _SLOT:
        with db.connect() as conn:
            map_ = controleer_doelmap(map_ or doelmap(conn))
        basis = f"{voorvoegsel}{datetime.now():%Y-%m-%d_%H%M%S}"
        naam, volgnummer = f"{basis}.zip", 1
        while (map_ / naam).exists():  # nooit een bestaande back-up overschrijven (bijv. twee in dezelfde seconde)
            volgnummer += 1
            naam = f"{basis}_{volgnummer}.zip"
        deel = map_ / (naam + ".deel")
        aantal = 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                kopie = Path(tmp) / "onderhoud.db"
                _kopieer_db(db.DATA_DIR / "onderhoud.db", kopie)  # consistente kopie, ook als de app tegelijk schrijft
                with zipfile.ZipFile(deel, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(kopie, "onderhoud.db")
                    for sub in MAPPEN:
                        for bestand in sorted((db.DATA_DIR / sub).rglob("*")) if (db.DATA_DIR / sub).is_dir() else []:
                            if bestand.is_file():
                                z.write(bestand, bestand.relative_to(db.DATA_DIR).as_posix())
                                aantal += 1
                    z.writestr("garage-backup.json", json.dumps({"gemaakt": datetime.now().isoformat(timespec="seconds"),
                                                                 "bestanden": aantal}))
            os.replace(deel, map_ / naam)
        except Exception as e:
            deel.unlink(missing_ok=True)
            if isinstance(e, BackupFout):
                raise
            raise BackupFout(f"Back-up mislukt: {e}") from e
        # oude opruimen: veiligheidskopieën tellen apart, zodat ze nooit goede back-ups wegdrukken
        for oud in lijst(map_, voorvoegsel)[BEWAAR if voorvoegsel == VOORVOEGSEL else BEWAAR_VEILIGHEID:]:
            if behalve is None or oud["pad"].resolve() != Path(behalve).resolve():
                try:
                    oud["pad"].unlink(missing_ok=True)
                except OSError:
                    logging.warning("Oude back-up %s kon niet worden verwijderd", oud["naam"])
        if voorvoegsel == VOORVOEGSEL:
            with db.connect() as conn:
                db.zet_instelling(conn, "backup_laatste", datetime.now().isoformat(timespec="seconds"))
                db.zet_instelling(conn, "backup_fout", "")
        return map_ / naam


def _veilige_naam(naam: str) -> bool:
    pad = PurePosixPath(naam)
    return (not pad.is_absolute() and ".." not in pad.parts and ":" not in naam and "\\" not in naam
            and (naam in ("onderhoud.db", "garage-backup.json") or pad.parts[0] in MAPPEN))


def _controleer_database(pad: Path) -> None:
    test = sqlite3.connect(pad)
    try:
        if db.schemaversie(test) > db.SCHEMAVERSIE:
            raise BackupFout("Deze back-up komt van een nieuwere versie van Garage. Installeer eerst de nieuwste versie.")
        ok = test.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and \
            test.execute("SELECT 1 FROM sqlite_master WHERE name = 'voertuig'").fetchone() is not None
        if ok:
            kolommen = {r[1] for r in test.execute("PRAGMA table_info(factuur)")}
            if {"pad", "origineel_pad"} <= kolommen and test.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'document'").fetchone():
                ok = not db.ongeldige_paden(test) and all(r[0] in db.MIME_TYPES for r in test.execute(
                    "SELECT mime_type FROM factuur UNION ALL SELECT mime_type FROM document"))
            else:  # back-up van een oudere versie: alleen de kolommen die er zijn
                ok = all(db.GELDIG_PAD.fullmatch(r[0] or "") and r[1] in db.MIME_TYPES
                         for r in test.execute("SELECT pad, mime_type FROM factuur"))
    except sqlite3.DatabaseError:
        ok = False
    finally:
        test.close()
    if not ok:
        raise BackupFout("De database in deze back-up is beschadigd of bevat ongeldige gegevens.")


def herstel(zip_pad: Path) -> Path:
    """Zet een back-up terug; geeft de veiligheidskopie van de huidige stand terug.

    Volgorde: uitpakken en controleren -> veiligheidskopie -> huidige mappen opzij zetten (faalt snel als er een
    bestand open staat) -> nieuwe mappen + database plaatsen. Gaat er onderweg iets mis, dan wordt alles
    teruggedraaid naar de oude stand."""
    with _SLOT:
        try:
            z = zipfile.ZipFile(zip_pad)
        except (zipfile.BadZipFile, OSError):
            raise BackupFout("Dit is geen geldige Garage-back-up (geen zip-bestand).")
        tijdelijk = db.DATA_DIR / ".herstel-tmp"
        with z:
            namen = z.namelist()
            if "onderhoud.db" not in namen or not all(_veilige_naam(n) for n in namen):
                raise BackupFout("Dit is geen geldige Garage-back-up.")
            if sum(i.file_size for i in z.infolist()) > MAX_GROOTTE:
                raise BackupFout("Deze back-up is onverwacht groot en wordt niet uitgepakt.")
            shutil.rmtree(tijdelijk, ignore_errors=True)
            try:
                z.extractall(tijdelijk)
                _controleer_database(tijdelijk / "onderhoud.db")
                veiligheid = maak(voorvoegsel=VEILIGHEID, behalve=zip_pad)
                _wissel(tijdelijk)
            finally:
                shutil.rmtree(tijdelijk, ignore_errors=True)
        with db.connect() as conn:
            db.zet_instelling(conn, "backup_laatste", datetime.now().isoformat(timespec="seconds"))
        return veiligheid


def _kopieer_db(van: Path, naar: Path) -> None:
    bron, doel = sqlite3.connect(van), sqlite3.connect(naar)
    try:
        bron.backup(doel)
    finally:
        bron.close()
        doel.close()


def _wissel(nieuw: Path) -> None:
    data = db.DATA_DIR
    with db.connect() as conn:  # deze instellingen horen bij deze pc, niet bij de back-up
        lokaal = {k: db.instelling(conn, k) for k in ("backup_map", "backup_auto", "backup_laatste")}
    oude_db = nieuw / "oud-onderhoud.db"  # om terug te zetten als het hieronder misgaat
    _kopieer_db(data / "onderhoud.db", oude_db)
    # markering voor opruimen() bij een crash: vanaf hier kan de oude stand nodig zijn (met de mappen die er waren)
    (nieuw / "begonnen").write_text(json.dumps([sub for sub in MAPPEN if (data / sub).exists()]), encoding="utf-8")
    opzij = []
    try:
        for sub in MAPPEN:
            if (data / sub).exists():
                os.replace(data / sub, data / f".oud-{sub}")
                opzij.append(sub)
    except OSError:
        for sub in opzij:
            os.replace(data / f".oud-{sub}", data / sub)
        raise BackupFout("Terugzetten kan niet: er staat nog een factuur of document open. "
                         "Sluit die (bijvoorbeeld in je PDF-lezer) en probeer het opnieuw. Er is niets veranderd.")
    geplaatst = []
    try:
        for sub in MAPPEN:
            if (nieuw / sub).exists():
                os.replace(nieuw / sub, data / sub)
                geplaatst.append(sub)
        _kopieer_db(nieuw / "onderhoud.db", data / "onderhoud.db")
        db.init()  # back-up van een oudere versie: eerst migreren
        with db.connect() as conn:
            for k, v in lokaal.items():
                db.zet_instelling(conn, k, v or "")
    except Exception as e:
        _kopieer_db(oude_db, data / "onderhoud.db")
        for sub in geplaatst:
            shutil.rmtree(data / sub, ignore_errors=True)
        for sub in opzij:
            os.replace(data / f".oud-{sub}", data / sub)
        raise BackupFout(f"Terugzetten mislukt; je oude gegevens zijn teruggezet. ({e})") from e
    (nieuw / "klaar").touch()  # de nieuwe stand is compleet; de oude mappen mogen weg
    for sub in opzij:
        shutil.rmtree(data / f".oud-{sub}", ignore_errors=True)


def opruimen() -> None:
    """Bij het starten, vóór db.init(): restanten van een afgebroken terugzetting opruimen. Die bevatten volledige
    kopieën van de gegevens (ook originele facturen). Is het terugzetten halverwege afgebroken, dan eerst de oude
    stand terugzetten: de map .oud-facturen kan dan de enige kopie zijn. Een fout hier mag het starten niet tegenhouden."""
    data, tijdelijk = db.DATA_DIR, db.DATA_DIR / ".herstel-tmp"
    with _SLOT:
        try:
            if (tijdelijk / "begonnen").exists() and not (tijdelijk / "klaar").exists():
                bestond = json.loads((tijdelijk / "begonnen").read_text(encoding="utf-8"))
                _kopieer_db(tijdelijk / "oud-onderhoud.db", data / "onderhoud.db")
                for sub in MAPPEN:
                    if (data / f".oud-{sub}").exists():
                        shutil.rmtree(data / sub, ignore_errors=True)
                        os.replace(data / f".oud-{sub}", data / sub)
                    elif sub not in bestond:  # alleen uit de back-up geplaatst
                        shutil.rmtree(data / sub, ignore_errors=True)
                logging.warning("Afgebroken terugzetting gevonden: de stand van daarvoor is teruggezet")
            for sub in MAPPEN:
                oud = data / f".oud-{sub}"
                if oud.exists() and not (data / sub).exists():  # zonder markering (van vóór 1.4): nooit iets weggooien
                    os.replace(oud, data / sub)
                shutil.rmtree(oud, ignore_errors=True)
            shutil.rmtree(tijdelijk, ignore_errors=True)
        except Exception:  # noqa: BLE001 - de restanten blijven staan; volgende start opnieuw proberen
            logging.exception("Restanten van een terugzetting opruimen mislukt")


def _halve_back_ups_weg(map_: Path) -> None:
    """'.deel'-bestanden van een back-up die is afgebroken (bijv. pc uitgezet tijdens het inpakken)."""
    for deel in [*map_.glob(f"{VOORVOEGSEL}*.zip.deel"), *map_.glob(f"{VEILIGHEID}*.zip.deel")]:
        try:
            deel.unlink()
        except OSError:
            logging.warning("Halve back-up %s kon niet worden verwijderd", deel.name)


def status(conn) -> dict:
    laatste = db.instelling(conn, "backup_laatste")
    laatste_dt = datetime.fromisoformat(laatste) if laatste else None
    return {
        "map": doelmap(conn),
        "auto": db.instelling(conn, "backup_auto", "aan") == "aan",
        "laatste": laatste_dt,
        "fout": db.instelling(conn, "backup_fout"),
        "te_oud": laatste_dt is None or datetime.now() - laatste_dt > timedelta(days=WAARSCHUW_DAGEN),
        "zelfde_schijf": not os.environ.get("OneDrive") and
                         Path(doelmap(conn)).anchor.lower() == db.DATA_DIR.resolve().anchor.lower(),
    }


def automatisch() -> None:
    """Bij het starten: back-up maken als de laatste ouder is dan INTERVAL_DAGEN. Fouten alleen vastleggen."""
    try:
        with db.connect() as conn:  # stand bij het starten in één keer uitlezen
            map_ = doelmap(conn)
            auto = db.instelling(conn, "backup_auto", "aan") == "aan"
            laatste = db.instelling(conn, "backup_laatste")
            heeft_gegevens = conn.execute("SELECT 1 FROM voertuig LIMIT 1").fetchone()
        with _SLOT:  # nooit het .deel-bestand van een back-up die nu loopt
            if map_.is_dir():
                _halve_back_ups_weg(map_)
        if not auto or not heeft_gegevens or (laatste and datetime.now() - datetime.fromisoformat(laatste) < timedelta(days=INTERVAL_DAGEN)):
            return
        maak()
        logging.info("Automatische back-up gemaakt")
    except Exception as e:  # noqa: BLE001 - een mislukte back-up mag de app nooit tegenhouden
        logging.exception("Automatische back-up mislukt")
        with db.connect() as conn:
            db.zet_instelling(conn, "backup_fout", str(e)[:300])
