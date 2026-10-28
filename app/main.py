import json
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from . import backup, crypto, db, extra, image, planning

# load only when a screen needs them: PyMuPDF, reportlab, pypdf and httpx make the startup slow otherwise
lazy from . import export_pdf, invoice_reader, rdw, redaction
from .web import (ALLOWED, VEHICLE_TYPES, delete_files, fetch, file_response, format_plate, invoice_paths,  # noqa: F401
                  mime_for, or_400, parse_amount, parse_int, read_uploads, redirect, render, templates)

HERE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app):
    backup.clean_up()  # before init: an interrupted restore also puts back the database
    db.init()
    if db.needs_encryption():  # the first start with encryption (P1): first a backup of the state as it was
        try:
            backup.create()
        except backup.BackupError:
            logging.exception("Backup before encrypting failed; encrypting anyway (file by file, nothing is lost)")
        if db.encrypt_existing():
            with db.connect() as conn:  # once: about the readable old versions that OneDrive keeps
                db.set_setting(conn, "encrypted_notice", "on")
    # automatic backup in the background: the startup does not wait for it (tests do, via app.state)
    _app.state.backup_thread = threading.Thread(target=backup.automatic, daemon=True)
    _app.state.backup_thread.start()
    yield


app = FastAPI(title="Garage", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(extra.router)

KEYLESS = ("/recover", "/health", "/favicon.ico", "/static/")  # what works without the master key


@app.middleware("http")  # registered before own_app_only, so it runs after those checks
async def key_present(request: Request, call_next):
    """Without the master key (other pc, Windows reinstalled) nothing can be read or written: every page goes to
    the screen that brings it back with the backup password."""
    if not request.url.path.startswith(KEYLESS) and not db.key_available():
        return redirect("/recover")
    return await call_next(request)


@app.middleware("http")
async def own_app_only(request: Request, call_next):
    """Protection against other websites in your normal browser while Garage is running:
    - Host must be 127.0.0.1/localhost (against DNS rebinding, also for reading files);
    - changing requests (POST) must come from Garage itself (against CSRF)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ALLOWED_HOSTS:
        return Response("Onbekende host", status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        own = f"{request.url.scheme}://{request.headers.get('host')}"
        if (site and site not in ("same-origin", "none")) or (origin and origin != own):
            return Response("Verzoek van buiten Garage geweigerd", status_code=403)
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            return Response("Ongeldig verzoek", status_code=400)
        if size > (MAX_BACKUP if request.url.path == "/backup/restore-file" else MAX_REQUEST):
            return render(request, "error.html", status_code=413, error="Dit bestand is te groot.")
    return await call_next(request)


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}  # 'testserver' = the test client
MAX_REQUEST = 200 * 1024 ** 2  # all invoices of one service together
MAX_BACKUP = backup.MAX_SIZE

# Inline scripts and handlers (onclick, onsubmit) are needed for the templates; the rest is closed: no framing
# (clickjacking), no connections or forms to outside, no plugins.
CSP = "; ".join([
    "default-src 'self'", "script-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: " + " ".join(f"https://{h}" for h in image.ALLOWED_HOSTS),
    "frame-src https://sketchfab.com", "connect-src 'self'", "object-src 'none'", "base-uri 'none'",
    "form-action 'self'", "frame-ancestors 'none'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # same-origin, not no-referrer: with no-referrer Chromium sends 'Origin: null' and the CSRF check refuses that
    response.headers["Referrer-Policy"] = "same-origin"
    if response.headers.get("content-type", "").startswith("text/html"):  # not on PDFs: that hinders the PDF viewer
        response.headers["Content-Security-Policy"] = CSP
    return response


@app.get("/health", include_in_schema=False)
def health():
    """For the app starter: is the server running yet?"""
    return {"app": "garage"}


@app.get("/recover")
def recover(request: Request):
    if db.key_available():
        return redirect("/")
    with db.connect() as conn:
        has_password = bool(db.setting(conn, "master_key_backup"))
    return render(request, "recover.html", has_password=has_password)


@app.post("/recover")
def recover_with_password(request: Request, password: str = Form("")):
    try:
        db.unlock_with_password(password)
    except crypto.CryptoError as e:
        return render(request, "recover.html", status_code=400, has_password=True, error=str(e))
    db.encrypt_existing()  # what was left readable while the key was missing
    return redirect("/")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """Error page in the app layout, except for files and fetch requests."""
    if request.url.path.startswith("/static") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", status_code=exc.status_code, error=str(exc.detail))


@app.exception_handler(RequestValidationError)
async def input_error(request: Request, exc: RequestValidationError):
    return render(request, "error.html", status_code=422, error="Er ontbreekt iets of een veld is ongeldig ingevuld.")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")


# ---------- vehicles ----------

@app.get("/")
def index(request: Request):
    with db.connect() as conn:
        vehicles = [dict(v) for v in conn.execute("""
            SELECT v.*, COUNT(s.id) AS service_count, MAX(s.date) AS last_service
            FROM vehicle v LEFT JOIN service s ON s.vehicle_id = v.id GROUP BY v.id ORDER BY v.id""")]
        attention = {}
        for v in vehicles:
            v["km"] = db.current_km(conn, v["id"])
            v["in_storage"] = db.active_storage(conn, v["id"]) is not None
            attention[v["id"]] = sum(p["color"] != "green" for p in extra.vehicle_plan(conn, v, current_km=v["km"]))
        backup_status = backup.status(conn)
        encrypted_notice = db.setting(conn, "encrypted_notice") == "on"
    return render(request, "index.html", vehicles=vehicles, attention=attention, backup=backup_status,
                  restored=request.query_params.get("restored"), encrypted_notice=encrypted_notice)


@app.post("/notice/encrypted")
def dismiss_encrypted_notice():
    with db.connect() as conn:
        db.set_setting(conn, "encrypted_notice", "")
    return redirect("/")


@app.get("/vehicles/new")
def new_vehicle(request: Request):
    return render(request, "vehicle_form.html", v={}, vehicle_types=VEHICLE_TYPES)


@app.post("/vehicles/lookup")
def lookup_vehicle(request: Request, license_plate: str = Form("")):
    try:
        details = rdw.lookup(license_plate)
    except rdw.RdwError as e:
        return render(request, "vehicle_form.html", v={"license_plate": rdw.normalize(license_plate)},
                      vehicle_types=VEHICLE_TYPES, error=str(e))
    types = VEHICLE_TYPES if details["vehicle_type"] in VEHICLE_TYPES else [details["vehicle_type"], *VEHICLE_TYPES]
    return render(request, "vehicle_form.html", v=details, vehicle_types=types, found=True)


@app.post("/vehicles")
def save_vehicle(
    license_plate: str = Form(...), vehicle_type: str = Form(...), make: str = Form(""), model: str = Form(""),
    first_registration_date: str = Form(""), color: str = Form(""), displacement_cc: str = Form(""),
    nickname: str = Form(""), rdw_json: str = Form(""),
):
    rdw_data = _rdw_extra(rdw_json)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO vehicle (license_plate, vehicle_type, make, model, first_registration_date, color,"
            " displacement_cc, nickname, rdw_json, rdw_updated_on) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rdw.normalize(license_plate), vehicle_type, make.strip(), model.strip(), first_registration_date.strip(),
             color.strip(), or_400(parse_int, displacement_cc), nickname.strip() or None,
             json.dumps(rdw_data) if rdw_data else None, date.today().isoformat() if rdw_data else None),
        )
    return redirect(f"/vehicles/{cur.lastrowid}")


RDW_FIELDS = {"apk_expiry_date", "open_recall", "odometer_judgement", "odometer_year", "list_price",
              "first_registration_nl_date", "curb_weight_kg", "fuel", "power_kw"}


def _rdw_extra(raw: str) -> dict:
    """Extra RDW data from the form; only known fields with simple values."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {k: v for k, v in data.items() if k in RDW_FIELDS and isinstance(v, (str, int, float, bool, type(None)))}
    for k in ("apk_expiry_date", "first_registration_nl_date"):
        try:
            if clean.get(k):
                date.fromisoformat(clean[k])
        except (TypeError, ValueError):
            clean.pop(k)
    return clean


@app.get("/vehicles/{vid}")
def vehicle_detail(request: Request, vid: int):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        services = db.services(conn, vid)
        intervals = db.intervals(conn, vid)
        current_km = db.current_km(conn, vid)
        plan = extra.vehicle_plan(conn, v, services, current_km)
        specs = conn.execute("SELECT * FROM spec WHERE vehicle_id = ? ORDER BY position", (vid,)).fetchall()
        doc_count = conn.execute("SELECT COUNT(*) FROM document WHERE vehicle_id = ?", (vid,)).fetchone()[0]
        storage = db.active_storage(conn, vid)
        last_reading = conn.execute("SELECT * FROM odometer_reading WHERE vehicle_id = ? ORDER BY date DESC, id DESC LIMIT 1",
                                    (vid,)).fetchone()
        sketchfab_autoload = db.setting(conn, "sketchfab_autoload", "off") == "on"
    unreviewed = [f for s in services for f in s["invoices"] if not f["reviewed"]]
    info = _rdw_extra(v["rdw_json"])
    try:  # import: registered in the Netherlands well after the first registration (somewhere else)
        imported = (date.fromisoformat(info["first_registration_nl_date"])
                    - date.fromisoformat(v["first_registration_date"])).days > 30
    except (KeyError, TypeError, ValueError):
        imported = False
    return render(request, "vehicle.html", v=v, services=services, current_km=current_km,
                  planning=plan, specs=specs, doc_count=doc_count, storage=storage,
                  last_reading=last_reading, today=date.today().isoformat(),
                  km_saved=request.query_params.get("km"),
                  unreviewed=unreviewed, rdw_info=info, custom_schedule=bool(intervals),
                  imported=imported, sketchfab_autoload=sketchfab_autoload,
                  km_estimate=planning.km_estimate(v["vehicle_type"], v["first_registration_date"])
                  if current_km is None else None)


@app.post("/vehicles/{vid}/rdw")
def refresh_rdw(request: Request, vid: int):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
    try:
        details = rdw.lookup(v["license_plate"])
    except rdw.RdwError as e:
        raise HTTPException(502, str(e))
    with db.connect() as conn:
        conn.execute("UPDATE vehicle SET rdw_json = ?, rdw_updated_on = ? WHERE id = ?",
                     (json.dumps(_rdw_extra(json.dumps(details["rdw"]))), date.today().isoformat(), vid))
    return redirect(f"/vehicles/{vid}")


@app.get("/vehicles/{vid}/intervals")
def intervals_page(request: Request, vid: int, saved: int = 0):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        types = [t for t in db.types_for(conn, v["vehicle_type"]) if t["name"] != db.OTHER_TYPE]
        custom = db.intervals(conn, vid)
    return render(request, "intervals.html", v=v, types=types, custom=custom, saved=saved)


@app.post("/vehicles/{vid}/intervals")
async def save_intervals(request: Request, vid: int):
    """Fields are called km_<type_id> and months_<type_id>. Equal to the rule of thumb = no custom value stored."""
    form = await request.form()
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        for t in db.types_for(conn, v["vehicle_type"]):
            if f"km_{t['id']}" not in form and f"months_{t['id']}" not in form:
                continue  # not sent: leave it as it is
            km = or_400(parse_int, form.get(f"km_{t['id']}"))
            months = or_400(parse_int, form.get(f"months_{t['id']}"))
            conn.execute("DELETE FROM vehicle_interval WHERE vehicle_id = ? AND type_id = ?", (vid, t["id"]))
            if (km, months) != (t["interval_km"], t["interval_months"]):
                conn.execute("INSERT INTO vehicle_interval (vehicle_id, type_id, interval_km, interval_months)"
                             " VALUES (?,?,?,?)", (vid, t["id"], km, months))
        conn.execute("UPDATE vehicle SET schedule_source = ? WHERE id = ?",
                     ((form.get("schedule_source") or "").strip() or None, vid))
    return redirect(f"/vehicles/{vid}/intervals?saved=1")


@app.post("/vehicles/{vid}/read-invoice")
async def read_invoice(vid: int, invoices: list[UploadFile] = File([])):
    """Stateless: reads the invoices and returns suggestions for the form. Nothing is stored."""
    with db.connect() as conn:
        types = db.types_for(conn, fetch(conn, "vehicle", vid)["vehicle_type"])
    try:
        uploads = await read_uploads(invoices)
    except ValueError as e:
        return {"readable": False, "error": str(e)}
    result = invoice_reader.combine([invoice_reader.read(content, ALLOWED[ext][0], types)
                                     for _, ext, content in uploads])
    names = {t["id"]: t["name"] for t in types}
    result["type_names"] = [names[t] for t in result.get("types", [])]
    return result


@app.post("/vehicles/{vid}/delete")
def delete_vehicle(vid: int):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        paths = invoice_paths(conn.execute(
            "SELECT i.* FROM invoice i JOIN service s ON s.id = i.service_id WHERE s.vehicle_id = ?", (vid,)))
        paths += [d["path"] for d in conn.execute("SELECT path FROM document WHERE vehicle_id = ?", (vid,))]
        conn.execute("DELETE FROM vehicle WHERE id = ?", (vid,))
    delete_files(paths + [v["image_path"]])
    return redirect("/")


@app.get("/vehicles/{vid}/export.pdf")
def export_vehicle(vid: int, costs: int = 1):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        services = db.services(conn, vid)
        km = db.odometer_history(conn, vid)
        specs = conn.execute("SELECT * FROM spec WHERE vehicle_id = ? ORDER BY position", (vid,)).fetchall()
    pdf = export_pdf.create(v, services, with_costs=bool(costs), km=km, specs=specs)
    name = f"onderhoudshistorie_{v['license_plate']}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- vehicle image (3D / photo) ----------

@app.get("/vehicles/{vid}/image")
def choose_image(request: Request, vid: int, q: str = ""):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
    if q.strip():
        models, photos, error = image.search_both(lambda: image.search_3d(q.strip()), lambda: image.search_photos(q.strip()))
    else:
        models, photos, error = image.search_all(v["make"], v["model"])
    query = q.strip() or " ".join(filter(None, [v["make"], v["model"]]))
    return render(request, "image.html", v=v, models=models, photos=photos, error=error, query=query)


def _set_image(vid: int, kind: str | None, content: bytes | None, ref=None, title=None, source=None):
    path = f"beelden/{uuid.uuid4().hex}.jpg" if content else None
    if path:
        db.write_file(path, content)
    try:
        with db.connect() as conn:
            old = fetch(conn, "vehicle", vid)["image_path"]
            conn.execute("UPDATE vehicle SET image_type=?, image_ref=?, image_title=?, image_source=?, image_path=? WHERE id=?",
                         (kind, ref, title, source, path, vid))
    except Exception:
        delete_files([path])  # no orphaned file
        raise
    delete_files([old])


@app.post("/vehicles/{vid}/image")
def save_image(request: Request, vid: int, kind: str = Form(...), thumb: str = Form(...), uid: str = Form(""),
               title: str = Form(""), source: str = Form("")):
    if kind not in ("3d", "photo") or (kind == "3d" and not re.fullmatch(r"[0-9a-f]{32}", uid)):
        raise HTTPException(400, "Ongeldige keuze")
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)  # check first, download after (no orphaned files)
    try:
        content = image.download(thumb)
    except image.ImageError as e:
        raise HTTPException(502, str(e))
    if kind == "3d":
        source = f"https://sketchfab.com/3d-models/{uid}"
    elif not source.startswith("https://commons.wikimedia.org/"):
        source = None
    _set_image(vid, kind, content, uid or None, title.strip() or None, source)
    return redirect(f"/vehicles/{vid}")


@app.post("/vehicles/{vid}/image/upload")
async def upload_image(vid: int, photo: UploadFile = File(...)):
    with db.connect() as conn:
        fetch(conn, "vehicle", vid)
    try:
        content = image.own_photo(await photo.read())
    except image.ImageError as e:
        raise HTTPException(400, str(e))
    _set_image(vid, "own", content, title="Eigen foto")
    return redirect(f"/vehicles/{vid}")


@app.post("/vehicles/{vid}/image/delete")
def delete_image(vid: int):
    _set_image(vid, None, None)
    return redirect(f"/vehicles/{vid}")


@app.get("/vehicles/{vid}/image.jpg")
def image_file(vid: int):
    with db.connect() as conn:
        path = fetch(conn, "vehicle", vid)["image_path"]
    try:
        content = db.read_file(path)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Geen beeld")
    # pages request this with ?v=<file name> (web.image_url); that url changes with every new image
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------- services ----------

def service_form(request: Request, conn, v, service=None, chosen=(), error=None):
    types = db.types_for(conn, v["vehicle_type"])
    # always show types that are already linked, even if the type no longer belongs to this vehicle type
    shown = {t["id"] for t in types}
    other = [t for t in conn.execute("SELECT * FROM maintenance_type ORDER BY name")
             if t["id"] in chosen and t["id"] not in shown]
    return render(request, "service_form.html", status_code=400 if error else 200, v=v, service=service or {},
                  types=types + other, chosen=set(chosen), error=error,
                  invoices=conn.execute("SELECT * FROM invoice WHERE service_id = ?", (service["id"],)).fetchall()
                  if service and service.get("id") else [],
                  today=date.today().isoformat(), current_km=db.current_km(conn, v["id"]))


@app.get("/vehicles/{vid}/services/new")
def new_service(request: Request, vid: int):
    with db.connect() as conn:
        return service_form(request, conn, fetch(conn, "vehicle", vid))


@app.get("/services/{sid}/edit")
def edit_service(request: Request, sid: int):
    with db.connect() as conn:
        s = fetch(conn, "service", sid)
        chosen = [r["type_id"] for r in conn.execute(
            "SELECT type_id FROM service_maintenance_type WHERE service_id = ?", (sid,))]
        return service_form(request, conn, fetch(conn, "vehicle", s["vehicle_id"]), dict(s), chosen)


async def save_service(request: Request, vid: int, sid: int | None, form_data: dict, files: list[UploadFile]):
    with db.connect() as conn:
        v = fetch(conn, "vehicle", vid)
        existing_types = {r["id"] for r in conn.execute("SELECT id FROM maintenance_type")}
        type_ids = [int(t) for t in form_data["types"] if t.isdigit() and int(t) in existing_types]
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", form_data["date"]):
                raise ValueError("Vul een geldige datum in.")
            date.fromisoformat(form_data["date"])
            if not type_ids:
                raise ValueError("Kies minimaal één onderhoudstype.")
            km, cost = parse_int(form_data["odometer_km"]), parse_amount(form_data["cost"])
            if km is not None and (error := extra.km_check(conn, vid, form_data["date"], km, except_service=sid)):
                raise ValueError(error)
            uploads = await read_uploads(files)
        except ValueError as e:
            return service_form(request, conn, v, {**form_data, "id": sid}, type_ids, str(e))

        fields = (form_data["date"], km, form_data["performed_by"].strip() or None, cost,
                  form_data["note"].strip() or None)
        if sid is None:
            sid = conn.execute("INSERT INTO service (date, odometer_km, performed_by, cost, note, vehicle_id)"
                               " VALUES (?,?,?,?,?,?)", (*fields, vid)).lastrowid
        else:
            conn.execute("UPDATE service SET date=?, odometer_km=?, performed_by=?, cost=?, note=? WHERE id=?",
                         (*fields, sid))
            conn.execute("DELETE FROM service_maintenance_type WHERE service_id = ?", (sid,))
        conn.executemany("INSERT INTO service_maintenance_type (service_id, type_id) VALUES (?,?)",
                         [(sid, t) for t in type_ids])

        written = []
        try:
            for name, ext, content in uploads:
                written.append(f"facturen/{vid}/{uuid.uuid4().hex}{ext}")
                db.write_file(written[-1], content, conn)
                conn.execute("INSERT INTO invoice (service_id, path, original_filename, mime_type) VALUES (?,?,?,?)",
                             (sid, written[-1], db.encrypt_text(name, "original_filename"), ALLOWED[ext][0]))
        except Exception:
            delete_files(written, conn)  # the database is rolled back, so the files go too
            raise
    # new invoices: straight on to the redaction screen for personal data
    return redirect(f"/services/{sid}/redact" if uploads else f"/vehicles/{vid}")


def _form(date_, odometer_km, performed_by, cost, note, types):
    return {"date": date_, "odometer_km": odometer_km, "performed_by": performed_by, "cost": cost, "note": note,
            "types": types}


@app.post("/vehicles/{vid}/services/new")
async def save_new_service(
    request: Request, vid: int, date_: str = Form(..., alias="date"), odometer_km: str = Form(""),
    performed_by: str = Form(""), cost: str = Form(""), note: str = Form(""),
    types: list[str] = Form([]), invoices: list[UploadFile] = File([]),
):
    return await save_service(request, vid, None, _form(date_, odometer_km, performed_by, cost, note, types), invoices)


@app.post("/services/{sid}/edit")
async def save_edited_service(
    request: Request, sid: int, date_: str = Form(..., alias="date"), odometer_km: str = Form(""),
    performed_by: str = Form(""), cost: str = Form(""), note: str = Form(""),
    types: list[str] = Form([]), invoices: list[UploadFile] = File([]),
):
    with db.connect() as conn:
        vid = fetch(conn, "service", sid)["vehicle_id"]
    return await save_service(request, vid, sid, _form(date_, odometer_km, performed_by, cost, note, types), invoices)


@app.post("/services/{sid}/delete")
def delete_service(sid: int):
    with db.connect() as conn:
        vid = fetch(conn, "service", sid)["vehicle_id"]
        paths = invoice_paths(conn.execute("SELECT * FROM invoice WHERE service_id = ?", (sid,)))
        conn.execute("DELETE FROM service WHERE id = ?", (sid,))
    delete_files(paths)
    return redirect(f"/vehicles/{vid}")


# ---------- invoices ----------

@app.get("/invoices/{iid}")
def open_invoice(iid: int):
    """The version that gets shared: redacted if that has been done."""
    with db.connect() as conn:
        f = fetch(conn, "invoice", iid)
    return file_response(f["path"], f["original_filename"], "Factuurbestand")


@app.get("/invoices/{iid}/original")
def open_original_invoice(iid: int):
    with db.connect() as conn:
        f = fetch(conn, "invoice", iid)
    return file_response(f["original_path"] or f["path"], f["original_filename"], "Factuurbestand")


@app.post("/invoices/{iid}/delete")
def delete_invoice(iid: int):
    with db.connect() as conn:
        f = fetch(conn, "invoice", iid)
        conn.execute("DELETE FROM invoice WHERE id = ?", (iid,))
    delete_files(invoice_paths([f]))
    return redirect(f"/services/{f['service_id']}/edit")


# ---------- redacting personal data ----------

@app.get("/services/{sid}/redact")
def redact_service(sid: int):
    """Goes through the not yet reviewed invoices of a service one by one."""
    with db.connect() as conn:
        s = fetch(conn, "service", sid)
        f = conn.execute("SELECT id FROM invoice WHERE service_id = ? AND reviewed = 0 ORDER BY id", (sid,)).fetchone()
    return redirect(f"/invoices/{f['id']}/redact" if f else f"/vehicles/{s['vehicle_id']}")


@app.get("/invoices/{iid}/redact")
def redact_invoice(request: Request, iid: int):
    with db.connect() as conn:
        f = fetch(conn, "invoice", iid)
        s = fetch(conn, "service", f["service_id"])
        v = fetch(conn, "vehicle", s["vehicle_id"])
        terms = db.private_terms(conn)
        remaining = conn.execute("SELECT COUNT(*) FROM invoice WHERE service_id = ? AND reviewed = 0 AND id != ?",
                                 (s["id"], iid)).fetchone()[0]
    original = _original(f)
    # boxes applied earlier are on again, so redacting again never makes anything visible again
    earlier = [{**b, "reason": "eerder gelakt", "active": True} for b in json.loads(f["redactions"] or "[]")]
    try:
        suggestions = redaction.suggestions(original, f["mime_type"], terms)
    except redaction.RedactionError as e:
        raise HTTPException(400, str(e))
    return render(request, "redact.html", f=f, v=v, remaining=remaining, has_terms=bool(terms),
                  pages=redaction.page_count(original, f["mime_type"]), suggestions=earlier + suggestions)


def _original(f) -> bytes:
    try:
        return db.read_file(f["original_path"] or f["path"])
    except ValueError:
        raise HTTPException(404, "Factuurbestand niet gevonden")
    except FileNotFoundError:
        raise HTTPException(404, "Factuurbestand ontbreekt op schijf")


@app.get("/invoices/{iid}/page/{nr}.png")
def invoice_page(iid: int, nr: int):
    with db.connect() as conn:
        f = fetch(conn, "invoice", iid)
    original = _original(f)
    if nr < 0 or nr >= redaction.page_count(original, f["mime_type"]):
        raise HTTPException(404, "Pagina bestaat niet")
    try:
        png = redaction.page_png(original, f["mime_type"], nr)
    except redaction.RedactionError as e:
        raise HTTPException(400, str(e))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


def _read_boxes(raw: str, pages: int) -> list[dict]:
    try:
        boxes = json.loads(raw or "[]")
        clean = []
        for b in boxes:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(b[k]))) for k in ("x0", "y0", "x1", "y1"))
            page = int(b["page"])
            if 0 <= page < pages and x1 > x0 and y1 > y0:
                clean.append({"page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        return clean
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, "Ongeldige balken")


@app.post("/invoices/{iid}/redact")
def save_redaction(iid: int, redactions: str = Form("[]")):
    with db.connect() as conn:
        f = fetch(conn, "invoice", iid)
    original = _original(f)
    original_rel = PurePosixPath(f["original_path"] or f["path"])
    clean = _read_boxes(redactions, redaction.page_count(original, f["mime_type"]))
    try:
        redacted = redaction.apply(original, f["mime_type"], clean)
    except redaction.RedactionError as e:
        raise HTTPException(400, str(e))
    target = original_rel.with_name(f"{uuid.uuid4().hex}-gelakt{original_rel.suffix}").as_posix()
    db.write_file(target, redacted)
    old_redacted = f["path"] if f["original_path"] else None
    try:
        with db.connect() as conn:
            conn.execute("UPDATE invoice SET path = ?, original_path = ?, reviewed = 1, redactions = ? WHERE id = ?",
                         (target, original_rel.as_posix(), json.dumps(clean), iid))
    except Exception:
        delete_files([target])  # no orphaned file
        raise
    delete_files([old_redacted])
    return redirect(f"/services/{f['service_id']}/redact")


# ---------- settings ----------

@app.get("/settings")
def settings(request: Request, saved: int = 0):
    with db.connect() as conn:
        types = conn.execute("SELECT * FROM maintenance_type ORDER BY name = ?, name", (db.OTHER_TYPE,)).fetchall()
        terms = db.private_terms_text(conn)
        other = {**extra.backup_context(conn), "reminders": db.setting(conn, "reminders", "on") == "on",
                 "sketchfab_autoload": db.setting(conn, "sketchfab_autoload", "off") == "on"}
    groups = [("*", "Alle voertuigen"), (db.MOTORCYCLE, "Motoren en brommers"), (db.CAR, "Auto's en bedrijfsauto's"),
              *[(t, t) for t in VEHICLE_TYPES]]
    return render(request, "settings.html", types=types, groups=groups, terms=terms,
                  saved=saved, themes=extra.THEMES, message=request.query_params, **other)


@app.get("/maintenance-types")
def maintenance_types():
    return redirect("/settings#types")


@app.post("/settings/private")
def save_private_terms(terms: str = Form("")):
    with db.connect() as conn:
        db.set_private_terms(conn, "\n".join(t.strip() for t in terms.splitlines() if t.strip()))
    return redirect("/settings?saved=1#private")


@app.post("/maintenance-types")
def save_maintenance_type(
    id: str = Form(""), name: str = Form(...), vehicle_types: list[str] = Form([]),
    interval_km: str = Form(""), interval_months: str = Form(""),
):
    vehicle_types = [t for v in vehicle_types for t in v.split(",") if t]
    types = "*" if not vehicle_types or "*" in vehicle_types else ",".join(dict.fromkeys(vehicle_types))
    values = (name.strip(), types, or_400(parse_int, interval_km), or_400(parse_int, interval_months))
    with db.connect() as conn:
        if id:
            conn.execute("UPDATE maintenance_type SET name=?, vehicle_types=?, interval_km=?, interval_months=? WHERE id=?",
                         (*values, int(id)))
        else:
            conn.execute("INSERT INTO maintenance_type (name, vehicle_types, interval_km, interval_months) VALUES (?,?,?,?)",
                         values)
    return redirect("/settings#types")
