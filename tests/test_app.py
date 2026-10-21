import io
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app import db, planning, rdw
from app.main import app

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
       b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")


def pdf_bytes(paginas=1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(paginas):
        c.drawString(100, 750, f"Factuur pagina {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import backup
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    # back-ups nooit in de echte OneDrive/gebruikersmap van de ontwikkelaar
    monkeypatch.setattr(backup, "standaard_map", lambda: tmp_path / "backups")
    with TestClient(app) as c:
        yield c


def type_id(naam):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM onderhoudstype WHERE naam = ?", (naam,)).fetchone()["id"]


def controleer_alles(client):
    """Markeert alle facturen als gecontroleerd (zonder balken), zodat ze in de export meegaan."""
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM factuur WHERE gecontroleerd = 0")]
    for fid in ids:
        assert client.post(f"/facturen/{fid}/lakken", data={"boxen": "[]"}, follow_redirects=False).status_code == 303


def voeg_voertuig_toe(client, soort="Motorfiets", kenteken="MD-12-AB"):
    r = client.post("/voertuigen", data={"kenteken": kenteken, "voertuigsoort": soort, "merk": "Honda",
                                         "model": "CB500F"}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


# ---------- planning ----------

T = lambda km=None, mnd=None, id_=1: {"id": id_, "naam": "Olie", "interval_km": km, "interval_maanden": mnd}
B = lambda datum, km, *types: {"datum": datum, "kilometerstand": km, "types": list(types)}
VANDAAG = date(2026, 10, 20)


def test_planning_km_groen_oranje_rood():
    beurt = [B("2026-10-01", 10000, T(km=6000))]
    assert planning.overzicht(beurt, 12000, VANDAAG)[0]["kleur"] == "groen"
    assert planning.overzicht(beurt, 15500, VANDAAG)[0]["kleur"] == "oranje"
    assert planning.overzicht(beurt, 16000, VANDAAG)[0]["kleur"] == "rood"
    assert planning.overzicht(beurt, 12000, VANDAAG)[0]["volgende_km"] == 16000


def test_planning_tijd():
    t = T(mnd=12)
    assert planning.overzicht([B("2026-01-01", None, t)], None, VANDAAG)[0]["kleur"] == "groen"
    assert planning.overzicht([B("2025-11-10", None, t)], None, VANDAAG)[0]["kleur"] == "oranje"
    rood = planning.overzicht([B("2025-10-01", None, t)], None, VANDAAG)[0]
    assert rood["kleur"] == "rood" and rood["volgende_datum"] == date(2026, 10, 1)


def test_planning_wat_eerst_komt_telt():
    # km nog ruim, maar tijd verlopen → rood
    assert planning.overzicht([B("2025-01-01", 10000, T(km=6000, mnd=12))], 11000, VANDAAG)[0]["kleur"] == "rood"


def test_planning_alleen_laatste_beurt_en_geen_interval_genegeerd():
    olie, overig = T(km=6000), {"id": 2, "naam": "Overig", "interval_km": None, "interval_maanden": None}
    beurten = [B("2026-10-01", 20000, olie, overig), B("2025-01-01", 5000, olie)]  # nieuwste eerst
    res = planning.overzicht(beurten, 20500, VANDAAG)
    assert len(res) == 1 and res[0]["volgende_km"] == 26000
    assert planning.overzicht([], 1000, VANDAAG) == []


def test_plus_maanden_einde_maand():
    assert planning.plus_maanden(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert planning.plus_maanden(date(2026, 11, 15), 14) == date(2028, 1, 15)


# ---------- RDW ----------

def rdw_client(json_data, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=json_data)))


def test_rdw_mapping_en_normalisatie():
    data = [{"kenteken": "MD12AB", "voertuigsoort": "Motorfiets", "merk": "HONDA", "handelsbenaming": "CB500F",
             "datum_eerste_toelating": "20190315", "eerste_kleur": "ROOD", "cilinderinhoud": "471"}]
    res = rdw.zoek("md-12-ab", rdw_client(data))
    res.pop("rdw")  # extra velden: zie tests/test_nieuw.py
    assert res == {"kenteken": "MD12AB", "voertuigsoort": "Motorfiets", "merk": "Honda", "model": "CB500F",
                   "datum_eerste_toelating": "2019-03-15", "kleur": "Rood", "cilinderinhoud": 471}


def test_rdw_niet_gevonden_en_onbereikbaar():
    with pytest.raises(rdw.RdwFout, match="niet gevonden"):
        rdw.zoek("XX-99-XX", rdw_client([]))
    with pytest.raises(rdw.RdwFout, match="niet bereikbaar"):
        rdw.zoek("XX-99-XX", rdw_client({}, status=500))
    with pytest.raises(rdw.RdwFout, match="Vul een kenteken"):
        rdw.zoek(" - ")


# ---------- web + export ----------

def test_dropdown_verschilt_per_voertuigsoort(client):
    motor, auto = voeg_voertuig_toe(client, "Motorfiets"), voeg_voertuig_toe(client, "Personenauto", "AB-123-C")
    m, a = client.get(f"/voertuigen/{motor}/beurt/nieuw").text, client.get(f"/voertuigen/{auto}/beurt/nieuw").text
    assert "Kleppen stellen" in m and "APK" not in m
    assert "APK" in a and "Kleppen stellen" not in a
    assert "Olie vervangen" in m and "Olie vervangen" in a


def test_beurt_met_facturen_dashboard_en_export(client):
    vid = voeg_voertuig_toe(client)
    r = client.post(f"/voertuigen/{vid}/beurt/nieuw", follow_redirects=False,
                    data={"datum": "2026-04-01", "kilometerstand": "12000", "uitgevoerd_door": "Zelf",
                          "kosten": "45,50", "types": [str(type_id("Olie vervangen")), str(type_id("Oliefilter vervangen"))]},
                    files=[("facturen", ("bon.pdf", pdf_bytes(2), "application/pdf")),
                           ("facturen", ("foto.png", PNG, "image/png"))])
    assert r.status_code == 303

    pagina = client.get(f"/voertuigen/{vid}").text
    assert "Olie vervangen" in pagina and "18.000 km" in pagina and "€ 45,50" in pagina
    assert "bon.pdf" in pagina and "foto.png" in pagina

    controleer_alles(client)
    met = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf?kosten=0&kosten=1").content))
    tekst = met.pages[0].extract_text()
    assert len(met.pages) == 1 + 2 + 1  # overzicht + 2 pagina's PDF-factuur + 1 afbeelding
    assert "MD12AB" in tekst and "Kosten" in tekst and "45,50" in tekst
    assert "Bijlage 1" in met.pages[1].extract_text() and "Factuur pagina 1" in met.pages[1].extract_text()
    assert "Bijlage 2" in met.pages[3].extract_text()

    zonder = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf?kosten=0").content))
    assert "45,50" not in zonder.pages[0].extract_text()


def test_upload_verkeerd_bestand_geweigerd(client):
    vid = voeg_voertuig_toe(client)
    r = client.post(f"/voertuigen/{vid}/beurt/nieuw",
                    data={"datum": "2026-04-01", "types": [str(type_id("Olie vervangen"))]},
                    files=[("facturen", ("virus.exe", b"MZ...", "application/octet-stream"))])
    assert r.status_code == 400 and "geen geldige" in r.text
    # nep-PDF (juiste extensie, verkeerde inhoud)
    r = client.post(f"/voertuigen/{vid}/beurt/nieuw",
                    data={"datum": "2026-04-01", "types": [str(type_id("Olie vervangen"))]},
                    files=[("facturen", ("nep.pdf", b"geen pdf", "application/pdf"))])
    assert r.status_code == 400
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM beurt").fetchone()[0] == 0


def test_beurt_zonder_type_geweigerd(client):
    vid = voeg_voertuig_toe(client)
    r = client.post(f"/voertuigen/{vid}/beurt/nieuw", data={"datum": "2026-04-01"})
    assert r.status_code == 400 and "minimaal één" in r.text


def test_bewerken_en_verwijderen_ruimt_bestanden_op(client):
    vid = voeg_voertuig_toe(client)
    olie = str(type_id("Olie vervangen"))
    client.post(f"/voertuigen/{vid}/beurt/nieuw", data={"datum": "2026-04-01", "types": [olie]},
                files=[("facturen", ("bon.pdf", pdf_bytes(), "application/pdf"))])
    with db.connect() as conn:
        bid = conn.execute("SELECT id FROM beurt").fetchone()["id"]
    client.post(f"/beurt/{bid}/bewerken", data={"datum": "2026-05-01", "kilometerstand": "500", "types": [olie]})
    assert "500 km" in client.get(f"/voertuigen/{vid}").text
    assert client.get(f"/beurt/{bid}/bewerken").status_code == 200

    bestanden = list((db.DATA_DIR / "facturen").rglob("*.pdf"))
    assert len(bestanden) == 1
    client.post(f"/voertuigen/{vid}/verwijderen")
    assert not bestanden[0].exists()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM factuur").fetchone()[0] == 0


def test_onderhoudstype_aanpassen(client):
    olie = type_id("Olie vervangen")
    client.post("/onderhoudstypes", data={"id": str(olie), "naam": "Olie vervangen",
                                          "voertuigsoorten": ["Motorfiets"], "interval_km": "8000"})
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM onderhoudstype WHERE id = ?", (olie,)).fetchone()
    assert (t["voertuigsoorten"], t["interval_km"], t["interval_maanden"]) == ("Motorfiets", 8000, None)
    assert client.get("/onderhoudstypes").status_code == 200


# ---------- regressies uit de code-review ----------

def nieuwe_beurt(client, vid, data=None, files=None):
    basis = {"datum": "2026-04-01", "types": [str(type_id("Olie vervangen"))]}
    return client.post(f"/voertuigen/{vid}/beurt/nieuw", data={**basis, **(data or {})}, files=files,
                       follow_redirects=False)


def test_bedragen_nederlandse_notatie_en_nette_fout(client):
    vid = voeg_voertuig_toe(client)
    assert nieuwe_beurt(client, vid, {"kosten": "€ 1.250,00", "kilometerstand": "12.000"}).status_code == 303
    with db.connect() as conn:
        b = conn.execute("SELECT kosten, kilometerstand FROM beurt").fetchone()
    assert (b["kosten"], b["kilometerstand"]) == (1250.0, 12000)
    r = nieuwe_beurt(client, vid, {"kosten": "veel", "opmerking": "blijft staan"})
    assert r.status_code == 400 and "geen geldig bedrag" in r.text and "blijft staan" in r.text


def test_ongeldige_datum_en_type_ids(client):
    vid = voeg_voertuig_toe(client)
    assert nieuwe_beurt(client, vid, {"datum": "20240101"}).status_code == 400
    assert nieuwe_beurt(client, vid, {"types": ["abc", "99999"]}).status_code == 400


def test_versleutelde_pdf_geweigerd(client):
    from pypdf import PdfWriter
    w = PdfWriter(clone_from=io.BytesIO(pdf_bytes()))
    w.encrypt("geheim")
    buf = io.BytesIO()
    w.write(buf)
    r = nieuwe_beurt(client, voeg_voertuig_toe(client), files=[("facturen", ("slot.pdf", buf.getvalue(), "application/pdf"))])
    assert r.status_code == 400 and "wachtwoord" in r.text


def test_export_met_vreemde_typenaam_en_ontbrekend_bestand(client):
    client.post("/onderhoudstypes", data={"naam": "Remmen <voor> & achter", "voertuigsoorten": ["*"]})
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, {"types": [str(type_id("Remmen <voor> & achter"))]},
                 files=[("facturen", ("bon.pdf", pdf_bytes(), "application/pdf"))])
    controleer_alles(client)
    for f in (db.DATA_DIR / "facturen").rglob("*.pdf"):
        f.unlink()
    r = client.get(f"/voertuigen/{vid}/export.pdf")
    assert r.status_code == 200
    pdf = PdfReader(io.BytesIO(r.content))
    assert "Remmen <voor> & achter" in pdf.pages[0].extract_text()
    assert "ontbreekt" in pdf.pages[1].extract_text()
    with db.connect() as conn:
        fid = conn.execute("SELECT id FROM factuur").fetchone()["id"]
    assert client.get(f"/facturen/{fid}").status_code == 404


def test_gedraaide_factuur_kop_bovenaan(client):
    from pypdf import PdfWriter
    w = PdfWriter(clone_from=io.BytesIO(pdf_bytes()))
    w.pages[0].rotate(90)
    buf = io.BytesIO()
    w.write(buf)
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("scan.pdf", buf.getvalue(), "application/pdf"))])
    controleer_alles(client)
    pagina = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content)).pages[1]
    assert pagina.rotation == 0
    # liggende pagina: breder dan hoog, met de kopstrook bovenaan erbij
    assert float(pagina.cropbox.width) > float(pagina.cropbox.height)
    assert "Bijlage 1" in pagina.extract_text()


def test_bewerken_behoudt_type_dat_niet_meer_bij_soort_hoort(client):
    vid = voeg_voertuig_toe(client)
    ketting = type_id("Ketting smeren/spannen")
    nieuwe_beurt(client, vid, {"types": [str(ketting)]})
    client.post("/onderhoudstypes", data={"id": str(ketting), "naam": "Ketting smeren/spannen",
                                          "voertuigsoorten": ["Personenauto"], "interval_km": "1000"})
    with db.connect() as conn:
        bid = conn.execute("SELECT id FROM beurt").fetchone()["id"]
    assert "Ketting smeren/spannen" in client.get(f"/beurt/{bid}/bewerken").text
