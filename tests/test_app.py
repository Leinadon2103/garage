import io
import zipfile
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app import db, planning, rdw
from app.main import app, start_url

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
       b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")


def backup_db(zip_path) -> bytes:
    """The database in a backup, decrypted (format 2: encrypted with the master key of this data folder)."""
    from app import backup, crypto
    with zipfile.ZipFile(zip_path) as z:
        return crypto.decrypt(db.master_key(), z.read(db.DB_NAME), backup.BACKUP_DB_CONTEXT)


def pdf_bytes(pages=1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(100, 750, f"Factuur pagina {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import backup
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    # never put backups in the developer's real OneDrive/user folder
    monkeypatch.setattr(backup, "default_folder", lambda: tmp_path / "backups")
    with TestClient(app) as c:
        # db.DATA_DIR is global: the startup backup must not run on into the next test's folder
        app.state.backup_thread.join()
        c.get(start_url("http://testserver"))  # the session cookie (S4), like the window gets it
        yield c


def type_id(name):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM maintenance_type WHERE name = ?", (name,)).fetchone()["id"]


def review_all(client):
    """Marks all invoices as reviewed (without boxes), so they are included in the export."""
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM invoice WHERE reviewed = 0")]
    for iid in ids:
        assert client.post(f"/invoices/{iid}/redact", data={"redactions": "[]"}, follow_redirects=False).status_code == 303


def add_vehicle(client, vehicle_type="Motorfiets", plate="MD-12-AB"):
    r = client.post("/vehicles", data={"license_plate": plate, "vehicle_type": vehicle_type, "make": "Honda",
                                       "model": "CB500F"}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


# ---------- planning ----------

T = lambda km=None, months=None, id_=1: {"id": id_, "name": "Olie", "interval_km": km, "interval_months": months}
S = lambda day, km, *types: {"date": day, "odometer_km": km, "types": list(types)}
TODAY = date(2026, 10, 20)


def test_planning_km_green_orange_red():
    service = [S("2026-10-01", 10000, T(km=6000))]
    assert planning.overview(service, 12000, TODAY)[0]["color"] == "green"
    assert planning.overview(service, 15500, TODAY)[0]["color"] == "orange"
    assert planning.overview(service, 16000, TODAY)[0]["color"] == "red"
    assert planning.overview(service, 12000, TODAY)[0]["next_km"] == 16000


def test_planning_time():
    t = T(months=12)
    assert planning.overview([S("2026-01-01", None, t)], None, TODAY)[0]["color"] == "green"
    assert planning.overview([S("2025-11-10", None, t)], None, TODAY)[0]["color"] == "orange"
    red = planning.overview([S("2025-10-01", None, t)], None, TODAY)[0]
    assert red["color"] == "red" and red["next_date"] == date(2026, 10, 1)


def test_planning_whichever_comes_first_counts():
    # km still plenty, but time expired -> red
    assert planning.overview([S("2025-01-01", 10000, T(km=6000, months=12))], 11000, TODAY)[0]["color"] == "red"


def test_planning_only_last_service_and_no_interval_ignored():
    oil, other = T(km=6000), {"id": 2, "name": "Overig", "interval_km": None, "interval_months": None}
    services = [S("2026-10-01", 20000, oil, other), S("2025-01-01", 5000, oil)]  # newest first
    res = planning.overview(services, 20500, TODAY)
    assert len(res) == 1 and res[0]["next_km"] == 26000
    assert planning.overview([], 1000, TODAY) == []


def test_add_months_end_of_month():
    assert planning.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert planning.add_months(date(2026, 11, 15), 14) == date(2028, 1, 15)


# ---------- RDW ----------

def rdw_client(json_data, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=json_data)))


def test_rdw_mapping_and_normalization():
    data = [{"kenteken": "MD12AB", "voertuigsoort": "Motorfiets", "merk": "HONDA", "handelsbenaming": "CB500F",
             "datum_eerste_toelating": "20190315", "eerste_kleur": "ROOD", "cilinderinhoud": "471"}]
    res = rdw.lookup("md-12-ab", rdw_client(data))
    res.pop("rdw")  # extra fields: see tests/test_features.py
    assert res == {"license_plate": "MD12AB", "vehicle_type": "Motorfiets", "make": "Honda", "model": "CB500F",
                   "first_registration_date": "2019-03-15", "color": "Rood", "displacement_cc": 471}


def test_rdw_not_found_and_unreachable():
    with pytest.raises(rdw.RdwError, match="niet gevonden"):
        rdw.lookup("XX-99-XX", rdw_client([]))
    with pytest.raises(rdw.RdwError, match="niet bereikbaar"):
        rdw.lookup("XX-99-XX", rdw_client({}, status=500))
    with pytest.raises(rdw.RdwError, match="Vul een kenteken"):
        rdw.lookup(" - ")


# ---------- web + export ----------

def test_dropdown_differs_per_vehicle_type(client):
    bike, car = add_vehicle(client, "Motorfiets"), add_vehicle(client, "Personenauto", "AB-123-C")
    b, c = client.get(f"/vehicles/{bike}/services/new").text, client.get(f"/vehicles/{car}/services/new").text
    assert "Kleppen stellen" in b and "APK" not in b
    assert "APK" in c and "Kleppen stellen" not in c
    assert "Olie vervangen" in b and "Olie vervangen" in c


def test_service_with_invoices_dashboard_and_export(client):
    vid = add_vehicle(client)
    r = client.post(f"/vehicles/{vid}/services/new", follow_redirects=False,
                    data={"date": "2026-04-01", "odometer_km": "12000", "performed_by": "Zelf",
                          "cost": "45,50", "types": [str(type_id("Olie vervangen")), str(type_id("Oliefilter vervangen"))]},
                    files=[("invoices", ("bon.pdf", pdf_bytes(2), "application/pdf")),
                           ("invoices", ("foto.png", PNG, "image/png"))])
    assert r.status_code == 303

    page = client.get(f"/vehicles/{vid}").text
    assert "Olie vervangen" in page and "18.000 km" in page and "€ 45,50" in page
    assert "bon.pdf" in page and "foto.png" in page

    review_all(client)
    with_costs = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf?costs=0&costs=1").content))
    text = with_costs.pages[0].extract_text()
    assert len(with_costs.pages) == 1 + 2 + 1  # overview + 2 pages of PDF invoice + 1 image
    assert "MD12AB" in text and "Kosten" in text and "45,50" in text
    assert "Bijlage 1" in with_costs.pages[1].extract_text() and "Factuur pagina 1" in with_costs.pages[1].extract_text()
    assert "Bijlage 2" in with_costs.pages[3].extract_text()

    without = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf?costs=0").content))
    assert "45,50" not in without.pages[0].extract_text()


def test_wrong_upload_refused(client):
    vid = add_vehicle(client)
    r = client.post(f"/vehicles/{vid}/services/new",
                    data={"date": "2026-04-01", "types": [str(type_id("Olie vervangen"))]},
                    files=[("invoices", ("virus.exe", b"MZ...", "application/octet-stream"))])
    assert r.status_code == 400 and "geen geldige" in r.text
    # fake PDF (right extension, wrong content)
    r = client.post(f"/vehicles/{vid}/services/new",
                    data={"date": "2026-04-01", "types": [str(type_id("Olie vervangen"))]},
                    files=[("invoices", ("nep.pdf", b"geen pdf", "application/pdf"))])
    assert r.status_code == 400
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM service").fetchone()[0] == 0


def test_service_without_type_refused(client):
    vid = add_vehicle(client)
    r = client.post(f"/vehicles/{vid}/services/new", data={"date": "2026-04-01"})
    assert r.status_code == 400 and "minimaal één" in r.text


def test_edit_and_delete_clean_up_files(client):
    vid = add_vehicle(client)
    oil = str(type_id("Olie vervangen"))
    client.post(f"/vehicles/{vid}/services/new", data={"date": "2026-04-01", "types": [oil]},
                files=[("invoices", ("bon.pdf", pdf_bytes(), "application/pdf"))])
    with db.connect() as conn:
        sid = conn.execute("SELECT id FROM service").fetchone()["id"]
    client.post(f"/services/{sid}/edit", data={"date": "2026-05-01", "odometer_km": "500", "types": [oil]})
    assert "500 km" in client.get(f"/vehicles/{vid}").text
    assert client.get(f"/services/{sid}/edit").status_code == 200

    files = list((db.DATA_DIR / "facturen").rglob("*.pdf"))
    assert len(files) == 1
    client.post(f"/vehicles/{vid}/delete")
    assert not files[0].exists()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM invoice").fetchone()[0] == 0


def test_change_maintenance_type(client):
    oil = type_id("Olie vervangen")
    client.post("/maintenance-types", data={"id": str(oil), "name": "Olie vervangen",
                                            "vehicle_types": ["Motorfiets"], "interval_km": "8000"})
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM maintenance_type WHERE id = ?", (oil,)).fetchone()
    assert (t["vehicle_types"], t["interval_km"], t["interval_months"]) == ("Motorfiets", 8000, None)
    assert client.get("/maintenance-types").status_code == 200


# ---------- regressions from the code review ----------

def new_service(client, vid, data=None, files=None):
    base = {"date": "2026-04-01", "types": [str(type_id("Olie vervangen"))]}
    return client.post(f"/vehicles/{vid}/services/new", data={**base, **(data or {})}, files=files,
                       follow_redirects=False)


def test_dutch_amount_notation_and_clean_error(client):
    vid = add_vehicle(client)
    assert new_service(client, vid, {"cost": "€ 1.250,00", "odometer_km": "12.000"}).status_code == 303
    with db.connect() as conn:
        s = conn.execute("SELECT cost, odometer_km FROM service").fetchone()
    assert (s["cost"], s["odometer_km"]) == (1250.0, 12000)
    r = new_service(client, vid, {"cost": "veel", "note": "blijft staan"})
    assert r.status_code == 400 and "geen geldig bedrag" in r.text and "blijft staan" in r.text


def test_invalid_date_and_type_ids(client):
    vid = add_vehicle(client)
    assert new_service(client, vid, {"date": "20240101"}).status_code == 400
    assert new_service(client, vid, {"types": ["abc", "99999"]}).status_code == 400


def test_encrypted_pdf_refused(client):
    from pypdf import PdfWriter
    w = PdfWriter(clone_from=io.BytesIO(pdf_bytes()))
    w.encrypt("geheim")
    buf = io.BytesIO()
    w.write(buf)
    r = new_service(client, add_vehicle(client), files=[("invoices", ("slot.pdf", buf.getvalue(), "application/pdf"))])
    assert r.status_code == 400 and "wachtwoord" in r.text


def test_export_with_odd_type_name_and_missing_file(client):
    client.post("/maintenance-types", data={"name": "Remmen <voor> & achter", "vehicle_types": ["*"]})
    vid = add_vehicle(client)
    new_service(client, vid, {"types": [str(type_id("Remmen <voor> & achter"))]},
                files=[("invoices", ("bon.pdf", pdf_bytes(), "application/pdf"))])
    review_all(client)
    for f in (db.DATA_DIR / "facturen").rglob("*.pdf"):
        f.unlink()
    r = client.get(f"/vehicles/{vid}/export.pdf")
    assert r.status_code == 200
    pdf = PdfReader(io.BytesIO(r.content))
    assert "Remmen <voor> & achter" in pdf.pages[0].extract_text()
    assert "ontbreekt" in pdf.pages[1].extract_text()
    with db.connect() as conn:
        iid = conn.execute("SELECT id FROM invoice").fetchone()["id"]
    assert client.get(f"/invoices/{iid}").status_code == 404


def test_rotated_invoice_header_on_top(client):
    from pypdf import PdfWriter
    w = PdfWriter(clone_from=io.BytesIO(pdf_bytes()))
    w.pages[0].rotate(90)
    buf = io.BytesIO()
    w.write(buf)
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("scan.pdf", buf.getvalue(), "application/pdf"))])
    review_all(client)
    page = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content)).pages[1]
    assert page.rotation == 0
    # landscape page: wider than high, with the header strip added on top
    assert float(page.cropbox.width) > float(page.cropbox.height)
    assert "Bijlage 1" in page.extract_text()


def test_edit_keeps_type_that_no_longer_fits_the_vehicle_type(client):
    vid = add_vehicle(client)
    chain = type_id("Ketting smeren/spannen")
    new_service(client, vid, {"types": [str(chain)]})
    client.post("/maintenance-types", data={"id": str(chain), "name": "Ketting smeren/spannen",
                                            "vehicle_types": ["Personenauto"], "interval_km": "1000"})
    with db.connect() as conn:
        sid = conn.execute("SELECT id FROM service").fetchone()["id"]
    assert "Ketting smeren/spannen" in client.get(f"/services/{sid}/edit").text
