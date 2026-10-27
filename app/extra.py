"""Extensions: backup, odometer readings, documents, specs, storage, cost overview and theme."""
import json
import shutil
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import backup, db, planning
from .web import ALLOWED, delete_files, fetch, mime_for, or_400, parse_int, read_uploads, redirect, render

router = APIRouter()

# Values and labels below are shown in the app, so they are Dutch.
THEMES = {"light": "Licht", "dark": "Donker", "system": "Zoals Windows"}
DOCUMENT_KINDS = ["Kentekenbewijs / kentekencard", "Verzekeringspolis", "Aankoopfactuur", "Garantiebewijs",
                  "Instructieboekje / handleiding", "Keuringsrapport", "Overig"]
SPECS_MOTORCYCLE = ["Motorolie", "Olie-inhoud", "Oliefilter", "Luchtfilter", "Bougies", "Bandenmaat voor",
                    "Bandenmaat achter", "Bandenspanning voor", "Bandenspanning achter", "Ketting",
                    "Tandwielen (voor/achter)", "Remblokken voor", "Remblokken achter", "Accu", "Remvloeistof",
                    "Koelvloeistof"]
SPECS_CAR = ["Motorolie", "Olie-inhoud", "Oliefilter", "Luchtfilter", "Interieurfilter", "Bougies", "Bandenmaat",
             "Bandenspanning voor", "Bandenspanning achter", "Ruitenwissers", "Accu", "Remvloeistof", "Koelvloeistof"]
SPEC_EXAMPLES = {"Motorolie": "bijv. 10W-40 (Motul 7100)", "Olie-inhoud": "bijv. 3,0 L met filter",
                 "Oliefilter": "bijv. HF303", "Bandenmaat voor": "bijv. 120/70 ZR17", "Bandenmaat achter": "bijv. 180/55 ZR17",
                 "Bandenspanning voor": "bijv. 2,5 bar", "Bandenspanning achter": "bijv. 2,9 bar",
                 "Ketting": "bijv. DID 520VX3, 112 schakels", "Accu": "bijv. YTX12-BS", "Bougies": "bijv. NGK CR9EK",
                 "Bandenmaat": "bijv. 205/55 R16"}
STORE_CHECKLIST = ["Tank volgooien (eventueel met brandstofstabilisator)", "Wassen en goed laten drogen",
                   "Ketting reinigen en smeren", "Bandenspanning iets verhogen (ca. 0,5 bar)",
                   "Accu loskoppelen of aan een druppellader", "Droog en vorstvrij wegzetten, eventueel op een paddockstand",
                   "Verzekering: eventueel schorsen of stallingskorting vragen"]
PICKUP_CHECKLIST = ["Accu laden en aansluiten", "Bandenspanning terug op normale waarde",
                    "Olieniveau en lekkage controleren", "Remmen en remvloeistof controleren",
                    "Ketting spannen en smeren", "Verlichting en richtingaanwijzers testen",
                    "Rustig de eerste kilometers rijden (banden en remmen moeten op temperatuur komen)"]


def specs_for(vehicle_type: str) -> list[str]:
    return (SPECS_MOTORCYCLE if vehicle_type in ("Motorfiets", "Bromfiets")
            else SPECS_CAR if vehicle_type in ("Personenauto", "Bedrijfsauto") else [])


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(400, "Vul een geldige datum in.")


# ---------- theme (light / dark / system) ----------

@router.post("/theme")
def set_theme(theme: str = Form(...)):
    if theme not in THEMES:
        raise HTTPException(400, "Onbekende weergave")
    with db.connect() as conn:
        db.set_setting(conn, "theme", theme)
    return {"theme": theme}


# ---------- odometer readings ----------

SOURCE_TEXT = {"service": "bij een onderhoudsbeurt", "reading": "als losse km-stand", "storage_start": "bij het stallen",
               "storage_end": "bij het ophalen uit de stalling"}


def km_check(conn, vid: int, day: str, km: int, except_service: int | None = None) -> str | None:
    """A reading must fit between earlier and later readings (odometers don't run backwards)."""
    for r in db.odometer_history(conn, vid):
        if except_service is not None and r["source"] == "service" and r["id"] == except_service:
            continue
        source = SOURCE_TEXT.get(r["source"], "")
        day_nl = date.fromisoformat(r["date"]).strftime("%d-%m-%Y")
        if r["date"] <= day and r["odometer_km"] > km:
            return (f"{km:,} km is lager dan de {r['odometer_km']:,} km die op {day_nl} al {source} is genoteerd. "
                    "Klopt die eerdere stand niet? Pas hem dan eerst aan (Kosten & km of Stalling).").replace(",", ".")
        if r["date"] > day and r["odometer_km"] < km:
            return (f"{km:,} km is hoger dan de {r['odometer_km']:,} km die later, op {day_nl}, {source} is genoteerd."
                    ).replace(",", ".")
    return None


@router.post("/vehicles/{vid}/odometer")
def add_reading(vid: int, odometer_km: str = Form(...), date_: str = Form("", alias="date"), note: str = Form("")):
    day = _date(date_ or date.today().isoformat())
    km = or_400(parse_int, odometer_km)
    if km is None:
        raise HTTPException(400, "Vul een kilometerstand in.")
    if day > date.today().isoformat():
        raise HTTPException(400, "De datum ligt in de toekomst.")
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)
        if error := km_check(conn, vid, day, km):
            raise HTTPException(400, error)
        conn.execute("INSERT INTO odometer_reading (vehicle_id, date, odometer_km, note) VALUES (?,?,?,?)",
                     (vid, day, km, note.strip() or None))
    return redirect(f"/vehicles/{vid}?km=1")


@router.post("/odometer/{rid}/delete")
def delete_reading(rid: int):
    with db.connect() as conn:
        vid = fetch(conn, "odometer_reading", rid)["vehicle_id"]
        conn.execute("DELETE FROM odometer_reading WHERE id = ?", (rid,))
    return redirect(f"/vehicles/{vid}/costs#odometer")


# ---------- cost and km overview ----------

def _cost_chart(per_year: dict[int, float]) -> dict:
    years = sorted(per_year)
    highest = max(per_year.values(), default=0) or 1
    return {"bars": [{"year": y, "amount": per_year[y], "height": per_year[y] / highest} for y in years],
            "max": highest}


def _km_chart(history: list[dict]) -> dict | None:
    points = [(date.fromisoformat(r["date"]), r["odometer_km"]) for r in history]
    if len(points) < 2:
        return None
    t0, t1 = points[0][0].toordinal(), max(points[-1][0].toordinal(), points[0][0].toordinal() + 1)
    k0, k1 = min(p[1] for p in points), max(p[1] for p in points)
    k1 = k1 if k1 > k0 else k0 + 1
    return {"points": [{"x": (d.toordinal() - t0) / (t1 - t0), "y": (k - k0) / (k1 - k0), "date": d, "km": k}
                       for d, k in points], "min": k0, "max": k1, "start": points[0][0], "end": points[-1][0]}


@router.get("/vehicles/{vid}/costs")
def costs(request: Request, vid: int):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        services = db.services(conn, vid)
        history = db.odometer_history(conn, vid)
        readings = conn.execute("SELECT * FROM odometer_reading WHERE vehicle_id = ? ORDER BY date DESC", (vid,)).fetchall()
    with_cost = [s for s in services if s["cost"]]
    per_year: dict[int, float] = {}
    per_kind: dict[str, float] = {}
    for s in with_cost:
        per_year[int(s["date"][:4])] = per_year.get(int(s["date"][:4]), 0) + s["cost"]
        # a service with several jobs cannot be split fairly: it counts as 'combined'
        kind = s["types"][0]["name"] if len(s["types"]) == 1 else "Beurten met meerdere klussen"
        per_kind[kind] = per_kind.get(kind, 0) + s["cost"]
    total = sum(s["cost"] for s in with_cost)
    driven = (history[-1]["odometer_km"] - history[0]["odometer_km"]) if len(history) >= 2 else 0
    highest_kind = max(per_kind.values(), default=1)
    return render(request, "costs.html", v=v, total=total, count=len(with_cost),
                  per_km=total / driven if driven > 0 and total else None, driven=driven,
                  per_year=_cost_chart(per_year),
                  per_kind=sorted(((k, a, a / highest_kind) for k, a in per_kind.items()), key=lambda x: -x[1]),
                  km=_km_chart(history), history=list(reversed(history)), readings=readings)


# ---------- documents ----------

@router.get("/vehicles/{vid}/documents")
def documents(request: Request, vid: int):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        docs = conn.execute("SELECT * FROM document WHERE vehicle_id = ? ORDER BY kind, added_on DESC", (vid,)).fetchall()
    return render(request, "documents.html", v=v, docs=docs, kinds=DOCUMENT_KINDS, today=date.today().isoformat())


@router.post("/vehicles/{vid}/documents")
async def add_document(vid: int, kind: str = Form(...), title: str = Form(""), expiry_date: str = Form(""),
                       file: UploadFile = File(...)):
    if kind not in DOCUMENT_KINDS:
        raise HTTPException(400, "Kies een soort document.")
    expiry_date = _date(expiry_date) if expiry_date else None
    try:
        uploads = await read_uploads([file])
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not uploads:
        raise HTTPException(400, "Kies een bestand.")
    name, ext, content = uploads[0]
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)
        folder = db.DATA_DIR / "documenten" / str(vid)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex}{ext}"
        path.write_bytes(content)
        try:
            conn.execute("INSERT INTO document (vehicle_id, kind, title, path, original_filename, mime_type, expiry_date,"
                         " added_on) VALUES (?,?,?,?,?,?,?,?)",
                         (vid, kind, title.strip() or None, path.relative_to(db.DATA_DIR).as_posix(),
                          name, ALLOWED[ext][0], expiry_date, date.today().isoformat()))
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return redirect(f"/vehicles/{vid}/documents")


@router.get("/documents/{did}")
def open_document(did: int):
    with db.connect() as conn:
        d = fetch(conn, "document", did)
    try:
        path = db.data_file(d["path"])
    except ValueError:
        raise HTTPException(404, "Bestand niet gevonden")
    if not path.is_file():
        raise HTTPException(404, "Bestand ontbreekt op schijf")
    return FileResponse(path, media_type=mime_for(path), filename=d["original_filename"], content_disposition_type="inline")


@router.post("/documents/{did}/delete")
def delete_document(did: int):
    with db.connect() as conn:
        d = fetch(conn, "document", did)
        conn.execute("DELETE FROM document WHERE id = ?", (did,))
    delete_files([d["path"]])
    return redirect(f"/vehicles/{d['vehicle_id']}/documents")


@router.post("/documents/{did}/expiry")
def set_document_expiry(did: int, expiry_date: str = Form("")):
    with db.connect() as conn:
        d = fetch(conn, "document", did)
        conn.execute("UPDATE document SET expiry_date = ? WHERE id = ?", (_date(expiry_date) if expiry_date else None, did))
    return redirect(f"/vehicles/{d['vehicle_id']}/documents")


# ---------- specs ----------

@router.get("/vehicles/{vid}/specs")
def specs(request: Request, vid: int, saved: int = 0):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        existing = {r["key"]: r["value"] for r in conn.execute(
            "SELECT * FROM spec WHERE vehicle_id = ? ORDER BY position", (vid,))}
    default = specs_for(v["vehicle_type"])
    own = [k for k in existing if k not in default]
    return render(request, "specs.html", v=v, fields=default + own, values=existing,
                  examples=SPEC_EXAMPLES, saved=saved)


@router.post("/vehicles/{vid}/specs")
async def save_specs(request: Request, vid: int):
    form = await request.form()
    keys, values = form.getlist("key"), form.getlist("value")
    if len(keys) != len(values):
        raise HTTPException(400, "Ongeldig formulier")
    rows = {}
    for key, value in zip(keys, values):
        key, value = str(key).strip()[:60], str(value).strip()[:200]
        if key and value and key not in rows:
            rows[key] = value
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)
        conn.execute("DELETE FROM spec WHERE vehicle_id = ?", (vid,))
        conn.executemany("INSERT INTO spec (vehicle_id, key, value, position) VALUES (?,?,?,?)",
                         [(vid, k, v, i) for i, (k, v) in enumerate(rows.items())])
    return redirect(f"/vehicles/{vid}/specs?saved=1")


# ---------- storage (e.g. winter storage) ----------

@router.get("/vehicles/{vid}/storage")
def storage(request: Request, vid: int):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        active = db.active_storage(conn, vid)
        periods = conn.execute("SELECT * FROM storage_period WHERE vehicle_id = ? ORDER BY start_date DESC", (vid,)).fetchall()
        current = db.current_km(conn, vid)
        plan = vehicle_plan(conn, v) if active else []
    return render(request, "storage.html", v=v, active=active, periods=periods, current_km=current,
                  checklist=PICKUP_CHECKLIST if active else STORE_CHECKLIST,
                  overdue=[p for p in plan if p["color"] != "green"], today=date.today().isoformat())


def _storage_km(conn, vid, day, odometer_km):
    km = or_400(parse_int, odometer_km)
    if km is not None and (error := km_check(conn, vid, day, km)):
        raise HTTPException(400, error)
    return km


def _not_in_future(day: str) -> str:
    if day > date.today().isoformat():
        raise HTTPException(400, "De datum ligt in de toekomst.")
    return day


@router.post("/vehicles/{vid}/storage/start")
def start_storage(vid: int, date_: str = Form("", alias="date"), odometer_km: str = Form("")):
    day = _not_in_future(_date(date_ or date.today().isoformat()))
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)
        if db.active_storage(conn, vid):
            raise HTTPException(400, "Dit voertuig staat al in de stalling.")
        previous = conn.execute("SELECT MAX(end_date) FROM storage_period WHERE vehicle_id = ?", (vid,)).fetchone()[0]
        if previous and day < previous:
            raise HTTPException(400, "Deze datum valt in een eerdere stallingsperiode.")
        conn.execute("INSERT INTO storage_period (vehicle_id, start_date, start_km) VALUES (?,?,?)",
                     (vid, day, _storage_km(conn, vid, day, odometer_km)))
    return redirect(f"/vehicles/{vid}")


@router.post("/vehicles/{vid}/storage/end")
def end_storage(vid: int, date_: str = Form("", alias="date"), odometer_km: str = Form("")):
    day = _not_in_future(_date(date_ or date.today().isoformat()))
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)
        active = db.active_storage(conn, vid)
        if not active:
            raise HTTPException(400, "Dit voertuig staat niet in de stalling.")
        if day < active["start_date"]:
            raise HTTPException(400, "De ophaaldatum ligt vóór de stallingsdatum.")
        conn.execute("UPDATE storage_period SET end_date = ?, end_km = ? WHERE id = ?",
                     (day, _storage_km(conn, vid, day, odometer_km), active["id"]))
    return redirect(f"/vehicles/{vid}")


@router.post("/storage/{sid}/delete")
def delete_storage(sid: int):
    """Delete a storage period (e.g. after a typo in the reading)."""
    with db.connect() as conn:
        vid = fetch(conn, "storage_period", sid)["vehicle_id"]
        conn.execute("DELETE FROM storage_period WHERE id = ?", (sid,))
    return redirect(f"/vehicles/{vid}/storage")


# ---------- planning including expiry dates (APK, insurance, ...) ----------

def expiries(conn, v) -> list[tuple[str, str, str]]:
    """(name, date, source) of things with an expiry date: APK from the RDW and documents."""
    items = []
    try:
        apk = json.loads(v["rdw_json"] or "{}").get("apk_expiry_date")
    except ValueError:
        apk = None
    if apk:
        items.append(("APK verloopt", apk, "rdw"))
    for d in conn.execute("SELECT kind, title, expiry_date FROM document WHERE vehicle_id = ? AND expiry_date IS NOT NULL",
                          (v["id"],)):
        items.append((f"{d['title'] or d['kind']} verloopt", d["expiry_date"], "document"))

    def valid(day) -> bool:  # a broken date must never crash a whole page
        try:
            date.fromisoformat(day)
            return True
        except (TypeError, ValueError):
            return False
    return [i for i in items if valid(i[1])]


def vehicle_plan(conn, v, services=None, current_km=None) -> list[dict]:
    services = services if services is not None else db.services(conn, v["id"])
    current_km = current_km if current_km is not None else db.current_km(conn, v["id"])
    plan = planning.overview(services, current_km, intervals=db.intervals(conn, v["id"]))
    plan += [planning.expiry_status(name, day, source) for name, day, source in expiries(conn, v)]
    order = {"red": 0, "orange": 1, "green": 2}
    return sorted(plan, key=lambda p: (order[p["color"]], p["name"]))


# ---------- backup ----------

def backup_context(conn) -> dict:
    st = backup.status(conn)
    return {"backup": st, "backups": backup.all_backups(st["folder"])}


@router.post("/backup/now")
def backup_now():
    try:
        backup.create()
    except backup.BackupError as e:
        with db.connect() as conn:
            db.set_setting(conn, "backup_error", str(e))
        return redirect("/settings?backup=error#backup")
    return redirect("/settings?backup=made#backup")


@router.post("/backup/settings")
def backup_settings(folder: str = Form(""), auto: str = Form("")):
    with db.connect() as conn:
        if folder.strip():
            try:
                db.set_setting(conn, "backup_folder", str(backup.check_target_folder(Path(folder.strip()))))
            except backup.BackupError as e:
                raise HTTPException(400, str(e))
        db.set_setting(conn, "backup_auto", "on" if auto else "off")
    return redirect("/settings?backup=saved#backup")


def _restore(zip_path: Path):
    try:
        safety = backup.restore(zip_path)
    except backup.BackupError as e:
        raise HTTPException(400, str(e))
    return redirect(f"/?restored={safety.name}")


@router.post("/backup/restore")
def restore_backup(name: str = Form(...)):
    with db.connect() as conn:
        folder = backup.target_folder(conn)
    # only a backup from the list (no arbitrary path from outside)
    candidate = next((b["path"] for b in backup.all_backups(folder) if b["name"] == name), None)
    if not candidate:
        raise HTTPException(404, "Back-up niet gevonden")
    return _restore(candidate)


@router.post("/backup/restore-file")
async def restore_backup_file(file: UploadFile = File(...)):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.zip"
        with open(path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        return _restore(path)


# ---------- reminders ----------

@router.post("/reminders")
def reminders(on: str = Form("")):
    with db.connect() as conn:
        db.set_setting(conn, "reminders", "on" if on else "off")
    return redirect("/settings?reminders=saved#reminders")


@router.post("/privacy")
def privacy(sketchfab_autoload: str = Form("")):
    with db.connect() as conn:
        db.set_setting(conn, "sketchfab_autoload", "on" if sketchfab_autoload else "off")
    return redirect("/settings?privacy=saved#privacy")


def check_notifications() -> list[tuple[str, str]]:
    """What needs attention, for a Windows notification (vehicles in storage are skipped).
    Returns (key, text): the key identifies the situation, the text is shown."""
    lines = []
    with db.connect() as conn:
        for v in conn.execute("SELECT * FROM vehicle ORDER BY id").fetchall():
            if db.active_storage(conn, v["id"]):
                continue
            name = v["nickname"] or f"{v['make'] or ''} {v['model'] or ''}".strip() or v["license_plate"]
            for p in vehicle_plan(conn, v):
                if p["color"] != "green":
                    lines.append((f"{v['id']}|{p['name']}|{p['color']}", f"{name}: {p['name'].lower()} ({p['text']})"))
        st = backup.status(conn)
        if st["auto"] and st["too_old"] and conn.execute("SELECT 1 FROM vehicle LIMIT 1").fetchone():
            lines.append(("backup", "Er is al een tijd geen back-up gemaakt."))
    return lines


def _key(lines) -> str:
    """What is going on, without the counting-down text ('nog 9 dagen'), so 'the same notification' is recognizable."""
    return "\n".join(sorted(k for k, _ in lines))


def may_notify(lines) -> bool:
    """At most one notification per day, and the same situation only again after a week."""
    if not lines:
        return False
    with db.connect() as conn:
        if db.setting(conn, "reminders", "on") != "on":
            return False
        try:
            previous = json.loads(db.setting(conn, "last_notification", "{}") or "{}")
            now = datetime.now()
            if previous.get("date") == now.date().isoformat():
                return False
            if previous.get("key") == _key(lines) and (now - datetime.fromisoformat(previous["time"])).days < 7:
                return False
        except (ValueError, KeyError, TypeError):
            pass  # broken bookkeeping: then just notify
    return True


def notified(lines) -> None:
    """Only record this after the notification was really shown (if PowerShell fails, try again tomorrow)."""
    now = datetime.now()
    with db.connect() as conn:
        db.set_setting(conn, "last_notification", json.dumps({"date": now.date().isoformat(), "time": now.isoformat(),
                                                              "key": _key(lines)}))
