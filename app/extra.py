"""Uitbreidingen: back-up, km-registraties, documenten, specificaties, stalling, kostenoverzicht en weergave."""
import json
import shutil
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import backup, db, planning
from .web import TOEGESTAAN, _of_400, getal, haal, lees_uploads, render, terug, verwijder_bestanden

router = APIRouter()

THEMAS = {"licht": "Licht", "donker": "Donker", "systeem": "Zoals Windows"}
DOCUMENTSOORTEN = ["Kentekenbewijs / kentekencard", "Verzekeringspolis", "Aankoopfactuur", "Garantiebewijs",
                   "Instructieboekje / handleiding", "Keuringsrapport", "Overig"]
SPECS_MOTOR = ["Motorolie", "Olie-inhoud", "Oliefilter", "Luchtfilter", "Bougies", "Bandenmaat voor", "Bandenmaat achter",
               "Bandenspanning voor", "Bandenspanning achter", "Ketting", "Tandwielen (voor/achter)", "Remblokken voor",
               "Remblokken achter", "Accu", "Remvloeistof", "Koelvloeistof"]
SPECS_AUTO = ["Motorolie", "Olie-inhoud", "Oliefilter", "Luchtfilter", "Interieurfilter", "Bougies", "Bandenmaat",
              "Bandenspanning voor", "Bandenspanning achter", "Ruitenwissers", "Accu", "Remvloeistof", "Koelvloeistof"]
SPEC_VOORBEELD = {"Motorolie": "bijv. 10W-40 (Motul 7100)", "Olie-inhoud": "bijv. 3,0 L met filter",
                  "Oliefilter": "bijv. HF303", "Bandenmaat voor": "bijv. 120/70 ZR17", "Bandenmaat achter": "bijv. 180/55 ZR17",
                  "Bandenspanning voor": "bijv. 2,5 bar", "Bandenspanning achter": "bijv. 2,9 bar", "Ketting": "bijv. DID 520VX3, 112 schakels",
                  "Accu": "bijv. YTX12-BS", "Bougies": "bijv. NGK CR9EK", "Bandenmaat": "bijv. 205/55 R16"}
STALLEN = ["Tank volgooien (eventueel met brandstofstabilisator)", "Wassen en goed laten drogen",
           "Ketting reinigen en smeren", "Bandenspanning iets verhogen (ca. 0,5 bar)", "Accu loskoppelen of aan een druppellader",
           "Droog en vorstvrij wegzetten, eventueel op een paddockstand", "Verzekering: eventueel schorsen of stallingskorting vragen"]
OPHALEN = ["Accu laden en aansluiten", "Bandenspanning terug op normale waarde", "Olieniveau en lekkage controleren",
           "Remmen en remvloeistof controleren", "Ketting spannen en smeren", "Verlichting en richtingaanwijzers testen",
           "Rustig de eerste kilometers rijden (banden en remmen moeten op temperatuur komen)"]


def specs_voor(soort: str) -> list[str]:
    return SPECS_MOTOR if soort in ("Motorfiets", "Bromfiets") else SPECS_AUTO if soort in ("Personenauto", "Bedrijfsauto") else []


def _datum(waarde: str) -> str:
    try:
        return date.fromisoformat(waarde).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(400, "Vul een geldige datum in.")


# ---------- weergave (licht / donker / systeem) ----------

@router.post("/weergave")
def weergave(thema: str = Form(...)):
    if thema not in THEMAS:
        raise HTTPException(400, "Onbekende weergave")
    with db.connect() as conn:
        db.zet_instelling(conn, "thema", thema)
    return {"thema": thema}


# ---------- km-stand noteren ----------

def km_controle(conn, vid: int, datum: str, km: int, behalve_beurt: int | None = None) -> str | None:
    """Een km-stand moet passen tussen eerdere en latere registraties (tellers lopen niet terug)."""
    for r in db.kmverloop(conn, vid):
        if behalve_beurt is not None and r["bron"] == "beurt" and r["id"] == behalve_beurt:
            continue
        bron = {"beurt": "bij een onderhoudsbeurt", "notitie": "als losse km-stand", "stalling in": "bij het stallen",
                "stalling uit": "bij het ophalen uit de stalling"}.get(r["bron"], "")
        datum_nl = date.fromisoformat(r["datum"]).strftime("%d-%m-%Y")
        if r["datum"] <= datum and r["kilometerstand"] > km:
            return (f"{km:,} km is lager dan de {r['kilometerstand']:,} km die op {datum_nl} al {bron} is genoteerd. "
                    "Klopt die eerdere stand niet? Pas hem dan eerst aan (Kosten & km of Stalling).").replace(",", ".")
        if r["datum"] > datum and r["kilometerstand"] < km:
            return (f"{km:,} km is hoger dan de {r['kilometerstand']:,} km die later, op {datum_nl}, {bron} is genoteerd."
                    ).replace(",", ".")
    return None


@router.post("/voertuigen/{vid}/km")
def km_noteren(vid: int, kilometerstand: str = Form(...), datum: str = Form(""), opmerking: str = Form("")):
    datum = _datum(datum or date.today().isoformat())
    km = _of_400(getal, kilometerstand)
    if km is None:
        raise HTTPException(400, "Vul een kilometerstand in.")
    if datum > date.today().isoformat():
        raise HTTPException(400, "De datum ligt in de toekomst.")
    with db.connect() as conn:
        haal(conn, "voertuig", vid)
        if fout := km_controle(conn, vid, datum, km):
            raise HTTPException(400, fout)
        conn.execute("INSERT INTO km_stand (voertuig_id, datum, kilometerstand, opmerking) VALUES (?,?,?,?)",
                     (vid, datum, km, opmerking.strip() or None))
    return terug(f"/voertuigen/{vid}?km=1")


@router.post("/km/{kid}/verwijderen")
def km_verwijderen(kid: int):
    with db.connect() as conn:
        vid = haal(conn, "km_stand", kid)["voertuig_id"]
        conn.execute("DELETE FROM km_stand WHERE id = ?", (kid,))
    return terug(f"/voertuigen/{vid}/kosten#km")


# ---------- kosten- en km-overzicht ----------

def _grafiek_kosten(per_jaar: dict[int, float]) -> dict:
    jaren = sorted(per_jaar)
    hoogste = max(per_jaar.values(), default=0) or 1
    return {"staven": [{"jaar": j, "bedrag": per_jaar[j], "hoogte": per_jaar[j] / hoogste} for j in jaren],
            "max": hoogste}


def _grafiek_km(verloop: list[dict]) -> dict | None:
    punten = [(date.fromisoformat(r["datum"]), r["kilometerstand"]) for r in verloop]
    if len(punten) < 2:
        return None
    t0, t1 = punten[0][0].toordinal(), max(punten[-1][0].toordinal(), punten[0][0].toordinal() + 1)
    k0, k1 = min(p[1] for p in punten), max(p[1] for p in punten)
    k1 = k1 if k1 > k0 else k0 + 1
    return {"punten": [{"x": (d.toordinal() - t0) / (t1 - t0), "y": (k - k0) / (k1 - k0), "datum": d, "km": k}
                       for d, k in punten], "min": k0, "max": k1, "van": punten[0][0], "tot": punten[-1][0]}


@router.get("/voertuigen/{vid}/kosten")
def kosten(request: Request, vid: int):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        beurten = db.beurten(conn, vid)
        verloop = db.kmverloop(conn, vid)
        notities = conn.execute("SELECT * FROM km_stand WHERE voertuig_id = ? ORDER BY datum DESC", (vid,)).fetchall()
    met_kosten = [b for b in beurten if b["kosten"]]
    per_jaar: dict[int, float] = {}
    per_soort: dict[str, float] = {}
    for b in met_kosten:
        per_jaar[int(b["datum"][:4])] = per_jaar.get(int(b["datum"][:4]), 0) + b["kosten"]
        # een beurt met meerdere werkzaamheden is niet eerlijk te splitsen: die telt als 'gecombineerd'
        soort = b["types"][0]["naam"] if len(b["types"]) == 1 else "Beurten met meerdere klussen"
        per_soort[soort] = per_soort.get(soort, 0) + b["kosten"]
    totaal = sum(b["kosten"] for b in met_kosten)
    gereden = (verloop[-1]["kilometerstand"] - verloop[0]["kilometerstand"]) if len(verloop) >= 2 else 0
    hoogste_soort = max(per_soort.values(), default=1)
    return render(request, "kosten.html", v=v, totaal=totaal, aantal=len(met_kosten),
                  per_km=totaal / gereden if gereden > 0 and totaal else None, gereden=gereden,
                  per_jaar=_grafiek_kosten(per_jaar),
                  per_soort=sorted(((s, b, b / hoogste_soort) for s, b in per_soort.items()), key=lambda x: -x[1]),
                  km=_grafiek_km(verloop), verloop=list(reversed(verloop)), notities=notities)


# ---------- documenten ----------

@router.get("/voertuigen/{vid}/documenten")
def documenten(request: Request, vid: int):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        docs = conn.execute("SELECT * FROM document WHERE voertuig_id = ? ORDER BY soort, toegevoegd DESC", (vid,)).fetchall()
    return render(request, "documenten.html", v=v, docs=docs, soorten=DOCUMENTSOORTEN, vandaag=date.today().isoformat())


@router.post("/voertuigen/{vid}/documenten")
async def document_toevoegen(vid: int, soort: str = Form(...), titel: str = Form(""), vervaldatum: str = Form(""),
                             bestand: UploadFile = File(...)):
    if soort not in DOCUMENTSOORTEN:
        raise HTTPException(400, "Kies een soort document.")
    vervaldatum = _datum(vervaldatum) if vervaldatum else None
    try:
        uploads = await lees_uploads([bestand])
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not uploads:
        raise HTTPException(400, "Kies een bestand.")
    naam, ext, inhoud = uploads[0]
    with db.connect() as conn:
        haal(conn, "voertuig", vid)
        map_ = db.DATA_DIR / "documenten" / str(vid)
        map_.mkdir(parents=True, exist_ok=True)
        pad = map_ / f"{uuid.uuid4().hex}{ext}"
        pad.write_bytes(inhoud)
        try:
            conn.execute("INSERT INTO document (voertuig_id, soort, titel, pad, originele_naam, mime_type, vervaldatum, toegevoegd)"
                         " VALUES (?,?,?,?,?,?,?,?)", (vid, soort, titel.strip() or None, pad.relative_to(db.DATA_DIR).as_posix(),
                                                       naam, TOEGESTAAN[ext][0], vervaldatum, date.today().isoformat()))
        except Exception:
            pad.unlink(missing_ok=True)
            raise
    return terug(f"/voertuigen/{vid}/documenten")


@router.get("/documenten/{did}")
def document_openen(did: int):
    with db.connect() as conn:
        d = haal(conn, "document", did)
    try:
        pad = db.bestand(d["pad"])
    except ValueError:
        raise HTTPException(404, "Bestand niet gevonden")
    if not pad.is_file():
        raise HTTPException(404, "Bestand ontbreekt op schijf")
    return FileResponse(pad, media_type=d["mime_type"], filename=d["originele_naam"], content_disposition_type="inline")


@router.post("/documenten/{did}/verwijderen")
def document_verwijderen(did: int):
    with db.connect() as conn:
        d = haal(conn, "document", did)
        conn.execute("DELETE FROM document WHERE id = ?", (did,))
    verwijder_bestanden([d["pad"]])
    return terug(f"/voertuigen/{d['voertuig_id']}/documenten")


@router.post("/documenten/{did}/vervaldatum")
def document_vervaldatum(did: int, vervaldatum: str = Form("")):
    with db.connect() as conn:
        d = haal(conn, "document", did)
        conn.execute("UPDATE document SET vervaldatum = ? WHERE id = ?", (_datum(vervaldatum) if vervaldatum else None, did))
    return terug(f"/voertuigen/{d['voertuig_id']}/documenten")


# ---------- specificaties ----------

@router.get("/voertuigen/{vid}/specificaties")
def specificaties(request: Request, vid: int, opgeslagen: int = 0):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        bestaand = {r["sleutel"]: r["waarde"] for r in conn.execute(
            "SELECT * FROM spec WHERE voertuig_id = ? ORDER BY volgorde", (vid,))}
    standaard = specs_voor(v["voertuigsoort"])
    eigen = [k for k in bestaand if k not in standaard]
    return render(request, "specificaties.html", v=v, velden=standaard + eigen, waarden=bestaand,
                  voorbeeld=SPEC_VOORBEELD, opgeslagen=opgeslagen)


@router.post("/voertuigen/{vid}/specificaties")
async def specificaties_opslaan(request: Request, vid: int):
    form = await request.form()
    sleutels, waarden = form.getlist("sleutel"), form.getlist("waarde")
    if len(sleutels) != len(waarden):
        raise HTTPException(400, "Ongeldig formulier")
    rijen = {}
    for sleutel, waarde in zip(sleutels, waarden):
        sleutel, waarde = str(sleutel).strip()[:60], str(waarde).strip()[:200]
        if sleutel and waarde and sleutel not in rijen:
            rijen[sleutel] = waarde
    with db.connect() as conn:
        haal(conn, "voertuig", vid)
        conn.execute("DELETE FROM spec WHERE voertuig_id = ?", (vid,))
        conn.executemany("INSERT INTO spec (voertuig_id, sleutel, waarde, volgorde) VALUES (?,?,?,?)",
                         [(vid, k, w, i) for i, (k, w) in enumerate(rijen.items())])
    return terug(f"/voertuigen/{vid}/specificaties?opgeslagen=1")


# ---------- stalling ----------

@router.get("/voertuigen/{vid}/stalling")
def stalling(request: Request, vid: int):
    with db.connect() as conn:
        v = haal(conn, "voertuig", vid)
        actief = db.actieve_stalling(conn, vid)
        periodes = conn.execute("SELECT * FROM stalling WHERE voertuig_id = ? ORDER BY van DESC", (vid,)).fetchall()
        huidige = db.huidige_km(conn, vid)
        plan = voertuig_plan(conn, v) if actief else []
    return render(request, "stalling.html", v=v, actief=actief, periodes=periodes, huidige_km=huidige,
                  checklist=OPHALEN if actief else STALLEN, achterstallig=[p for p in plan if p["kleur"] != "groen"],
                  vandaag=date.today().isoformat())


def _stalling_km(conn, vid, datum, kilometerstand):
    km = _of_400(getal, kilometerstand)
    if km is not None and (fout := km_controle(conn, vid, datum, km)):
        raise HTTPException(400, fout)
    return km


def _niet_in_toekomst(datum: str) -> str:
    if datum > date.today().isoformat():
        raise HTTPException(400, "De datum ligt in de toekomst.")
    return datum


@router.post("/voertuigen/{vid}/stalling/in")
def stalling_in(vid: int, datum: str = Form(""), kilometerstand: str = Form("")):
    datum = _niet_in_toekomst(_datum(datum or date.today().isoformat()))
    with db.connect() as conn:
        haal(conn, "voertuig", vid)
        if db.actieve_stalling(conn, vid):
            raise HTTPException(400, "Dit voertuig staat al in de stalling.")
        vorige = conn.execute("SELECT MAX(tot) FROM stalling WHERE voertuig_id = ?", (vid,)).fetchone()[0]
        if vorige and datum < vorige:
            raise HTTPException(400, "Deze datum valt in een eerdere stallingsperiode.")
        conn.execute("INSERT INTO stalling (voertuig_id, van, km_van) VALUES (?,?,?)",
                     (vid, datum, _stalling_km(conn, vid, datum, kilometerstand)))
    return terug(f"/voertuigen/{vid}")


@router.post("/voertuigen/{vid}/stalling/uit")
def stalling_uit(vid: int, datum: str = Form(""), kilometerstand: str = Form("")):
    datum = _niet_in_toekomst(_datum(datum or date.today().isoformat()))
    with db.connect() as conn:
        haal(conn, "voertuig", vid)
        actief = db.actieve_stalling(conn, vid)
        if not actief:
            raise HTTPException(400, "Dit voertuig staat niet in de stalling.")
        if datum < actief["van"]:
            raise HTTPException(400, "De ophaaldatum ligt vóór de stallingsdatum.")
        conn.execute("UPDATE stalling SET tot = ?, km_tot = ? WHERE id = ?",
                     (datum, _stalling_km(conn, vid, datum, kilometerstand), actief["id"]))
    return terug(f"/voertuigen/{vid}")


@router.post("/stalling/{sid}/verwijderen")
def stalling_verwijderen(sid: int):
    """Een stallingsperiode wissen (bijv. na een tikfout in de km-stand)."""
    with db.connect() as conn:
        vid = haal(conn, "stalling", sid)["voertuig_id"]
        conn.execute("DELETE FROM stalling WHERE id = ?", (sid,))
    return terug(f"/voertuigen/{vid}/stalling")


# ---------- planning inclusief vervaldata (APK, verzekering, ...) ----------

def vervaldata(conn, v) -> list[tuple[str, str, str]]:
    """(naam, datum, bron) van dingen met een vervaldatum: APK uit de RDW en documenten."""
    items = []
    try:
        apk = json.loads(v["rdw_json"] or "{}").get("apk_vervaldatum")
    except ValueError:
        apk = None
    if apk:
        items.append(("APK verloopt", apk, "rdw"))
    for d in conn.execute("SELECT soort, titel, vervaldatum FROM document WHERE voertuig_id = ? AND vervaldatum IS NOT NULL",
                          (v["id"],)):
        items.append((f"{d['titel'] or d['soort']} verloopt", d["vervaldatum"], "document"))

    def geldig(datum) -> bool:  # een kapotte datum mag nooit een hele pagina laten crashen
        try:
            date.fromisoformat(datum)
            return True
        except (TypeError, ValueError):
            return False
    return [i for i in items if geldig(i[1])]


def voertuig_plan(conn, v, beurten=None, huidige_km=None) -> list[dict]:
    beurten = beurten if beurten is not None else db.beurten(conn, v["id"])
    huidige_km = huidige_km if huidige_km is not None else db.huidige_km(conn, v["id"])
    plan = planning.overzicht(beurten, huidige_km, intervallen=db.intervallen(conn, v["id"]))
    plan += [planning.vervalstatus(naam, datum, bron) for naam, datum, bron in vervaldata(conn, v)]
    volgorde = {"rood": 0, "oranje": 1, "groen": 2}
    return sorted(plan, key=lambda p: (volgorde[p["kleur"]], p["naam"]))


# ---------- back-up ----------

def backup_context(conn) -> dict:
    st = backup.status(conn)
    return {"backup": st, "backups": backup.alle(st["map"])}


@router.post("/backup/nu")
def backup_nu():
    try:
        backup.maak()
    except backup.BackupFout as e:
        with db.connect() as conn:
            db.zet_instelling(conn, "backup_fout", str(e))
        return terug("/instellingen?backup=fout#backup")
    return terug("/instellingen?backup=gemaakt#backup")


@router.post("/backup/instellingen")
def backup_instellingen(map: str = Form(""), auto: str = Form("")):
    with db.connect() as conn:
        if map.strip():
            try:
                db.zet_instelling(conn, "backup_map", str(backup.controleer_doelmap(Path(map.strip()))))
            except backup.BackupFout as e:
                raise HTTPException(400, str(e))
        db.zet_instelling(conn, "backup_auto", "aan" if auto else "uit")
    return terug("/instellingen?backup=opgeslagen#backup")


def _herstel(zip_pad: Path):
    try:
        veiligheid = backup.herstel(zip_pad)
    except backup.BackupFout as e:
        raise HTTPException(400, str(e))
    return terug(f"/?hersteld={veiligheid.name}")


@router.post("/backup/terugzetten")
def backup_terugzetten(naam: str = Form(...)):
    with db.connect() as conn:
        map_ = backup.doelmap(conn)
    # alleen een back-up uit de lijst (geen willekeurig pad van buitenaf)
    kandidaat = next((b["pad"] for b in backup.alle(map_) if b["naam"] == naam), None)
    if not kandidaat:
        raise HTTPException(404, "Back-up niet gevonden")
    return _herstel(kandidaat)


@router.post("/backup/terugzetten-bestand")
async def backup_terugzetten_bestand(bestand: UploadFile = File(...)):
    with tempfile.TemporaryDirectory() as tmp:
        pad = Path(tmp) / "upload.zip"
        with open(pad, "wb") as uit:
            shutil.copyfileobj(bestand.file, uit)
        return _herstel(pad)


# ---------- herinneringen ----------

@router.post("/herinneringen")
def herinneringen(aan: str = Form("")):
    with db.connect() as conn:
        db.zet_instelling(conn, "herinneringen", "aan" if aan else "uit")
    return terug("/instellingen?herinneringen=opgeslagen#herinneringen")


def controle_meldingen() -> list[str]:
    """Wat er aandacht nodig heeft, voor een Windows-melding (voertuigen in stalling worden overgeslagen)."""
    regels = []
    with db.connect() as conn:
        for v in conn.execute("SELECT * FROM voertuig ORDER BY id").fetchall():
            if db.actieve_stalling(conn, v["id"]):
                continue
            naam = v["bijnaam"] or f"{v['merk'] or ''} {v['model'] or ''}".strip() or v["kenteken"]
            for p in voertuig_plan(conn, v):
                if p["kleur"] != "groen":
                    regels.append((f"{v['id']}|{p['naam']}|{p['kleur']}", f"{naam}: {p['naam'].lower()} ({p['tekst']})"))
        st = backup.status(conn)
        if st["auto"] and st["te_oud"] and conn.execute("SELECT 1 FROM voertuig LIMIT 1").fetchone():
            regels.append(("backup", "Er is al een tijd geen back-up gemaakt."))
    return regels


def _sleutel(regels) -> str:
    """Wat er aan de hand is, zonder de aftellende tekst ('nog 9 dagen'), zodat 'dezelfde melding' herkenbaar is."""
    return "\n".join(sorted(s for s, _ in regels))


def mag_melden(regels) -> bool:
    """Hooguit één melding per dag, en dezelfde situatie pas na een week opnieuw."""
    if not regels:
        return False
    with db.connect() as conn:
        if db.instelling(conn, "herinneringen", "aan") != "aan":
            return False
        try:
            vorige = json.loads(db.instelling(conn, "melding_laatst", "{}") or "{}")
            nu = datetime.now()
            if vorige.get("datum") == nu.date().isoformat():
                return False
            if vorige.get("sleutel") == _sleutel(regels) and (nu - datetime.fromisoformat(vorige["tijd"])).days < 7:
                return False
        except (ValueError, KeyError, TypeError):
            pass  # kapotte administratie: dan gewoon melden
    return True


def gemeld(regels) -> None:
    """Pas vastleggen nadat de melding echt getoond is (mislukt PowerShell, dan morgen opnieuw proberen)."""
    nu = datetime.now()
    with db.connect() as conn:
        db.zet_instelling(conn, "melding_laatst", json.dumps({"datum": nu.date().isoformat(), "tijd": nu.isoformat(),
                                                               "sleutel": _sleutel(regels)}))
