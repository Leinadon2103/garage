import io
import json
import sqlite3

import httpx
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app import beeld, db, lakken, planning
from app.main import kenteken_opmaak
from tests.test_app import B, T, VANDAAG, client, controleer_alles, nieuwe_beurt, pdf_bytes, voeg_voertuig_toe  # noqa: F401 (fixture)


def pdf_met_adres() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 780, "Motorgarage Snel - Industrieweg 5")
    c.drawString(72, 740, "Aan: Jan de Vries")
    c.drawString(72, 725, "Dorpsstraat 12")
    c.drawString(72, 710, "1234 AB Utrecht")
    c.drawString(72, 695, "jan@example.nl")
    c.drawString(72, 650, "Olie verversen  EUR 54,00")
    c.save()
    return buf.getvalue()


def eerste_factuur():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM factuur ORDER BY id").fetchone()


# ---------- weglakken ----------

def test_upload_gaat_naar_lakscherm_met_suggesties(client):
    vid = voeg_voertuig_toe(client)
    r = nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_met_adres(), "application/pdf"))])
    assert r.headers["location"].endswith("/lakken")
    r = client.get(r.headers["location"])  # beurt -> eerste ongecontroleerde factuur
    assert r.status_code == 200 and "Persoonsgegevens weglakken" in r.text
    for reden in ("postcode", "e-mail", "adres"):
        assert f'"reden": "{reden}"' in r.text
    fid = eerste_factuur()["id"]
    png = client.get(f"/facturen/{fid}/pagina/0.png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert client.get(f"/facturen/{fid}/pagina/5.png").status_code == 404
    assert "nog niet gecontroleerd" in client.get(f"/voertuigen/{vid}").text


def test_lakken_verwijdert_tekst_echt_en_bewaart_origineel(client):
    client.post("/instellingen/prive", data={"termen": "Jan de Vries\n"})
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_met_adres(), "application/pdf"))])
    f = eerste_factuur()
    with db.connect() as conn:
        termen = db.prive_termen(conn)
    sugg = lakken.suggesties(db.DATA_DIR / f["pad"], f["mime_type"], termen)
    eigen = [s for s in sugg if s["reden"] == "jouw gegevens"]
    assert eigen and all(s["actief"] for s in eigen)
    assert not any(s["actief"] for s in sugg if s["reden"] != "jouw gegevens")  # patronen uit bij eigen termen

    r = client.post(f"/facturen/{f['id']}/lakken", data={"boxen": json.dumps(sugg)}, follow_redirects=False)
    assert r.status_code == 303
    f2 = eerste_factuur()
    assert f2["gecontroleerd"] == 1 and f2["origineel_pad"] == f["pad"] and f2["pad"] != f["pad"]
    gelakt = PdfReader(io.BytesIO(client.get(f"/facturen/{f['id']}").content)).pages[0].extract_text()
    for geheim in ("Jan de Vries", "1234 AB", "jan@example.nl", "Dorpsstraat 12"):
        assert geheim not in gelakt
    assert "Olie verversen" in gelakt  # de rest blijft staan
    origineel = PdfReader(io.BytesIO(client.get(f"/facturen/{f['id']}/origineel").content)).pages[0].extract_text()
    assert "Jan de Vries" in origineel
    export = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content))
    assert "Jan de Vries" not in export.pages[1].extract_text()  # export gebruikt de gelakte versie

    # opnieuw lakken vervangt de oude gelakte versie (geen weesbestanden)
    client.post(f"/facturen/{f['id']}/lakken", data={"boxen": "[]"})
    assert len(list((db.DATA_DIR / "facturen").rglob("*-gelakt*"))) == 1
    # verwijderen ruimt origineel én gelakte versie op
    client.post(f"/facturen/{f['id']}/verwijderen")
    assert not list((db.DATA_DIR / "facturen").rglob("*.pdf"))


def test_lakken_afbeelding_zwart_en_zonder_exif(client):
    im = Image.new("RGB", (200, 100), "white")
    exif = Image.Exif()
    exif[0x010F] = "Telefoonmerk"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("foto.jpg", buf.getvalue(), "image/jpeg"))])
    fid = eerste_factuur()["id"]
    client.post(f"/facturen/{fid}/lakken", data={"boxen": '[{"pagina":0,"x0":0,"y0":0,"x1":0.5,"y1":1}]'})
    gelakt = Image.open(io.BytesIO(client.get(f"/facturen/{fid}").content))
    assert gelakt.getpixel((10, 50))[0] < 20 and gelakt.getpixel((190, 50))[0] > 230
    assert not gelakt.getexif()


def test_gedraaide_pdf_lakken_op_juiste_plek(client):
    from pypdf import PdfWriter
    w = PdfWriter(clone_from=io.BytesIO(pdf_met_adres()))
    w.pages[0].rotate(90)
    buf = io.BytesIO()
    w.write(buf)
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("scan.pdf", buf.getvalue(), "application/pdf"))])
    f = eerste_factuur()
    sugg = [s for s in lakken.suggesties(db.DATA_DIR / f["pad"], f["mime_type"], []) if s["reden"] == "postcode"]
    client.post(f"/facturen/{f['id']}/lakken", data={"boxen": json.dumps(sugg)})
    tekst = PdfReader(io.BytesIO(client.get(f"/facturen/{f['id']}").content)).pages[0].extract_text()
    assert "1234 AB" not in tekst and "Olie verversen" in tekst


def test_ongeldige_boxen(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    assert client.post(f"/facturen/{eerste_factuur()['id']}/lakken", data={"boxen": "kapot"}).status_code == 400


def test_instellingen_pagina(client):
    assert client.get("/onderhoudstypes").status_code == 200  # redirect naar /instellingen
    client.post("/instellingen/prive", data={"termen": "  Jan  \n\nab\nDorpsstraat 12"})
    with db.connect() as conn:
        assert db.prive_termen(conn) == ["Jan", "Dorpsstraat 12"]  # te korte termen genegeerd


# ---------- voertuigbeeld ----------

def test_beeld_kiezen_en_opslaan(client, monkeypatch):
    jpg = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(jpg, "JPEG")

    def nep_get(url, **_):
        if "sketchfab.com/v3" in url:
            data = {"results": [{"uid": "a" * 32, "name": "Ninja", "user": {"displayName": "x"},
                                 "thumbnails": {"images": [{"url": "https://media.sketchfab.com/t.jpg", "width": 640}]}}]}
        elif "commons.wikimedia.org/w/api" in url:
            data = {"query": {"pages": {"1": {"index": 1, "title": "File:Ninja.jpg", "imageinfo": [
                {"thumburl": "https://thumb.wikimedia.org/n.jpg",
                 "descriptionurl": "https://commons.wikimedia.org/wiki/File:Ninja.jpg"}]}}}}
        else:
            return httpx.Response(200, content=jpg.getvalue(), request=httpx.Request("GET", url))
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(beeld.httpx, "get", nep_get)
    vid = voeg_voertuig_toe(client)
    pagina = client.get(f"/voertuigen/{vid}/beeld").text
    assert "Ninja" in pagina and "media.sketchfab.com/t.jpg" in pagina and "thumb.wikimedia.org/n.jpg" in pagina

    client.post(f"/voertuigen/{vid}/beeld", data={"soort": "3d", "uid": "a" * 32,
                                                  "thumb": "https://media.sketchfab.com/t.jpg", "titel": "Ninja"})
    assert f"sketchfab.com/models/{'a' * 32}/embed" in client.get(f"/voertuigen/{vid}").text
    assert client.get(f"/voertuigen/{vid}/beeld.jpg").status_code == 200
    assert "beeld.jpg" in client.get("/").text

    client.post(f"/voertuigen/{vid}/beeld", data={"soort": "foto", "thumb": "https://thumb.wikimedia.org/n.jpg", "titel": "Ninja",
                                                  "bron": "https://commons.wikimedia.org/wiki/File:Ninja.jpg"})
    assert "Wikimedia Commons" not in client.get(f"/voertuigen/{vid}").text  # geen bronvermelding over de foto
    r = client.post(f"/voertuigen/{vid}/beeld", data={"soort": "foto", "thumb": "https://evil.example/x.jpg"})
    assert r.status_code == 502  # alleen bekende afbeeldingsbronnen

    client.post(f"/voertuigen/{vid}/beeld/upload", files={"foto": ("mijn.jpg", jpg.getvalue(), "image/jpeg")})
    assert len(list((db.DATA_DIR / "beelden").glob("*.jpg"))) == 1  # oude beeld opgeruimd
    assert "sketchfab" not in client.get(f"/voertuigen/{vid}").text
    assert client.post(f"/voertuigen/{vid}/beeld/upload",
                       files={"foto": ("x.jpg", b"geen foto", "image/jpeg")}).status_code == 400


def test_beeld_zonder_internet(client, monkeypatch):
    def kapot(*_, **__):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(beeld.httpx, "get", kapot)
    vid = voeg_voertuig_toe(client)
    r = client.get(f"/voertuigen/{vid}/beeld")
    assert r.status_code == 200 and "geen internet" in r.text


# ---------- overig ----------

def test_kenteken_opmaak_en_planningtekst():
    assert kenteken_opmaak("00MDB8") == "00-MDB-8"
    assert kenteken_opmaak("JHK73J") == "JHK-73-J"
    assert kenteken_opmaak("12ABCD") == "12-AB-CD"
    s = planning.overzicht([B("2026-10-01", 10000, T(km=6000))], 15100, VANDAAG)[0]
    assert s["tekst"] == "nog 900 km" and 0.8 < s["deel"] < 0.9
    s = planning.overzicht([B("2025-08-01", None, T(mnd=12))], None, VANDAAG)[0]
    assert s["tekst"] == "3 maanden te laat"


def test_migratie_bestaande_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    oud = sqlite3.connect(tmp_path / "onderhoud.db")
    oud.executescript(
        "CREATE TABLE voertuig (id INTEGER PRIMARY KEY, kenteken TEXT NOT NULL, voertuigsoort TEXT NOT NULL,"
        " merk TEXT, model TEXT, datum_eerste_toelating TEXT, kleur TEXT, cilinderinhoud INTEGER, bijnaam TEXT);"
        "CREATE TABLE factuur (id INTEGER PRIMARY KEY, beurt_id INTEGER NOT NULL, pad TEXT NOT NULL,"
        " originele_naam TEXT NOT NULL, mime_type TEXT NOT NULL);"
        "INSERT INTO voertuig (kenteken, voertuigsoort) VALUES ('AB12CD', 'Motorfiets');")
    oud.close()
    db.init()
    with db.connect() as conn:
        v = conn.execute("SELECT * FROM voertuig").fetchone()
        kolommen = {r["name"] for r in conn.execute("PRAGMA table_info(factuur)")}
    assert v["kenteken"] == "AB12CD" and v["beeld_type"] is None
    assert {"origineel_pad", "gecontroleerd"} <= kolommen


# ---------- regressies uit de tweede review ----------

def test_export_neemt_ongecontroleerde_factuur_niet_mee(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_met_adres(), "application/pdf"))])
    export = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content))
    assert len(export.pages) == 2
    assert "Jan de Vries" not in export.pages[1].extract_text()
    assert "nog niet gecontroleerd" in export.pages[1].extract_text()
    assert "niet meegenomen" in client.get(f"/voertuigen/{vid}").text


def test_opnieuw_lakken_bouwt_voort_op_eerdere_balken(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_met_adres(), "application/pdf"))])
    f = eerste_factuur()
    naam = [s for s in lakken.suggesties(db.DATA_DIR / f["pad"], f["mime_type"], ["Jan de Vries"])
            if s["reden"] == "jouw gegevens"]
    client.post(f"/facturen/{f['id']}/lakken", data={"boxen": json.dumps(naam)})
    pagina = client.get(f"/facturen/{f['id']}/lakken").text
    assert "eerder gelakt" in pagina  # eerdere balk staat weer aan in het lakscherm
    # stuurt de browser die balken mee terug (zoals de pagina doet), dan blijft de naam weg
    eerder = json.loads(eerste_factuur()["boxen"])
    client.post(f"/facturen/{f['id']}/lakken", data={"boxen": json.dumps(eerder)})
    tekst = PdfReader(io.BytesIO(client.get(f"/facturen/{f['id']}").content)).pages[0].extract_text()
    assert "Jan de Vries" not in tekst


def test_annotaties_en_tekst_buiten_cropbox_verdwijnen(client):
    import pymupdf
    doc = pymupdf.open(stream=pdf_met_adres(), filetype="pdf")
    pagina = doc[0]
    notitie = pagina.add_text_annot((300, 300), "Geheim-notitie Jansen")
    notitie.set_info(title="Jansen-auteur")
    pagina.insert_text((72, 20), "Verborgen 0612345678")  # komt buiten de cropbox te liggen
    pagina.set_cropbox(pymupdf.Rect(0, 40, pagina.rect.width, pagina.rect.height))
    doc.set_metadata({"author": "Jan de Vries"})
    buf = doc.tobytes()
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", buf, "application/pdf"))])
    controleer_alles(client)
    gelakt = client.get(f"/facturen/{eerste_factuur()['id']}").content
    d = pymupdf.open(stream=gelakt, filetype="pdf")
    assert not list(d[0].annots())
    alle_tekst = "".join(p.get_text() for p in d)
    for geheim in ("Geheim-notitie", "Jansen-auteur", "Verborgen"):
        assert geheim not in alle_tekst
    assert "Jan de Vries" not in str(d.metadata)
    assert b"Jansen" not in gelakt and b"0612345678" not in gelakt


def test_jpeg_commentaar_verdwijnt(client):
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, "JPEG", comment=b"Jansen-comment")
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.jpg", buf.getvalue(), "image/jpeg"))])
    controleer_alles(client)
    assert b"Jansen-comment" not in client.get(f"/facturen/{eerste_factuur()['id']}").content
    client.post(f"/voertuigen/{vid}/beeld/upload", files={"foto": ("m.jpg", buf.getvalue(), "image/jpeg")})
    assert b"Jansen-comment" not in client.get(f"/voertuigen/{vid}/beeld.jpg").content


def test_beeld_onbekend_voertuig_laat_geen_bestand_achter(client):
    buf = io.BytesIO()
    Image.new("RGB", (5, 5)).save(buf, "JPEG")
    assert client.post("/voertuigen/999/beeld/upload", files={"foto": ("m.jpg", buf.getvalue(), "image/jpeg")}).status_code == 404
    assert not list((db.DATA_DIR / "beelden").glob("*")) if (db.DATA_DIR / "beelden").exists() else True
