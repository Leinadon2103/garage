import io
import json
import sqlite3
from datetime import date

import httpx
from PIL import Image
from reportlab.pdfgen import canvas

from app import db, invoice_reader, planning, rdw
from tests.test_app import S, T, TODAY, add_vehicle, client, new_service, type_id  # noqa: F401 (fixture)


def pdf(*lines) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(lines):
        c.drawString(60, 790 - i * 16, line)
    c.save()
    return buf.getvalue()


def motorcycle_types():
    with db.connect() as conn:
        return db.types_for(conn, "Motorfiets")


INVOICE = pdf(
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


# ---------- reading invoices ----------

def test_read_invoice_basics(client):
    types = motorcycle_types()
    r = invoice_reader.read(INVOICE, "application/pdf", types)
    names = {t["id"]: t["name"] for t in types}
    assert r["readable"]
    assert r["date"] == "2025-03-14"  # invoice date, not the due date
    assert r["cost"] == 142.18  # total incl. VAT, not the subtotal or the VAT
    assert r["odometer_km"] == 12345
    assert r["performed_by"] == "Motorgarage Snel B.V."
    assert {names[t] for t in r["types"]} == {"Olie vervangen", "Oliefilter vervangen", "Remblokken voor vervangen"}


def test_invoice_variants(client):
    types = motorcycle_types()
    names = {t["id"]: t["name"] for t in types}
    r = invoice_reader.read(pdf("Factuur", "Datum 2 februari 2026", "Alleen oliefilter geleverd",
                                "Te betalen", "EUR 18,50", "Stand teller 9800 km"), "application/pdf", types)
    assert r["date"] == "2026-02-02" and r["cost"] == 18.5 and r["odometer_km"] == 9800
    assert [names[t] for t in r["types"]] == ["Oliefilter vervangen"]  # no false 'oil'
    r = invoice_reader.read(pdf("Bon 05/06/2026", "Kettingset DID 520 + tandwielen", "Bougie NGK x2",
                                "Bedragen: 189,00  12,50  1.234,56"), "application/pdf", types)
    assert r["cost"] == 1234.56  # no total line: highest amount
    assert {names[t] for t in r["types"]} == {"Ketting + tandwielen vervangen", "Bougies vervangen"}
    # car types cannot come from a motorcycle invoice
    r = invoice_reader.read(pdf("APK keuring en distributieriem", "Totaal 500,00"), "application/pdf", types)
    assert r["types"] == []


def test_invoice_not_readable():
    assert invoice_reader.read(b"\xff\xd8\xff...", "image/jpeg", []) == {"readable": False}
    assert invoice_reader.read(pdf(""), "application/pdf", []) == {"readable": False}  # scan without text
    assert invoice_reader.read(b"%PDF kapot", "application/pdf", []) == {"readable": False}


def test_endpoint_combines_several_invoices(client):
    vid = add_vehicle(client)
    second = pdf("Bandenservice", "Datum: 20-03-2025", "Michelin Road 6 achterband", "Totaal: 210,00")
    jpg = io.BytesIO()
    Image.new("RGB", (10, 10)).save(jpg, "JPEG")
    r = client.post(f"/vehicles/{vid}/read-invoice", files=[
        ("invoices", ("a.pdf", INVOICE, "application/pdf")), ("invoices", ("b.pdf", second, "application/pdf")),
        ("invoices", ("c.jpg", jpg.getvalue(), "image/jpeg"))]).json()
    assert r["readable"] and r["unreadable"] == 1
    assert r["cost"] == 352.18 and r["date"] == "2025-03-20" and r["odometer_km"] == 12345
    assert "Banden vervangen" in r["type_names"] and "Olie vervangen" in r["type_names"]
    with db.connect() as conn:  # stateless: nothing stored
        assert conn.execute("SELECT COUNT(*) FROM service").fetchone()[0] == 0
    error = client.post(f"/vehicles/{vid}/read-invoice", files=[("invoices", ("x.exe", b"MZ", "x"))]).json()
    assert not error["readable"] and "geen geldige" in error["error"]


def test_service_form_has_invoice_on_top(client):
    vid = add_vehicle(client)
    html = client.get(f"/vehicles/{vid}/services/new").text
    assert html.index("Begin met je factuur") < html.index("Wat is er gedaan?")
    assert f"/vehicles/{vid}/read-invoice" in html


# ---------- intervals per vehicle ----------

def test_planning_custom_interval_and_source():
    oil = T(km=6000, months=12)
    services = [S("2026-10-01", 10000, oil)]
    default = planning.overview(services, 12000, TODAY)[0]
    assert default["source"] == "rule_of_thumb" and default["next_km"] == 16000
    custom = planning.overview(services, 12000, TODAY, intervals={1: (4000, None)})[0]
    assert custom["source"] == "custom" and custom["next_km"] == 14000 and custom["next_date"] is None
    # a custom schedule can also switch a reminder off
    assert planning.overview(services, 12000, TODAY, intervals={1: (None, None)}) == []


def test_save_intervals_page(client):
    vid = add_vehicle(client)
    oil, chain = type_id("Olie vervangen"), type_id("Ketting smeren/spannen")
    html = client.get(f"/vehicles/{vid}/intervals").text
    assert "Kleppen stellen" in html and "APK" not in html and "vuistregel: 6.000" in html
    # oil changed, chain equal to the rule of thumb -> only oil is stored
    r = client.post(f"/vehicles/{vid}/intervals", data={f"km_{oil}": "7.500", f"months_{oil}": "12",
                    f"km_{chain}": "1000", f"months_{chain}": "", "schedule_source": "Instructieboekje p. 112"},
                    follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as conn:
        assert db.intervals(conn, vid) == {oil: (7500, 12)}
        assert conn.execute("SELECT schedule_source FROM vehicle").fetchone()[0] == "Instructieboekje p. 112"
    new_service(client, vid, {"odometer_km": "1000"})
    page = client.get(f"/vehicles/{vid}").text
    assert "8.500 km" in page and "volgens eigen schema" in page
    assert client.post(f"/vehicles/{vid}/intervals", data={f"km_{oil}": "abc"}).status_code == 400


# ---------- RDW and km estimate ----------

RDW_VEHICLE = [{"kenteken": "00MDB8", "voertuigsoort": "Motorfiets", "merk": "KAWASAKI", "handelsbenaming": "NINJA ZX-4R",
                "datum_eerste_toelating": "20240122", "datum_eerste_tenaamstelling_in_nederland": "20260701",
                "openstaande_terugroepactie_indicator": "Ja", "tellerstandoordeel": "Niet geregistreerd",
                "jaar_laatste_registratie_tellerstand": "2026", "catalogusprijs": "10298", "massa_rijklaar": "189",
                "eerste_kleur": "GROEN", "cilinderinhoud": "399"}]
RDW_FUEL = [{"kenteken": "00MDB8", "brandstof_omschrijving": "Benzine", "nettomaximumvermogen": "57.00"}]


def rdw_fake(request):
    return httpx.Response(200, json=RDW_FUEL if "8ys7" in str(request.url) else RDW_VEHICLE)


def test_rdw_extra_fields():
    res = rdw.lookup("00-MDB-8", httpx.Client(transport=httpx.MockTransport(rdw_fake)))
    assert res["rdw"] == {"apk_expiry_date": None, "open_recall": True, "odometer_judgement": "Niet geregistreerd",
                          "odometer_year": 2026, "list_price": 10298, "first_registration_nl_date": "2026-07-01",
                          "curb_weight_kg": 189, "fuel": "Benzine", "power_kw": 57}


def test_rdw_fuel_may_fail():
    def half(request):
        return httpx.Response(500) if "8ys7" in str(request.url) else httpx.Response(200, json=RDW_VEHICLE)
    res = rdw.lookup("00MDB8", httpx.Client(transport=httpx.MockTransport(half)))
    assert res["make"] == "Kawasaki" and "fuel" not in res["rdw"]


def test_vehicle_page_shows_rdw_info_and_refresh(client, monkeypatch):
    extra = rdw.lookup("00MDB8", httpx.Client(transport=httpx.MockTransport(rdw_fake)))["rdw"]
    r = client.post("/vehicles", follow_redirects=False, data={
        "license_plate": "00MDB8", "vehicle_type": "Motorfiets", "make": "Kawasaki", "model": "NINJA ZX-4R",
        "first_registration_date": "2024-01-22", "rdw_json": json.dumps({**extra, "evil": "<script>"})})
    vid = r.headers["location"].rsplit("/", 1)[1]
    html = client.get(f"/vehicles/{vid}").text
    for text in ("Openstaande terugroepactie", "Import: in NL sinds", "nog geen km-standen geregistreerd", "57 kW", "Benzine",
                 "ruwe schatting"):
        assert text in html, text
    with db.connect() as conn:
        assert "evil" not in conn.execute("SELECT rdw_json FROM vehicle").fetchone()[0]

    # refreshing fetches the current status (recall handled in the meantime)
    handled = {**extra, "open_recall": False}
    monkeypatch.setattr(rdw, "lookup", lambda k: {"rdw": handled})
    client.post(f"/vehicles/{vid}/rdw")
    assert "Openstaande terugroepactie" not in client.get(f"/vehicles/{vid}").text


def test_km_estimate():
    assert planning.km_estimate("Motorfiets", "2024-01-22", date(2026, 10, 20)) == 9600  # 2.75 years x 3,500
    assert planning.km_estimate("Personenauto", "2025-10-31", date(2026, 10, 20)) == 12600
    assert planning.km_estimate("Aanhangwagen", "2020-01-01") is None
    assert planning.km_estimate("Motorfiets", None) is None


def test_estimate_disappears_with_real_km(client):
    vid = add_vehicle(client)
    client.post(f"/vehicles/{vid}/services/new", data={"date": "2026-01-01", "odometer_km": "4321",
                                                       "types": [str(type_id("Olie vervangen"))]})
    assert "ruwe schatting" not in client.get(f"/vehicles/{vid}").text


# ---------- app & migration ----------

def test_health_and_manifest(client):
    assert client.get("/health").json() == {"app": "garage"}
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200 and json.loads(manifest.content)["display"] == "standalone"
    assert client.get("/static/icon-192.png").status_code == 200
    assert 'rel="manifest"' in client.get("/").text


def test_migration_new_table_and_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    old = sqlite3.connect(tmp_path / "onderhoud.db")
    old.executescript("CREATE TABLE voertuig (id INTEGER PRIMARY KEY, kenteken TEXT NOT NULL, voertuigsoort TEXT NOT NULL,"
                      " merk TEXT, model TEXT, datum_eerste_toelating TEXT, kleur TEXT, cilinderinhoud INTEGER, bijnaam TEXT);")
    old.close()
    db.init()
    with db.connect() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(vehicle)")}
        assert {"schedule_source", "rdw_json", "rdw_updated_on"} <= columns
        assert conn.execute("SELECT COUNT(*) FROM vehicle_interval").fetchone()[0] == 0


def load_starter():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("garage", Path(__file__).parent.parent / "Garage.pyw")
    garage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(garage)
    return garage


def test_starter_data_folder_port_and_single_instance(monkeypatch, tmp_path):
    garage = load_starter()
    assert garage.documents().is_dir()
    monkeypatch.delenv("GARAGE_DATA", raising=False)
    monkeypatch.delenv("ONDERHOUD_DATA", raising=False)
    assert garage.data_folder() == garage.HERE / "data"  # development: project folder
    monkeypatch.setattr(garage, "FROZEN", True)
    assert garage.data_folder() == garage.documents() / "Garage"  # installed: Documents\Garage
    monkeypatch.setenv("ONDERHOUD_DATA", str(tmp_path))  # old name still works
    assert garage.data_folder() == tmp_path
    monkeypatch.setenv("GARAGE_DATA", str(tmp_path / "new"))  # the new name wins
    assert garage.data_folder() == tmp_path / "new"
    assert 1024 < garage.free_port() < 65536
    monkeypatch.setattr(garage, "MUTEX_NAME", f"Garage-test-{tmp_path.name}")
    assert garage.already_running() is False  # first
    second = load_starter()
    monkeypatch.setattr(second, "MUTEX_NAME", f"Garage-test-{tmp_path.name}")
    assert second.already_running() is True  # the second sees the first


def test_starter_starts_and_stops_server(monkeypatch, tmp_path):
    import httpx
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    garage = load_starter()
    port = garage.free_port()
    server, thread = garage.start_server(port)
    assert httpx.get(f"http://127.0.0.1:{port}/health").json() == {"app": "garage"}
    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_error_page_has_app_layout(client):
    r = client.get("/vehicles/999")
    assert r.status_code == 404 and "Dat lukte niet" in r.text and "Naar de garage" in r.text
    assert client.get("/vehicles/999", headers={"accept": "application/json"}).json()["detail"]


def test_import_label_on_full_date(client):
    def make(first_registration, nl):
        r = client.post("/vehicles", follow_redirects=False, data={
            "license_plate": "AB12CD", "vehicle_type": "Personenauto", "first_registration_date": first_registration,
            "rdw_json": json.dumps({"first_registration_nl_date": nl})})
        return client.get(r.headers["location"]).text
    assert "Import: in NL sinds" not in make("2015-12-28", "2016-01-02")  # turn of the year, no import
    assert "Import: in NL sinds" in make("2024-01-22", "2026-07-01")


# ---------- regressions in reading invoices (review) ----------

def read_text(*lines):
    return invoice_reader.read(pdf(*lines), "application/pdf", motorcycle_types())


def names(r):
    every = {t["id"]: t["name"] for t in motorcycle_types()}
    return {every[t] for t in r["types"]}


def test_cost_misfires(client):
    assert read_text("Omschrijving Aantal Prijs Totaal", "Oliefilter 1 50,00 50,00", "Bougie 1 28,65 28,65",
                     "Totaal incl. BTW 78,65")["cost"] == 78.65
    assert read_text("Totaal arbeid 2,50 uur", "Totaal BTW 21,00", "Totaal excl. BTW 100,00",
                     "Totaal incl. BTW 121,00")["cost"] == 121.0
    assert read_text("Totaal incl. BTW 121,00", "Gelieve binnen 14 dagen te betalen.",
                     "Bij te late betaling € 15,00 administratiekosten")["cost"] == 121.0


def test_date_misfires(client):
    assert read_text("Datum eerste toelating 01-03-2015", "Datum: 15-10-2026", "Olie")["date"] == "2026-10-15"
    assert read_text("Factuurdatum", "12-09-2026", "APK geldig tot 15-10-2027")["date"] == "2026-09-12"


def test_km_misfires(client):
    assert read_text("Volgende onderhoud bij 45.000 km", "12 maanden garantie", "Km-stand 31.250")["odometer_km"] == 31250
    assert read_text("Haal en breng binnen 15 km: 25,00", "Kilometerstand: 24.512")["odometer_km"] == 24512
    assert read_text("Art.nr. HKM 123456", "Oliefilter")["odometer_km"] is None
    assert read_text("Kilometer stand: 24.512")["odometer_km"] == 24512
    assert read_text("Km.stand 24512")["odometer_km"] == 24512


def test_type_misfires(client):
    assert names(read_text("Ruitenwisservloeistof bijgevuld", "Oliepeil gecontroleerd", "Volgende APK: 01-01-2027")) == set()
    assert names(read_text("Remblokken vóór vervangen")) == {"Remblokken voor vervangen"}
    assert names(read_text("Uitlijnen voor; remblokken achter")) == {"Remblokken achter vervangen"}
    assert names(read_text("Voorvorkolie vervangen", "Versnellingsbakolie")) == set()
    assert names(read_text("Olie filter HF204")) == {"Oliefilter vervangen"}
    assert names(read_text("Achterband 180/55 gemonteerd")) == {"Banden vervangen"}
    assert names(read_text("Distributieband", "Tandband")) == set()


def test_company_not_customer(client):
    assert read_text("Klant: Daniel Automatisering", "Motorhuis Jansen B.V.", "Totaal 10,00")["performed_by"] \
        == "Motorhuis Jansen B.V."


def test_starter_save_pdf(monkeypatch, tmp_path):
    import types
    import webview
    from pypdf import PdfReader
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    garage = load_starter()
    port = garage.free_port()
    server, thread = garage.start_server(port)
    try:
        import httpx
        from app.main import SESSION_COOKIE, SESSION_TOKEN
        r = httpx.post(f"http://127.0.0.1:{port}/vehicles", data={"license_plate": "AB12CD", "vehicle_type": "Motorfiets"},
                       cookies={SESSION_COOKIE: SESSION_TOKEN})
        vid = r.headers["location"].rsplit("/", 1)[1]
        target = tmp_path / "printout.pdf"
        chosen = []
        fake_window = types.SimpleNamespace(create_file_dialog=lambda *a, **k: chosen.append(k) or (str(target),))
        monkeypatch.setattr(webview, "windows", [fake_window])
        api = garage.Api(f"http://127.0.0.1:{port}", session=f"{SESSION_COOKIE}={SESSION_TOKEN}")
        assert api.save(f"/vehicles/{vid}/export.pdf?costs=0&costs=1", "x.pdf") == str(target)
        assert "AB12CD" in PdfReader(target).pages[0].extract_text()
        assert chosen[0]["save_filename"] == "x.pdf"
        fake_window.create_file_dialog = lambda *a, **k: None  # cancelled
        assert api.save(f"/vehicles/{vid}/export.pdf", "x.pdf") is None
        try:
            api.save("/invoices/1/original", "x.pdf")  # only exports
            raise AssertionError("should have been refused")
        except ValueError:
            pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# ---------- hiding costs on invoices too ----------

def test_export_without_costs_redacts_amounts_on_invoices(client):
    from pypdf import PdfReader
    from tests.test_app import review_all
    vid = add_vehicle(client)
    invoice = pdf("Motorgarage Snel B.V.", "Factuurdatum: 14-03-2025", "Kilometerstand: 12.345",
                  "Motorolie 10W-40 3L     54,00", "Oliefilter HF303   € 18,50", "Totaal incl. BTW  EUR 72,50")
    jpg = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(jpg, "JPEG")
    new_service(client, vid, files=[("invoices", ("f.pdf", invoice, "application/pdf")),
                                    ("invoices", ("bon.jpg", jpg.getvalue(), "image/jpeg"))])
    review_all(client)

    with_costs = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf?costs=1").content))
    assert "54,00" in with_costs.pages[1].extract_text()

    without = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf?costs=0").content))
    text = without.pages[1].extract_text()
    for amount in ("54,00", "18,50", "72,50", "€", "EUR"):
        assert amount not in text, amount
    for stays in ("Motorolie 10W-40", "Oliefilter HF303", "14-03-2025", "12.345", "Totaal incl. BTW"):
        assert stays in text, stays
    assert "weggelaten" in without.pages[2].extract_text()  # photo: amounts cannot be hidden
    # the stored invoice file itself has not been changed
    assert "54,00" in PdfReader(io.BytesIO(client.get(f"/invoices/{first_invoice_id()}").content)).pages[0].extract_text()


def first_invoice_id():
    with db.connect() as conn:
        return conn.execute("SELECT id FROM invoice ORDER BY id").fetchone()["id"]
