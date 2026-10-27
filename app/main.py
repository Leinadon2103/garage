import json
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from . import backup, beeld, db, extra, planning

# pas laden als een scherm ze nodig heeft: PyMuPDF, reportlab, pypdf en httpx maken het opstarten anders traag
lazy from . import export_pdf, factuur_lezen, lakken, rdw
from .web import (TOEGESTAAN, VOERTUIGSOORTEN, _of_400, bedrag, factuur_paden, getal, haal, kenteken_opmaak,  # noqa: F401
                  lees_uploads, mime_voor, render, templates, terug, verwijder_bestanden)

HIER = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app):
    db.init()
    # automatische back-up op de achtergrond: het opstarten wacht er niet op
    threading.Thread(target=backup.automatisch, daemon=True).start()
    yield


app = FastAPI(title="Voertuigonderhoud", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
app.include_router(extra.router)


@app.middleware("http")
async def alleen_eigen_app(request: Request, call_next):
    """Bescherming tegen andere websites in je gewone browser terwijl Garage draait:
    - Host moet 127.0.0.1/localhost zijn (tegen DNS-rebinding, ook voor het lezen van bestanden);
    - wijzigende verzoeken (POST) moeten van Garage zelf komen (tegen CSRF)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in TOEGESTANE_HOSTS:
        return Response("Onbekende host", status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        eigen = f"{request.url.scheme}://{request.headers.get('host')}"
        if (site and site not in ("same-origin", "none")) or (origin and origin != eigen):
            return Response("Verzoek van buiten Garage geweigerd", status_code=403)
        try:
            grootte = int(request.headers.get("content-length") or 0)
        except ValueError:
            return Response("Ongeldig verzoek", status_code=400)
        if grootte > (MAX_BACKUP if request.url.path == "/backup/terugzetten-bestand" else MAX_VERZOEK):
            return render(request, "fout.html", status_code=413, fout="Dit bestand is te groot.")
    return await call_next(request)


TOEGESTANE_HOSTS = {"127.0.0.1", "localhost", "testserver"}  # 'testserver' = de testclient
MAX_VERZOEK = 200 * 1024 ** 2  # alle facturen van één beurt samen
MAX_BACKUP = backup.MAX_GROOTTE

# Inline scripts en handlers (onclick, onsubmit) zijn nodig voor de templates; de rest is dicht: geen framing
# (clickjacking), geen verbindingen of formulieren naar buiten, geen plugins.
CSP = "; ".join([
    "default-src 'self'", "script-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: " + " ".join(f"https://{h}" for h in beeld.TOEGESTANE_HOSTS),
    "frame-src https://sketchfab.com", "connect-src 'self'", "object-src 'none'", "base-uri 'none'",
    "form-action 'self'", "frame-ancestors 'none'",
])


@app.middleware("http")
async def beveiligingsheaders(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # same-origin, niet no-referrer: bij no-referrer stuurt Chromium 'Origin: null' en dat weigert de CSRF-check
    response.headers["Referrer-Policy"] = "same-origin"
    if response.headers.get("content-type", "").startswith("text/html"):  # niet op PDF's: dat hindert de PDF-viewer
        response.headers["Content-Security-Policy"] = CSP
    return response


@app.get("/gezond", include_in_schema=False)
def gezond():
    """Voor de app-starter: draait de server al?"""
    return {"app": "voertuigonderhoud"}


@app.exception_handler(StarletteHTTPException)
async def http_fout(request: Request, exc: StarletteHTTPException):
    """Foutpagina in de app-opmaak, behalve voor bestanden en fetch-verzoeken."""
    if request.url.path.startswith("/static") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return render(request, "fout.html", status_code=exc.status_code, fout=str(exc.detail))


@app.exception_handler(RequestValidationError)
async def invoer_fout(request: Request, exc: RequestValidationError):
    return render(request, "fout.html", status_code=422, fout="Er ontbreekt iets of een veld is ongeldig ingevuld.")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(HIER / "static" / "favicon.svg", media_type="image/svg+xml")


# ---------- voertuigen ----------

@app.get("/")
def index(request: Request):
    with db.connect() as conn:
        voertuigen = [dict(v) for v in conn.execute("""
            SELECT v.*, COUNT(b.id) AS aantal, MAX(b.datum) AS laatste
            FROM voertuig v LEFT JOIN beurt b ON b.voertuig_id = v.id GROUP BY v.id ORDER BY v.id""")]
        aandacht = {}
        for v in voertuigen:
            v["km"] = db.huidige_km(conn, v["id"])
            v["in_stalling"] = db.actieve_stalling(conn, v["id"]) is not None
            aandacht[v["id"]] = sum(p["kleur"] != "groen" for p in extra.voertuig_plan(conn, v, huidige_km=v["km"]))
        backup_status = backup.status(conn)
    return render(request, "index.html", voertuigen=voertuigen, aandacht=aandacht, backup=backup_status,
                  hersteld=request.query_params.get("hersteld"))


@app.get("/voertuigen/nieuw")
def voertuig_nieuw(request: Request):
    return render(request, "voertuig_form.html", v={}, soorten=VOERTUIGSOORTEN)


@app.post("/voertuigen/opzoeken")
def voertuig_opzoeken(request: Request, kenteken: str = Form("")):
    try:
        gegevens = rdw.zoek(kenteken)
    except rdw.RdwFout as e:
        return render(request, "voertuig_form.html", v={"kenteken": rdw.normaliseer(kenteken)},
                      soorten=VOERTUIGSOORTEN, fout=str(e))
    soorten = VOERTUIGSOORTEN if gegevens["voertuigsoort"] in VOERTUIGSOORTEN else [gegevens["voertuigsoort"], *VOERTUIGSOORTEN]
    return render(request, "voertuig_form.html", v=gegevens, soorten=soorten, gevonden=True)


@app.post("/voertuigen")
def voertuig_opslaan(
    kenteken: str = Form(...), voertuigsoort: str = Form(...), merk: str = Form(""), model: str = Form(""),
    datum_eerste_toelating: str = Form(""), kleur: str = Form(""), cilinderinhoud: str = Form(""),
    bijnaam: str = Form(""), rdw_json: str = Form(""),
):
    extra = _rdw_extra(rdw_json)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO voertuig (kenteken, voertuigsoort, merk, model, datum_eerste_toelating, kleur, cilinderinhoud, bijnaam,"
            " rdw_json, rdw_bijgewerkt) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rdw.normaliseer(kenteken), voertuigsoort, merk.strip(), model.strip(), datum_eerste_toelating.strip(),
             kleur.strip(), _of_400(getal, cilinderinhoud), bijnaam.strip() or None,
             json.dumps(extra) if extra else None, date.today().isoformat() if extra else None),
        )
    return terug(f"/voertuigen/{cur.lastrowid}")


RDW_VELDEN = {"apk_vervaldatum", "terugroepactie", "tellerstandoordeel", "jaar_tellerstand", "catalogusprijs",
              "datum_tenaamstelling_nl", "massa_rijklaar", "brandstof", "vermogen_kw"}


def _rdw_extra(ruw: str) -> dict:
    """Extra RDW-gegevens uit het formulier; alleen bekende velden met eenvoudige waarden."""
    try:
        data = json.loads(ruw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    schoon = {k: v for k, v in data.items() if k in RDW_VELDEN and isinstance(v, (str, int, float, bool, type(None)))}
    for k in ("apk_vervaldatum", "datum_tenaamstelling_nl"):
        try:
            if schoon.get(k):
                date.fromisoformat(schoon[k])
        except (TypeError, ValueError):
            schoon.pop(k)
    return schoon


@app.get("/voertuigen/{vid}")
def voertuig_detail(request: Request, vid: int):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        beurten = db.beurten(conn, vid)
        intervallen = db.intervallen(conn, vid)
        huidige_km = db.huidige_km(conn, vid)
        plan = extra.voertuig_plan(conn, v, beurten, huidige_km)
        specs = conn.execute("SELECT * FROM spec WHERE voertuig_id = ? ORDER BY volgorde", (vid,)).fetchall()
        aantal_docs = conn.execute("SELECT COUNT(*) FROM document WHERE voertuig_id = ?", (vid,)).fetchone()[0]
        stalling = db.actieve_stalling(conn, vid)
        laatste_notitie = conn.execute("SELECT * FROM km_stand WHERE voertuig_id = ? ORDER BY datum DESC, id DESC LIMIT 1",
                                       (vid,)).fetchone()
        sketchfab_auto = db.instelling(conn, "sketchfab_auto", "uit") == "aan"
    ongecontroleerd = [f for b in beurten for f in b["facturen"] if not f["gecontroleerd"]]
    info = _rdw_extra(v["rdw_json"])
    try:  # import: pas ruim na de eerste toelating (ergens anders) in Nederland op kenteken gezet
        importvoertuig = (date.fromisoformat(info["datum_tenaamstelling_nl"])
                          - date.fromisoformat(v["datum_eerste_toelating"])).days > 30
    except (KeyError, TypeError, ValueError):
        importvoertuig = False
    return render(request, "voertuig.html", v=v, beurten=beurten, huidige_km=huidige_km,
                  planning=plan, specs=specs, aantal_docs=aantal_docs, stalling=stalling,
                  laatste_notitie=laatste_notitie, vandaag=date.today().isoformat(),
                  km_genoteerd=request.query_params.get("km"),
                  ongecontroleerd=ongecontroleerd, rdw_info=info, eigen_schema=bool(intervallen),
                  importvoertuig=importvoertuig, sketchfab_auto=sketchfab_auto,
                  km_schatting=planning.km_schatting(v["voertuigsoort"], v["datum_eerste_toelating"])
                  if huidige_km is None else None)


@app.post("/voertuigen/{vid}/rdw")
def voertuig_rdw_vernieuwen(request: Request, vid: int):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
    try:
        gegevens = rdw.zoek(v["kenteken"])
    except rdw.RdwFout as e:
        raise HTTPException(502, str(e))
    with db.connect() as conn:
        conn.execute("UPDATE voertuig SET rdw_json = ?, rdw_bijgewerkt = ? WHERE id = ?",
                     (json.dumps(_rdw_extra(json.dumps(gegevens["rdw"]))), date.today().isoformat(), vid))
    return terug(f"/voertuigen/{vid}")


@app.get("/voertuigen/{vid}/intervallen")
def intervallen_pagina(request: Request, vid: int, opgeslagen: int = 0):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        types = [t for t in db.types_voor(conn, v["voertuigsoort"]) if t["naam"] != "Overig"]
        eigen = db.intervallen(conn, vid)
    return render(request, "intervallen.html", v=v, types=types, eigen=eigen, opgeslagen=opgeslagen)


@app.post("/voertuigen/{vid}/intervallen")
async def intervallen_opslaan(request: Request, vid: int):
    """Velden heten km_<type_id> en mnd_<type_id>. Gelijk aan de vuistregel = geen eigen waarde opslaan."""
    form = await request.form()
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        for t in db.types_voor(conn, v["voertuigsoort"]):
            if f"km_{t['id']}" not in form and f"mnd_{t['id']}" not in form:
                continue  # niet meegestuurd: laten zoals het is
            km = _of_400(getal, form.get(f"km_{t['id']}"))
            mnd = _of_400(getal, form.get(f"mnd_{t['id']}"))
            conn.execute("DELETE FROM voertuig_interval WHERE voertuig_id = ? AND type_id = ?", (vid, t["id"]))
            if (km, mnd) != (t["interval_km"], t["interval_maanden"]):
                conn.execute("INSERT INTO voertuig_interval (voertuig_id, type_id, interval_km, interval_maanden)"
                             " VALUES (?,?,?,?)", (vid, t["id"], km, mnd))
        conn.execute("UPDATE voertuig SET schema_bron = ? WHERE id = ?", ((form.get("schema_bron") or "").strip() or None, vid))
    return terug(f"/voertuigen/{vid}/intervallen?opgeslagen=1")


@app.post("/voertuigen/{vid}/factuur-uitlezen")
async def factuur_uitlezen(vid: int, facturen: list[UploadFile] = File([])):
    """Stateless: leest de facturen en geeft voorstellen voor het formulier terug. Er wordt niets opgeslagen."""
    with db.connect() as conn:
        types = db.types_voor(conn, haal(conn, "voertuig", vid)["voertuigsoort"])
    try:
        uploads = await lees_uploads(facturen)
    except ValueError as e:
        return {"leesbaar": False, "fout": str(e)}
    resultaat = factuur_lezen.combineer([factuur_lezen.lees(inhoud, TOEGESTAAN[ext][0], types)
                                         for _, ext, inhoud in uploads])
    namen = {t["id"]: t["naam"] for t in types}
    resultaat["type_namen"] = [namen[t] for t in resultaat.get("types", [])]
    return resultaat


@app.post("/voertuigen/{vid}/verwijderen")
def voertuig_verwijderen(vid: int):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        paden = factuur_paden(conn.execute(
            "SELECT f.* FROM factuur f JOIN beurt b ON b.id = f.beurt_id WHERE b.voertuig_id = ?", (vid,)))
        paden += [d["pad"] for d in conn.execute("SELECT pad FROM document WHERE voertuig_id = ?", (vid,))]
        conn.execute("DELETE FROM voertuig WHERE id = ?", (vid,))
    verwijder_bestanden(paden + [v["beeld_pad"]])
    return terug("/")


@app.get("/voertuigen/{vid}/export.pdf")
def voertuig_export(vid: int, kosten: int = 1):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        beurten = db.beurten(conn, vid)
        km = db.kmverloop(conn, vid)
        specs = conn.execute("SELECT * FROM spec WHERE voertuig_id = ? ORDER BY volgorde", (vid,)).fetchall()
    pdf = export_pdf.maak(v, beurten, met_kosten=bool(kosten), km=km, specs=specs)
    naam = f"onderhoudshistorie_{v['kenteken']}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{naam}"'})


# ---------- voertuigbeeld (3D / foto) ----------

@app.get("/voertuigen/{vid}/beeld")
def beeld_kiezen(request: Request, vid: int, q: str = ""):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
    if q.strip():
        modellen, fotos, fout = beeld.zoek_beide(lambda: beeld.zoek_3d(q.strip()), lambda: beeld.zoek_fotos(q.strip()))
    else:
        modellen, fotos, fout = beeld.zoek_alles(v["merk"], v["model"])
    zoekterm = q.strip() or " ".join(filter(None, [v["merk"], v["model"]]))
    return render(request, "beeld.html", v=v, modellen=modellen, fotos=fotos, fout=fout, zoekterm=zoekterm)


def _zet_beeld(vid: int, soort: str | None, naam: str | None, ref=None, titel=None, bron=None):
    with db.connect() as conn:
        oud = haal(conn, "voertuig", vid)["beeld_pad"]
        conn.execute("UPDATE voertuig SET beeld_type=?, beeld_ref=?, beeld_titel=?, beeld_bron=?, beeld_pad=? WHERE id=?",
                     (soort, ref, titel, bron, f"beelden/{naam}" if naam else None, vid))
    verwijder_bestanden([oud])


@app.post("/voertuigen/{vid}/beeld")
def beeld_opslaan(request: Request, vid: int, soort: str = Form(...), thumb: str = Form(...), uid: str = Form(""),
                  titel: str = Form(""), bron: str = Form("")):
    if soort not in ("3d", "foto") or (soort == "3d" and not re.fullmatch(r"[0-9a-f]{32}", uid)):
        raise HTTPException(400, "Ongeldige keuze")
    with db.connect() as conn:
        haal(conn, "voertuig", vid)  # eerst controleren, dan pas downloaden (geen weesbestanden)
    try:
        naam = beeld.download(thumb, db.DATA_DIR / "beelden")
    except beeld.BeeldFout as e:
        raise HTTPException(502, str(e))
    if soort == "3d":
        bron = f"https://sketchfab.com/3d-models/{uid}"
    elif not bron.startswith("https://commons.wikimedia.org/"):
        bron = None
    _zet_beeld(vid, soort, naam, uid or None, titel.strip() or None, bron)
    return terug(f"/voertuigen/{vid}")


@app.post("/voertuigen/{vid}/beeld/upload")
async def beeld_upload(vid: int, foto: UploadFile = File(...)):
    with db.connect() as conn:
        haal(conn, "voertuig", vid)
    try:
        naam = beeld.eigen_foto(await foto.read(), db.DATA_DIR / "beelden")
    except beeld.BeeldFout as e:
        raise HTTPException(400, str(e))
    _zet_beeld(vid, "eigen", naam, titel="Eigen foto")
    return terug(f"/voertuigen/{vid}")


@app.post("/voertuigen/{vid}/beeld/verwijderen")
def beeld_verwijderen(vid: int):
    _zet_beeld(vid, None, None)
    return terug(f"/voertuigen/{vid}")


@app.get("/voertuigen/{vid}/beeld.jpg")
def beeld_bestand(vid: int):
    with db.connect() as conn:
        pad = haal(conn, "voertuig", vid)["beeld_pad"]
    try:
        bestand = db.bestand(pad)
    except ValueError:
        raise HTTPException(404, "Geen beeld")
    if not bestand.is_file():
        raise HTTPException(404, "Geen beeld")
    # de pagina's vragen dit op met ?v=<bestandsnaam> (web.beeld_url); die url verandert bij elk nieuw beeld
    return FileResponse(bestand, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------- onderhoudsbeurten ----------

def beurt_formulier(request: Request, conn, v, beurt=None, gekozen=(), fout=None):
    types = db.types_voor(conn, v["voertuigsoort"])
    # al gekoppelde types altijd tonen, ook als het type inmiddels niet meer bij deze voertuigsoort hoort
    getoond = {t["id"] for t in types}
    extra = [t for t in conn.execute("SELECT * FROM onderhoudstype ORDER BY naam")
             if t["id"] in gekozen and t["id"] not in getoond]
    return render(request, "beurt_form.html", status_code=400 if fout else 200, v=v, beurt=beurt or {},
                  types=types + extra, gekozen=set(gekozen), fout=fout,
                  facturen=conn.execute("SELECT * FROM factuur WHERE beurt_id = ?", (beurt["id"],)).fetchall()
                  if beurt and beurt.get("id") else [],
                  vandaag=date.today().isoformat(), huidige_km=db.huidige_km(conn, v["id"]))


@app.get("/voertuigen/{vid}/beurt/nieuw")
def beurt_nieuw(request: Request, vid: int):
    with db.connect() as conn:
        return beurt_formulier(request, conn, haal(conn, "voertuig", vid))


@app.get("/beurt/{bid}/bewerken")
def beurt_bewerken(request: Request, bid: int):
    with db.connect() as conn:
        b = haal(conn, "beurt", bid)
        gekozen = [r["type_id"] for r in conn.execute("SELECT type_id FROM beurt_type WHERE beurt_id = ?", (bid,))]
        return beurt_formulier(request, conn, haal(conn, "voertuig", b["voertuig_id"]), dict(b), gekozen)


async def beurt_opslaan(request: Request, vid: int, bid: int | None, form_data: dict, bestanden: list[UploadFile]):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        bestaande_types = {r["id"] for r in conn.execute("SELECT id FROM onderhoudstype")}
        type_ids = [int(t) for t in form_data["types"] if t.isdigit() and int(t) in bestaande_types]
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", form_data["datum"]):
                raise ValueError("Vul een geldige datum in.")
            date.fromisoformat(form_data["datum"])
            if not type_ids:
                raise ValueError("Kies minimaal één onderhoudstype.")
            km, kosten = getal(form_data["kilometerstand"]), bedrag(form_data["kosten"])
            if km is not None and (fout := extra.km_controle(conn, vid, form_data["datum"], km, behalve_beurt=bid)):
                raise ValueError(fout)
            uploads = await lees_uploads(bestanden)
        except ValueError as e:
            return beurt_formulier(request, conn, v, {**form_data, "id": bid}, type_ids, str(e))

        velden = (form_data["datum"], km, form_data["uitgevoerd_door"].strip() or None, kosten,
                  form_data["opmerking"].strip() or None)
        if bid is None:
            bid = conn.execute("INSERT INTO beurt (datum, kilometerstand, uitgevoerd_door, kosten, opmerking, voertuig_id)"
                               " VALUES (?,?,?,?,?,?)", (*velden, vid)).lastrowid
        else:
            conn.execute("UPDATE beurt SET datum=?, kilometerstand=?, uitgevoerd_door=?, kosten=?, opmerking=? WHERE id=?",
                         (*velden, bid))
            conn.execute("DELETE FROM beurt_type WHERE beurt_id = ?", (bid,))
        conn.executemany("INSERT INTO beurt_type (beurt_id, type_id) VALUES (?,?)", [(bid, t) for t in type_ids])

        map_ = db.DATA_DIR / "facturen" / str(vid)
        map_.mkdir(parents=True, exist_ok=True)
        geschreven = []
        try:
            for naam, ext, inhoud in uploads:
                pad = map_ / f"{uuid.uuid4().hex}{ext}"
                pad.write_bytes(inhoud)
                geschreven.append(pad.relative_to(db.DATA_DIR).as_posix())
                conn.execute("INSERT INTO factuur (beurt_id, pad, originele_naam, mime_type) VALUES (?,?,?,?)",
                             (bid, geschreven[-1], naam, TOEGESTAAN[ext][0]))
        except Exception:
            verwijder_bestanden(geschreven)  # database wordt teruggedraaid, dus bestanden ook weg
            raise
    # nieuwe facturen: direct door naar het lakscherm voor persoonsgegevens
    return terug(f"/beurt/{bid}/lakken" if uploads else f"/voertuigen/{vid}")


def _form(datum, kilometerstand, uitgevoerd_door, kosten, opmerking, types):
    return dict(datum=datum, kilometerstand=kilometerstand, uitgevoerd_door=uitgevoerd_door, kosten=kosten,
                opmerking=opmerking, types=types)


@app.post("/voertuigen/{vid}/beurt/nieuw")
async def beurt_nieuw_opslaan(
    request: Request, vid: int, datum: str = Form(...), kilometerstand: str = Form(""),
    uitgevoerd_door: str = Form(""), kosten: str = Form(""), opmerking: str = Form(""),
    types: list[str] = Form([]), facturen: list[UploadFile] = File([]),
):
    return await beurt_opslaan(request, vid, None, _form(datum, kilometerstand, uitgevoerd_door, kosten, opmerking, types), facturen)


@app.post("/beurt/{bid}/bewerken")
async def beurt_bewerken_opslaan(
    request: Request, bid: int, datum: str = Form(...), kilometerstand: str = Form(""),
    uitgevoerd_door: str = Form(""), kosten: str = Form(""), opmerking: str = Form(""),
    types: list[str] = Form([]), facturen: list[UploadFile] = File([]),
):
    with db.connect() as conn:
        vid = haal(conn, "beurt", bid)["voertuig_id"]
    return await beurt_opslaan(request, vid, bid, _form(datum, kilometerstand, uitgevoerd_door, kosten, opmerking, types), facturen)


@app.post("/beurt/{bid}/verwijderen")
def beurt_verwijderen(bid: int):
    with db.connect() as conn:
        vid = haal(conn, "beurt", bid)["voertuig_id"]
        paden = factuur_paden(conn.execute("SELECT * FROM factuur WHERE beurt_id = ?", (bid,)))
        conn.execute("DELETE FROM beurt WHERE id = ?", (bid,))
    verwijder_bestanden(paden)
    return terug(f"/voertuigen/{vid}")


# ---------- facturen ----------

def _bestand(pad: str, naam: str) -> FileResponse:
    try:
        bestand = db.bestand(pad)
    except ValueError:
        raise HTTPException(404, "Factuurbestand niet gevonden")
    if not bestand.is_file():
        raise HTTPException(404, "Factuurbestand ontbreekt op schijf")
    return FileResponse(bestand, media_type=mime_voor(bestand), filename=naam, content_disposition_type="inline")


@app.get("/facturen/{fid}")
def factuur_openen(fid: int):
    """De versie die gedeeld wordt: gelakt als dat gedaan is."""
    with db.connect() as conn:
        f = haal(conn, "factuur", fid)
    return _bestand(f["pad"], f["originele_naam"])


@app.get("/facturen/{fid}/origineel")
def factuur_origineel(fid: int):
    with db.connect() as conn:
        f = haal(conn, "factuur", fid)
    return _bestand(f["origineel_pad"] or f["pad"], f["originele_naam"])


@app.post("/facturen/{fid}/verwijderen")
def factuur_verwijderen(fid: int):
    with db.connect() as conn:
        f = haal(conn, "factuur", fid)
        conn.execute("DELETE FROM factuur WHERE id = ?", (fid,))
    verwijder_bestanden(factuur_paden([f]))
    return terug(f"/beurt/{f['beurt_id']}/bewerken")


# ---------- persoonsgegevens weglakken ----------

@app.get("/beurt/{bid}/lakken")
def beurt_lakken(bid: int):
    """Loopt de nog niet gecontroleerde facturen van een beurt één voor één af."""
    with db.connect() as conn:
        b = haal(conn, "beurt", bid)
        f = conn.execute("SELECT id FROM factuur WHERE beurt_id = ? AND gecontroleerd = 0 ORDER BY id", (bid,)).fetchone()
    return terug(f"/facturen/{f['id']}/lakken" if f else f"/voertuigen/{b['voertuig_id']}")


@app.get("/facturen/{fid}/lakken")
def factuur_lakken(request: Request, fid: int):
    with db.connect() as conn:
        f = haal(conn, "factuur", fid)
        b = haal(conn, "beurt", f["beurt_id"])
        v = haal(conn, "voertuig", b["voertuig_id"])
        termen = db.prive_termen(conn)
        nog = conn.execute("SELECT COUNT(*) FROM factuur WHERE beurt_id = ? AND gecontroleerd = 0 AND id != ?",
                           (b["id"], fid)).fetchone()[0]
    origineel = _origineel(f)
    # eerder toegepaste balken staan weer aan, zodat opnieuw lakken nooit iets terug zichtbaar maakt
    eerder = [{**b, "reden": "eerder gelakt", "actief": True} for b in json.loads(f["boxen"] or "[]")]
    try:
        suggesties = lakken.suggesties(origineel, f["mime_type"], termen)
    except lakken.LakFout as e:
        raise HTTPException(400, str(e))
    return render(request, "lakken.html", f=f, v=v, nog=nog, heeft_termen=bool(termen),
                  paginas=lakken.aantal_paginas(origineel, f["mime_type"]), suggesties=eerder + suggesties)


def _origineel(f) -> Path:
    try:
        pad = db.bestand(f["origineel_pad"] or f["pad"])
    except ValueError:
        raise HTTPException(404, "Factuurbestand niet gevonden")
    if not pad.is_file():
        raise HTTPException(404, "Factuurbestand ontbreekt op schijf")
    return pad


@app.get("/facturen/{fid}/pagina/{nr}.png")
def factuur_pagina(fid: int, nr: int):
    with db.connect() as conn:
        f = haal(conn, "factuur", fid)
    origineel = _origineel(f)
    if nr < 0 or nr >= lakken.aantal_paginas(origineel, f["mime_type"]):
        raise HTTPException(404, "Pagina bestaat niet")
    try:
        png = lakken.pagina_png(origineel, f["mime_type"], nr)
    except lakken.LakFout as e:
        raise HTTPException(400, str(e))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


def _lees_boxen(ruw: str, paginas: int) -> list[dict]:
    try:
        boxen = json.loads(ruw or "[]")
        schoon = []
        for b in boxen:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(b[k]))) for k in ("x0", "y0", "x1", "y1"))
            pagina = int(b["pagina"])
            if 0 <= pagina < paginas and x1 > x0 and y1 > y0:
                schoon.append({"pagina": pagina, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        return schoon
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, "Ongeldige balken")


@app.post("/facturen/{fid}/lakken")
def factuur_lakken_opslaan(fid: int, boxen: str = Form("[]")):
    with db.connect() as conn:
        f = haal(conn, "factuur", fid)
    origineel = _origineel(f)
    origineel_rel = origineel.relative_to(db.DATA_DIR).as_posix()
    schoon = _lees_boxen(boxen, lakken.aantal_paginas(origineel, f["mime_type"]))
    doel = origineel.with_name(f"{uuid.uuid4().hex}-gelakt{origineel.suffix}")
    try:
        lakken.toepassen(origineel, f["mime_type"], schoon, doel)
    except Exception as e:
        doel.unlink(missing_ok=True)  # geen half geschreven bestand laten staan
        if isinstance(e, lakken.LakFout):
            raise HTTPException(400, str(e))
        raise
    oud_gelakt = f["pad"] if f["origineel_pad"] else None
    with db.connect() as conn:
        conn.execute("UPDATE factuur SET pad = ?, origineel_pad = ?, gecontroleerd = 1, boxen = ? WHERE id = ?",
                     (doel.relative_to(db.DATA_DIR).as_posix(), origineel_rel, json.dumps(schoon), fid))
    verwijder_bestanden([oud_gelakt])
    return terug(f"/beurt/{f['beurt_id']}/lakken")


# ---------- instellingen ----------

@app.get("/instellingen")
def instellingen(request: Request, opgeslagen: int = 0):
    with db.connect() as conn:
        types = conn.execute("SELECT * FROM onderhoudstype ORDER BY naam = 'Overig', naam").fetchall()
        termen = db.instelling(conn, "prive_termen")
        overig = {**extra.backup_context(conn), "herinneringen": db.instelling(conn, "herinneringen", "aan") == "aan",
                  "sketchfab_auto": db.instelling(conn, "sketchfab_auto", "uit") == "aan"}
    groepen = [("*", "Alle voertuigen"), (db.MOTOR, "Motoren en brommers"), (db.AUTO, "Auto's en bedrijfsauto's"),
               *[(s, s) for s in VOERTUIGSOORTEN]]
    return render(request, "instellingen.html", types=types, groepen=groepen, termen=termen,
                  opgeslagen=opgeslagen, themas=extra.THEMAS, melding=request.query_params, **overig)


@app.get("/onderhoudstypes")
def types_lijst():
    return terug("/instellingen#types")


@app.post("/instellingen/prive")
def prive_opslaan(termen: str = Form("")):
    with db.connect() as conn:
        db.zet_instelling(conn, "prive_termen", "\n".join(t.strip() for t in termen.splitlines() if t.strip()))
    return terug("/instellingen?opgeslagen=1#prive")


@app.post("/onderhoudstypes")
def type_opslaan(
    id: str = Form(""), naam: str = Form(...), voertuigsoorten: list[str] = Form([]),
    interval_km: str = Form(""), interval_maanden: str = Form(""),
):
    voertuigsoorten = [s for v in voertuigsoorten for s in v.split(",") if s]
    soorten = "*" if not voertuigsoorten or "*" in voertuigsoorten else ",".join(dict.fromkeys(voertuigsoorten))
    waarden = (naam.strip(), soorten, _of_400(getal, interval_km), _of_400(getal, interval_maanden))
    with db.connect() as conn:
        if id:
            conn.execute("UPDATE onderhoudstype SET naam=?, voertuigsoorten=?, interval_km=?, interval_maanden=? WHERE id=?",
                         (*waarden, int(id)))
        else:
            conn.execute("INSERT INTO onderhoudstype (naam, voertuigsoorten, interval_km, interval_maanden) VALUES (?,?,?,?)",
                         waarden)
    return terug("/instellingen#types")
