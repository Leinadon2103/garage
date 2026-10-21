import io
import json
import sqlite3
from datetime import date

import httpx
from PIL import Image
from reportlab.pdfgen import canvas

from app import db, factuur_lezen, planning, rdw
from tests.test_app import B, T, VANDAAG, client, nieuwe_beurt, type_id, voeg_voertuig_toe  # noqa: F401 (fixture)


def pdf(*regels) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, r in enumerate(regels):
        c.drawString(60, 790 - i * 16, r)
    c.save()
    return buf.getvalue()


def motor_types():
    with db.connect() as conn:
        return db.types_voor(conn, "Motorfiets")


FACTUUR = pdf(
    "Motorgarage Snel B.V.",
    "Industrieweg 5, 3542 AD Utrecht",
    "Factuurdatum: 14-03-2025",
    "Vervaldatum: 28-03-2025",
    "Kenteken: 00-MDB-8   Kilometerstand: 12.345",
    "Motorolie 10W-40 3L           54,00",
    "Oliefilter HF303              18,50",
    "Remblokken voor (set)         45,00",
    "Subtotaal                    117,50",
    "BTW 21%                       24,68",
    "Totaal incl. BTW          € 142,18",
)


# ---------- factuur uitlezen ----------

def test_factuur_uitlezen_basis(client):
    types = motor_types()
    r = factuur_lezen.lees(FACTUUR, "application/pdf", types)
    namen = {t["id"]: t["naam"] for t in types}
    assert r["leesbaar"]
    assert r["datum"] == "2025-03-14"  # factuurdatum, niet de vervaldatum
    assert r["kosten"] == 142.18  # totaal incl., niet subtotaal of btw
    assert r["kilometerstand"] == 12345
    assert r["uitgevoerd_door"] == "Motorgarage Snel B.V."
    assert {namen[t] for t in r["types"]} == {"Olie vervangen", "Oliefilter vervangen", "Remblokken voor vervangen"}


def test_factuur_varianten(client):
    types = motor_types()
    namen = {t["id"]: t["naam"] for t in types}
    r = factuur_lezen.lees(pdf("Factuur", "Datum 2 februari 2026", "Alleen oliefilter geleverd",
                               "Te betalen", "EUR 18,50", "Stand teller 9800 km"), "application/pdf", types)
    assert r["datum"] == "2026-02-02" and r["kosten"] == 18.5 and r["kilometerstand"] == 9800
    assert [namen[t] for t in r["types"]] == ["Oliefilter vervangen"]  # geen vals 'olie'
    r = factuur_lezen.lees(pdf("Bon 05/06/2026", "Kettingset DID 520 + tandwielen", "Bougie NGK x2",
                               "Bedragen: 189,00  12,50  1.234,56"), "application/pdf", types)
    assert r["kosten"] == 1234.56  # geen totaalregel: hoogste bedrag
    assert {namen[t] for t in r["types"]} == {"Ketting + tandwielen vervangen", "Bougies vervangen"}
    # autotypes kunnen niet uit een motorfactuur komen
    r = factuur_lezen.lees(pdf("APK keuring en distributieriem", "Totaal 500,00"), "application/pdf", types)
    assert r["types"] == []


def test_factuur_niet_leesbaar():
    assert factuur_lezen.lees(b"\xff\xd8\xff...", "image/jpeg", []) == {"leesbaar": False}
    assert factuur_lezen.lees(pdf(""), "application/pdf", []) == {"leesbaar": False}  # scan zonder tekst
    assert factuur_lezen.lees(b"%PDF kapot", "application/pdf", []) == {"leesbaar": False}


def test_endpoint_combineert_meerdere_facturen(client):
    vid = voeg_voertuig_toe(client)
    tweede = pdf("Bandenservice", "Datum: 20-03-2025", "Michelin Road 6 achterband", "Totaal: 210,00")
    jpg = io.BytesIO()
    Image.new("RGB", (10, 10)).save(jpg, "JPEG")
    r = client.post(f"/voertuigen/{vid}/factuur-uitlezen", files=[
        ("facturen", ("a.pdf", FACTUUR, "application/pdf")), ("facturen", ("b.pdf", tweede, "application/pdf")),
        ("facturen", ("c.jpg", jpg.getvalue(), "image/jpeg"))]).json()
    assert r["leesbaar"] and r["onleesbaar"] == 1
    assert r["kosten"] == 352.18 and r["datum"] == "2025-03-20" and r["kilometerstand"] == 12345
    assert "Banden vervangen" in r["type_namen"] and "Olie vervangen" in r["type_namen"]
    with db.connect() as conn:  # stateless: niets opgeslagen
        assert conn.execute("SELECT COUNT(*) FROM beurt").fetchone()[0] == 0
    fout = client.post(f"/voertuigen/{vid}/factuur-uitlezen", files=[("facturen", ("x.exe", b"MZ", "x"))]).json()
    assert not fout["leesbaar"] and "geen geldige" in fout["fout"]


def test_beurtformulier_heeft_factuur_bovenaan(client):
    vid = voeg_voertuig_toe(client)
    html = client.get(f"/voertuigen/{vid}/beurt/nieuw").text
    assert html.index("Begin met je factuur") < html.index("Wat is er gedaan?")
    assert f"/voertuigen/{vid}/factuur-uitlezen" in html


# ---------- intervallen per voertuig ----------

def test_planning_eigen_interval_en_bron():
    olie = T(km=6000, mnd=12)
    beurten = [B("2026-10-01", 10000, olie)]
    standaard = planning.overzicht(beurten, 12000, VANDAAG)[0]
    assert standaard["bron"] == "vuistregel" and standaard["volgende_km"] == 16000
    eigen = planning.overzicht(beurten, 12000, VANDAAG, intervallen={1: (4000, None)})[0]
    assert eigen["bron"] == "eigen" and eigen["volgende_km"] == 14000 and eigen["volgende_datum"] is None
    # eigen schema kan een herinnering ook uitzetten
    assert planning.overzicht(beurten, 12000, VANDAAG, intervallen={1: (None, None)}) == []


def test_intervallen_pagina_opslaan(client):
    vid = voeg_voertuig_toe(client)
    olie, ketting = type_id("Olie vervangen"), type_id("Ketting smeren/spannen")
    html = client.get(f"/voertuigen/{vid}/intervallen").text
    assert "Kleppen stellen" in html and "APK" not in html and "vuistregel: 6.000" in html
    # olie aangepast, ketting gelijk aan vuistregel -> alleen olie wordt opgeslagen
    r = client.post(f"/voertuigen/{vid}/intervallen", data={f"km_{olie}": "7.500", f"mnd_{olie}": "12",
                    f"km_{ketting}": "1000", f"mnd_{ketting}": "", "schema_bron": "Instructieboekje p. 112"},
                    follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as conn:
        assert db.intervallen(conn, vid) == {olie: (7500, 12)}
        assert conn.execute("SELECT schema_bron FROM voertuig").fetchone()[0] == "Instructieboekje p. 112"
    nieuwe_beurt(client, vid, {"kilometerstand": "1000"})
    pagina = client.get(f"/voertuigen/{vid}").text
    assert "8.500 km" in pagina and "volgens eigen schema" in pagina
    assert client.post(f"/voertuigen/{vid}/intervallen", data={f"km_{olie}": "abc"}).status_code == 400


# ---------- RDW en km-schatting ----------

RDW_VOERTUIG = [{"kenteken": "00MDB8", "voertuigsoort": "Motorfiets", "merk": "KAWASAKI", "handelsbenaming": "NINJA ZX-4R",
                 "datum_eerste_toelating": "20240122", "datum_eerste_tenaamstelling_in_nederland": "20260701",
                 "openstaande_terugroepactie_indicator": "Ja", "tellerstandoordeel": "Niet geregistreerd",
                 "jaar_laatste_registratie_tellerstand": "2026", "catalogusprijs": "10298", "massa_rijklaar": "189",
                 "eerste_kleur": "GROEN", "cilinderinhoud": "399"}]
RDW_BRANDSTOF = [{"kenteken": "00MDB8", "brandstof_omschrijving": "Benzine", "nettomaximumvermogen": "57.00"}]


def rdw_nep(request):
    return httpx.Response(200, json=RDW_BRANDSTOF if "8ys7" in str(request.url) else RDW_VOERTUIG)


def test_rdw_extra_velden():
    res = rdw.zoek("00-MDB-8", httpx.Client(transport=httpx.MockTransport(rdw_nep)))
    assert res["rdw"] == {"apk_vervaldatum": None, "terugroepactie": True, "tellerstandoordeel": "Niet geregistreerd",
                          "jaar_tellerstand": 2026, "catalogusprijs": 10298, "datum_tenaamstelling_nl": "2026-07-01",
                          "massa_rijklaar": 189, "brandstof": "Benzine", "vermogen_kw": 57}


def test_rdw_brandstof_mag_mislukken():
    def half(request):
        return httpx.Response(500) if "8ys7" in str(request.url) else httpx.Response(200, json=RDW_VOERTUIG)
    res = rdw.zoek("00MDB8", httpx.Client(transport=httpx.MockTransport(half)))
    assert res["merk"] == "Kawasaki" and "brandstof" not in res["rdw"]


def test_voertuigpagina_toont_rdw_info_en_vernieuwen(client, monkeypatch):
    extra = rdw.zoek("00MDB8", httpx.Client(transport=httpx.MockTransport(rdw_nep)))["rdw"]
    r = client.post("/voertuigen", follow_redirects=False, data={
        "kenteken": "00MDB8", "voertuigsoort": "Motorfiets", "merk": "Kawasaki", "model": "NINJA ZX-4R",
        "datum_eerste_toelating": "2024-01-22", "rdw_json": json.dumps({**extra, "kwaad": "<script>"})})
    vid = r.headers["location"].rsplit("/", 1)[1]
    html = client.get(f"/voertuigen/{vid}").text
    for tekst in ("Openstaande terugroepactie", "Import: in NL sinds", "nog geen km-standen geregistreerd", "57 kW", "Benzine",
                  "ruwe schatting"):
        assert tekst in html, tekst
    with db.connect() as conn:
        assert "kwaad" not in conn.execute("SELECT rdw_json FROM voertuig").fetchone()[0]

    # vernieuwen haalt de actuele status op (terugroepactie inmiddels afgehandeld)
    afgehandeld = {**extra, "terugroepactie": False}
    monkeypatch.setattr(rdw, "zoek", lambda k: {"rdw": afgehandeld})
    client.post(f"/voertuigen/{vid}/rdw")
    assert "Openstaande terugroepactie" not in client.get(f"/voertuigen/{vid}").text


def test_km_schatting():
    assert planning.km_schatting("Motorfiets", "2024-01-22", date(2026, 10, 20)) == 9600  # 2,75 jaar x 3.500
    assert planning.km_schatting("Personenauto", "2025-10-31", date(2026, 10, 20)) == 12600
    assert planning.km_schatting("Aanhangwagen", "2020-01-01") is None
    assert planning.km_schatting("Motorfiets", None) is None


def test_schatting_verdwijnt_bij_echte_km(client):
    vid = voeg_voertuig_toe(client)
    client.post(f"/voertuigen/{vid}/beurt/nieuw", data={"datum": "2026-01-01", "kilometerstand": "4321",
                                                        "types": [str(type_id("Olie vervangen"))]})
    assert "ruwe schatting" not in client.get(f"/voertuigen/{vid}").text


# ---------- app & migratie ----------

def test_gezond_en_manifest(client):
    assert client.get("/gezond").json() == {"app": "voertuigonderhoud"}
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200 and json.loads(manifest.content)["display"] == "standalone"
    assert client.get("/static/icoon-192.png").status_code == 200
    assert 'rel="manifest"' in client.get("/").text


def test_migratie_nieuwe_tabel_en_kolommen(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    oud = sqlite3.connect(tmp_path / "onderhoud.db")
    oud.executescript("CREATE TABLE voertuig (id INTEGER PRIMARY KEY, kenteken TEXT NOT NULL, voertuigsoort TEXT NOT NULL,"
                      " merk TEXT, model TEXT, datum_eerste_toelating TEXT, kleur TEXT, cilinderinhoud INTEGER, bijnaam TEXT);")
    oud.close()
    db.init()
    with db.connect() as conn:
        kolommen = {r["name"] for r in conn.execute("PRAGMA table_info(voertuig)")}
        assert {"schema_bron", "rdw_json", "rdw_bijgewerkt"} <= kolommen
        assert conn.execute("SELECT COUNT(*) FROM voertuig_interval").fetchone()[0] == 0


def laad_starter():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("garage", Path(__file__).parent.parent / "Garage.pyw")
    garage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(garage)
    return garage


def test_starter_datamap_poort_en_enkele_instantie(monkeypatch, tmp_path):
    garage = laad_starter()
    assert garage.documenten().is_dir()
    monkeypatch.delenv("ONDERHOUD_DATA", raising=False)
    assert garage.data_map() == garage.HIER / "data"  # ontwikkelen: projectmap
    monkeypatch.setattr(garage, "BEVROREN", True)
    assert garage.data_map() == garage.documenten() / "Garage"  # geïnstalleerd: Documenten\Garage
    monkeypatch.setenv("ONDERHOUD_DATA", str(tmp_path))
    assert garage.data_map() == tmp_path
    assert 1024 < garage.vrije_poort() < 65536
    monkeypatch.setattr(garage, "MUTEX_NAAM", f"Garage-test-{tmp_path.name}")
    assert garage.al_open() is False  # eerste
    tweede = laad_starter()
    monkeypatch.setattr(tweede, "MUTEX_NAAM", f"Garage-test-{tmp_path.name}")
    assert tweede.al_open() is True  # tweede ziet de eerste


def test_starter_start_en_stopt_server(monkeypatch, tmp_path):
    import httpx
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    garage = laad_starter()
    poort = garage.vrije_poort()
    server, draad = garage.start_server(poort)
    assert httpx.get(f"http://127.0.0.1:{poort}/gezond").json() == {"app": "voertuigonderhoud"}
    server.should_exit = True
    draad.join(timeout=5)
    assert not draad.is_alive()


def test_foutpagina_heeft_app_opmaak(client):
    r = client.get("/voertuigen/999")
    assert r.status_code == 404 and "Dat lukte niet" in r.text and "Naar de garage" in r.text
    assert client.get("/voertuigen/999", headers={"accept": "application/json"}).json()["detail"]


def test_import_label_op_volledige_datum(client):
    def maak(toelating, nl):
        r = client.post("/voertuigen", follow_redirects=False, data={
            "kenteken": "AB12CD", "voertuigsoort": "Personenauto", "datum_eerste_toelating": toelating,
            "rdw_json": json.dumps({"datum_tenaamstelling_nl": nl})})
        return client.get(r.headers["location"]).text
    assert "Import: in NL sinds" not in maak("2015-12-28", "2016-01-02")  # jaarwisseling, geen import
    assert "Import: in NL sinds" in maak("2024-01-22", "2026-07-01")


# ---------- regressies factuur-uitlezen (review) ----------

def lees_tekst(*regels):
    return factuur_lezen.lees(pdf(*regels), "application/pdf", motor_types())


def namen(r):
    alle = {t["id"]: t["naam"] for t in motor_types()}
    return {alle[t] for t in r["types"]}


def test_kosten_misfires(client):
    assert lees_tekst("Omschrijving Aantal Prijs Totaal", "Oliefilter 1 50,00 50,00", "Bougie 1 28,65 28,65",
                      "Totaal incl. BTW 78,65")["kosten"] == 78.65
    assert lees_tekst("Totaal arbeid 2,50 uur", "Totaal BTW 21,00", "Totaal excl. BTW 100,00",
                      "Totaal incl. BTW 121,00")["kosten"] == 121.0
    assert lees_tekst("Totaal incl. BTW 121,00", "Gelieve binnen 14 dagen te betalen.",
                      "Bij te late betaling € 15,00 administratiekosten")["kosten"] == 121.0


def test_datum_misfires(client):
    assert lees_tekst("Datum eerste toelating 01-03-2015", "Datum: 15-10-2026", "Olie")["datum"] == "2026-10-15"
    assert lees_tekst("Factuurdatum", "12-09-2026", "APK geldig tot 15-10-2027")["datum"] == "2026-09-12"


def test_km_misfires(client):
    assert lees_tekst("Volgende onderhoud bij 45.000 km", "12 maanden garantie", "Km-stand 31.250")["kilometerstand"] == 31250
    assert lees_tekst("Haal en breng binnen 15 km: 25,00", "Kilometerstand: 24.512")["kilometerstand"] == 24512
    assert lees_tekst("Art.nr. HKM 123456", "Oliefilter")["kilometerstand"] is None
    assert lees_tekst("Kilometer stand: 24.512")["kilometerstand"] == 24512
    assert lees_tekst("Km.stand 24512")["kilometerstand"] == 24512


def test_type_misfires(client):
    assert namen(lees_tekst("Ruitenwisservloeistof bijgevuld", "Oliepeil gecontroleerd", "Volgende APK: 01-01-2027")) == set()
    assert namen(lees_tekst("Remblokken vóór vervangen")) == {"Remblokken voor vervangen"}
    assert namen(lees_tekst("Uitlijnen voor; remblokken achter")) == {"Remblokken achter vervangen"}
    assert namen(lees_tekst("Voorvorkolie vervangen", "Versnellingsbakolie")) == set()
    assert namen(lees_tekst("Olie filter HF204")) == {"Oliefilter vervangen"}
    assert namen(lees_tekst("Achterband 180/55 gemonteerd")) == {"Banden vervangen"}
    assert namen(lees_tekst("Distributieband", "Tandband")) == set()


def test_bedrijf_niet_klant(client):
    assert lees_tekst("Klant: Daniel Automatisering", "Motorhuis Jansen B.V.", "Totaal 10,00")["uitgevoerd_door"]         == "Motorhuis Jansen B.V."


def test_starter_pdf_opslaan(monkeypatch, tmp_path):
    import types
    import webview
    from pypdf import PdfReader
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    garage = laad_starter()
    poort = garage.vrije_poort()
    server, draad = garage.start_server(poort)
    try:
        import httpx
        r = httpx.post(f"http://127.0.0.1:{poort}/voertuigen", data={"kenteken": "AB12CD", "voertuigsoort": "Motorfiets"})
        vid = r.headers["location"].rsplit("/", 1)[1]
        doel = tmp_path / "uitdraai.pdf"
        gekozen = []
        nep_venster = types.SimpleNamespace(create_file_dialog=lambda *a, **k: gekozen.append(k) or (str(doel),))
        monkeypatch.setattr(webview, "windows", [nep_venster])
        api = garage.Api(f"http://127.0.0.1:{poort}")
        assert api.opslaan(f"/voertuigen/{vid}/export.pdf?kosten=0&kosten=1", "x.pdf") == str(doel)
        assert "AB12CD" in PdfReader(doel).pages[0].extract_text()
        assert gekozen[0]["save_filename"] == "x.pdf"
        nep_venster.create_file_dialog = lambda *a, **k: None  # geannuleerd
        assert api.opslaan(f"/voertuigen/{vid}/export.pdf", "x.pdf") is None
        try:
            api.opslaan("/facturen/1/origineel", "x.pdf")  # alleen exports
            raise AssertionError("had geweigerd moeten worden")
        except ValueError:
            pass
    finally:
        server.should_exit = True
        draad.join(timeout=5)


# ---------- kosten verbergen ook op facturen ----------

def test_export_zonder_kosten_lakt_bedragen_op_facturen(client):
    from pypdf import PdfReader
    from tests.test_app import controleer_alles
    vid = voeg_voertuig_toe(client)
    factuur = pdf("Motorgarage Snel B.V.", "Factuurdatum: 14-03-2025", "Kilometerstand: 12.345",
                  "Motorolie 10W-40 3L     54,00", "Oliefilter HF303   € 18,50", "Totaal incl. BTW  EUR 72,50")
    jpg = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(jpg, "JPEG")
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", factuur, "application/pdf")),
                                     ("facturen", ("bon.jpg", jpg.getvalue(), "image/jpeg"))])
    controleer_alles(client)

    met = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf?kosten=1").content))
    assert "54,00" in met.pages[1].extract_text()

    zonder = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf?kosten=0").content))
    tekst = zonder.pages[1].extract_text()
    for bedrag in ("54,00", "18,50", "72,50", "€", "EUR"):
        assert bedrag not in tekst, bedrag
    for blijft in ("Motorolie 10W-40", "Oliefilter HF303", "14-03-2025", "12.345", "Totaal incl. BTW"):
        assert blijft in tekst, blijft
    assert "weggelaten" in zonder.pages[2].extract_text()  # foto: bedragen niet te verbergen
    # het opgeslagen factuurbestand zelf is niet aangepast
    assert "54,00" in PdfReader(io.BytesIO(client.get(f"/facturen/{eerste_factuur_id()}").content)).pages[0].extract_text()


def eerste_factuur_id():
    with db.connect() as conn:
        return conn.execute("SELECT id FROM factuur ORDER BY id").fetchone()["id"]
