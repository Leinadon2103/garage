"""F3: inspections with defects and recalls from RDW Open Data, on the vehicle page and in the sale PDF."""
import html
import io
import json
import re

import httpx
from pypdf import PdfReader

from app import db, rdw
from tests.test_app import add_vehicle, client  # noqa: F401 (fixture)
from tests.test_features import RDW_FUEL, RDW_VEHICLE

DEFECTS = [  # two inspections; on 2025-03-10 also a code that must never end up in a query
    {"kenteken": "00MDB8", "meld_datum_door_keuringsinstantie": "20230315", "gebrek_identificatie": "205",
     "soort_erkenning_omschrijving": "APK Lichte voertuigen", "aantal_gebreken_geconstateerd": "1"},
    {"kenteken": "00MDB8", "meld_datum_door_keuringsinstantie": "20250310", "gebrek_identificatie": "205",
     "soort_erkenning_omschrijving": "APK Lichte voertuigen", "aantal_gebreken_geconstateerd": "2"},
    {"kenteken": "00MDB8", "meld_datum_door_keuringsinstantie": "20250310", "gebrek_identificatie": "AC1",
     "soort_erkenning_omschrijving": "APK Lichte voertuigen", "aantal_gebreken_geconstateerd": "1"},
    {"kenteken": "00MDB8", "meld_datum_door_keuringsinstantie": "20250310", "gebrek_identificatie": "x') or (1=1",
     "soort_erkenning_omschrijving": "APK Lichte voertuigen", "aantal_gebreken_geconstateerd": "1"},
]
DEFECT_TYPES = [  # code 205 got a new description in 2024
    {"gebrek_identificatie": "205", "ingangsdatum_gebrek": "20170401", "einddatum_gebrek": "20240101",
     "gebrek_omschrijving": "Band oude omschrijving"},
    {"gebrek_identificatie": "205", "ingangsdatum_gebrek": "20240101", "gebrek_omschrijving": "Band beschadigd"},
    {"gebrek_identificatie": "AC1", "ingangsdatum_gebrek": "20170401", "gebrek_omschrijving": "Uitlaat lekt"},
]
RECALL_STATUS = [
    {"kenteken": "00MDB8", "referentiecode_rdw": "MGP230001", "code_status": "P", "status": "Producent heeft herstel gemeld"},
    {"kenteken": "00MDB8", "referentiecode_rdw": "MGP250002", "code_status": "O", "status": "Openstaande terugroepactie"},
]
RECALLS = [
    {"referentiecode_rdw": "MGP230001", "publicatiedatum_rdw": "20230601", "meldende_producent_distributeur": "Kawasaki",
     "omschrijving_defect": "Remleiding kan schuren", "beschrijving_van_het_herstel": "Leiding vervangen"},
    {"referentiecode_rdw": "MGP250002", "publicatiedatum_rdw": "20250901", "meldende_producent_distributeur": "Kawasaki",
     "omschrijving_defect": "Kettingspanner <kan> breken", "materi_le_gevolgen": "Ketting kan eraf lopen",
     "beschrijving_van_het_herstel": "Spanner vervangen"},
]
DATASETS = {"8ys7": RDW_FUEL, "a34c": DEFECTS, "hx2c": DEFECT_TYPES, "t49b": RECALL_STATUS, "j9yg": RECALLS}


def fake(requests=None, failing=()):
    def handler(request):
        if requests is not None:
            requests.append(request)
        key = next((k for k in DATASETS if k in str(request.url)), None)
        if key in failing:
            return httpx.Response(500)
        return httpx.Response(200, json=DATASETS[key] if key else RDW_VEHICLE)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_inspections_grouped_with_the_description_valid_on_that_date():
    requests = []
    res = rdw.lookup("00-MDB-8", fake(requests))["rdw"]
    assert res["inspections"] == [
        {"date": "2025-03-10", "kind": "APK Lichte voertuigen", "defects": [
            {"code": "205", "description": "Band beschadigd", "count": 2},
            {"code": "AC1", "description": "Uitlaat lekt", "count": 1}]},
        {"date": "2023-03-15", "kind": "APK Lichte voertuigen", "defects": [
            {"code": "205", "description": "Band oude omschrijving", "count": 1}]},
    ]
    where = next(r.url.params["$where"] for r in requests if "hx2c" in str(r.url))
    assert where == "gebrek_identificatie in ('205','AC1')"  # the odd code stays out of the query


def test_recalls_open_first_with_details():
    res = rdw.lookup("00MDB8", fake())["rdw"]
    assert [(r["ref"], r["open"]) for r in res["recalls"]] == [("MGP250002", True), ("MGP230001", False)]
    assert res["recalls"][0] == {
        "ref": "MGP250002", "open": True, "status": "Openstaande terugroepactie", "date": "2025-09-01",
        "manufacturer": "Kawasaki", "description": "Kettingspanner <kan> breken", "consequence": "Ketting kan eraf lopen",
        "remedy": "Spanner vervangen"}


def test_a_failing_part_is_left_out_and_the_lookup_still_works():
    res = rdw.lookup("00MDB8", fake(failing={"hx2c"}))
    assert res["make"] == "Kawasaki" and "inspections" not in res["rdw"] and len(res["rdw"]["recalls"]) == 2
    res = rdw.lookup("00MDB8", fake(failing={"j9yg"}))["rdw"]
    assert "recalls" not in res and len(res["inspections"]) == 2


def test_clean_history_keeps_only_the_known_shape():
    long = "x" * 5000
    data = {"inspections": [{"date": "2025-03-10", "kind": "APK", "evil": 1,
                             "defects": [{"code": "205", "description": long, "count": 10 ** 6}, "nonsense"]},
                            {"date": "gisteren", "kind": "APK", "defects": []}, "nonsense"],
            "recalls": [{"ref": "MGP1", "open": "ja", "date": "2025-13-01", "description": ["a"], "remedy": long}] * 80}
    clean = rdw.clean_history(data)
    assert clean["inspections"] == [{"date": "2025-03-10", "kind": "APK",
                                     "defects": [{"code": "205", "description": "x" * 300, "count": 1}]}]
    assert len(clean["recalls"]) == rdw.MAX_ITEMS
    assert clean["recalls"][0] == {"ref": "MGP1", "open": False, "status": "", "date": None, "manufacturer": "",
                                   "description": "", "consequence": "", "remedy": "x" * 1500}
    assert rdw.clean_history({"inspections": "nee", "recalls": None}) == {}


LOOKUP = rdw.lookup


def use_rdw(monkeypatch, **kw):
    """The app's own RDW calls (lookup, refresh) go to the fake datasets."""
    monkeypatch.setattr(rdw, "lookup", lambda plate: LOOKUP(plate, fake(**kw)))


def save_with_history(client, monkeypatch):
    use_rdw(monkeypatch)
    found = client.post("/vehicles/lookup", data={"license_plate": "00-MDB-8"}).text
    # the history travels along in a hidden form field and comes back intact
    hidden = html.unescape(re.search(r'name="rdw_json" value=\'([^\']*)\'', found)[1])
    assert json.loads(hidden) == LOOKUP("00MDB8", fake())["rdw"]
    r = client.post("/vehicles", follow_redirects=False, data={
        "license_plate": "00MDB8", "vehicle_type": "Motorfiets", "make": "Kawasaki", "rdw_json": hidden})
    return r.headers["location"].rsplit("/", 1)[1]


def test_vehicle_page_and_sale_pdf_show_history(client, monkeypatch):
    vid = save_with_history(client, monkeypatch)
    page = client.get(f"/vehicles/{vid}").text
    for text in ("Keuringen en terugroepacties", "Band beschadigd (2x)", "Uitlaat lekt", "Kettingspanner &lt;kan&gt; breken",
                 "Ketting kan eraf lopen", "Hersteld", 'href="#rdw-history"'):
        assert text in page, text

    pages = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content)).pages
    text = " ".join(" ".join(page.extract_text() for page in pages).split())  # table cells wrap
    for part in ("Keuringen en terugroepacties", "alleen keuringen waarbij gebreken", "Band beschadigd (2x)",
                 "Kettingspanner <kan> breken", "Herstel: Spanner vervangen", "Producent heeft herstel gemeld"):
        assert part in text, part


def test_without_history_the_page_asks_to_refresh_and_pdf_has_no_section(client):
    vid = add_vehicle(client)
    assert "Vernieuw om ook keuringen" in client.get(f"/vehicles/{vid}").text
    text = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content)).pages[0].extract_text()
    assert "terugroepacties" not in text


def test_refresh_keeps_a_part_the_rdw_did_not_deliver(client, monkeypatch):
    vid = save_with_history(client, monkeypatch)
    use_rdw(monkeypatch, failing={"a34c"})
    client.post(f"/vehicles/{vid}/rdw")
    with db.connect() as conn:
        stored = json.loads(conn.execute("SELECT rdw_json FROM vehicle").fetchone()[0])
    assert len(stored["inspections"]) == 2 and len(stored["recalls"]) == 2
