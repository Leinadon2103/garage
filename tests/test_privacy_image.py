import io
import json
import sqlite3

import httpx
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app import db, image, planning, redaction
from app.web import format_plate
from tests.test_app import S, T, TODAY, add_vehicle, client, new_service, pdf_bytes, review_all  # noqa: F401 (fixture)


def pdf_with_address() -> bytes:
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


def first_invoice():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM invoice ORDER BY id").fetchone()


# ---------- redacting ----------

def test_upload_goes_to_redaction_screen_with_suggestions(client):
    vid = add_vehicle(client)
    r = new_service(client, vid, files=[("invoices", ("f.pdf", pdf_with_address(), "application/pdf"))])
    assert r.headers["location"].endswith("/redact")
    r = client.get(r.headers["location"])  # service -> first unreviewed invoice
    assert r.status_code == 200 and "Persoonsgegevens weglakken" in r.text
    for reason in ("postcode", "e-mail", "adres"):
        assert f'"reason": "{reason}"' in r.text
    iid = first_invoice()["id"]
    png = client.get(f"/invoices/{iid}/page/0.png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert client.get(f"/invoices/{iid}/page/5.png").status_code == 404
    assert "nog niet gecontroleerd" in client.get(f"/vehicles/{vid}").text


def test_redaction_really_removes_text_and_keeps_original(client):
    client.post("/settings/private", data={"terms": "Jan de Vries\n"})
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_with_address(), "application/pdf"))])
    f = first_invoice()
    with db.connect() as conn:
        terms = db.private_terms(conn)
    sugg = redaction.suggestions(db.DATA_DIR / f["path"], f["mime_type"], terms)
    own = [s for s in sugg if s["reason"] == "jouw gegevens"]
    assert own and all(s["active"] for s in own)
    assert not any(s["active"] for s in sugg if s["reason"] != "jouw gegevens")  # patterns off with own terms

    r = client.post(f"/invoices/{f['id']}/redact", data={"redactions": json.dumps(sugg)}, follow_redirects=False)
    assert r.status_code == 303
    f2 = first_invoice()
    assert f2["reviewed"] == 1 and f2["original_path"] == f["path"] and f2["path"] != f["path"]
    redacted = PdfReader(io.BytesIO(client.get(f"/invoices/{f['id']}").content)).pages[0].extract_text()
    for secret in ("Jan de Vries", "1234 AB", "jan@example.nl", "Dorpsstraat 12"):
        assert secret not in redacted
    assert "Olie verversen" in redacted  # the rest stays
    original = PdfReader(io.BytesIO(client.get(f"/invoices/{f['id']}/original").content)).pages[0].extract_text()
    assert "Jan de Vries" in original
    export = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content))
    assert "Jan de Vries" not in export.pages[1].extract_text()  # the export uses the redacted version

    # redacting again replaces the old redacted version (no orphaned files)
    client.post(f"/invoices/{f['id']}/redact", data={"redactions": "[]"})
    assert len(list((db.DATA_DIR / "facturen").rglob("*-gelakt*"))) == 1
    # deleting removes the original and the redacted version
    client.post(f"/invoices/{f['id']}/delete")
    assert not list((db.DATA_DIR / "facturen").rglob("*.pdf"))


def test_redacting_image_black_and_without_exif(client):
    im = Image.new("RGB", (200, 100), "white")
    exif = Image.Exif()
    exif[0x010F] = "Telefoonmerk"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("foto.jpg", buf.getvalue(), "image/jpeg"))])
    iid = first_invoice()["id"]
    client.post(f"/invoices/{iid}/redact", data={"redactions": '[{"page":0,"x0":0,"y0":0,"x1":0.5,"y1":1}]'})
    redacted = Image.open(io.BytesIO(client.get(f"/invoices/{iid}").content))
    assert redacted.getpixel((10, 50))[0] < 20 and redacted.getpixel((190, 50))[0] > 230
    assert not redacted.getexif()


def test_rotated_pdf_redacted_in_the_right_place(client):
    from pypdf import PdfWriter
    w = PdfWriter(clone_from=io.BytesIO(pdf_with_address()))
    w.pages[0].rotate(90)
    buf = io.BytesIO()
    w.write(buf)
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("scan.pdf", buf.getvalue(), "application/pdf"))])
    f = first_invoice()
    sugg = [s for s in redaction.suggestions(db.DATA_DIR / f["path"], f["mime_type"], []) if s["reason"] == "postcode"]
    client.post(f"/invoices/{f['id']}/redact", data={"redactions": json.dumps(sugg)})
    text = PdfReader(io.BytesIO(client.get(f"/invoices/{f['id']}").content)).pages[0].extract_text()
    assert "1234 AB" not in text and "Olie verversen" in text


def test_invalid_boxes(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    assert client.post(f"/invoices/{first_invoice()['id']}/redact", data={"redactions": "kapot"}).status_code == 400


def test_settings_page(client):
    assert client.get("/maintenance-types").status_code == 200  # redirect to /settings
    client.post("/settings/private", data={"terms": "  Jan  \n\nab\nDorpsstraat 12"})
    with db.connect() as conn:
        assert db.private_terms(conn) == ["Jan", "Dorpsstraat 12"]  # terms that are too short are ignored


# ---------- vehicle image ----------

def test_choose_and_save_image(client, monkeypatch):
    jpg = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(jpg, "JPEG")

    def fake_get(url, **_):
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

    monkeypatch.setattr(image.httpx, "get", fake_get)
    vid = add_vehicle(client)
    page = client.get(f"/vehicles/{vid}/image").text
    assert "Ninja" in page and "media.sketchfab.com/t.jpg" in page and "thumb.wikimedia.org/n.jpg" in page

    client.post(f"/vehicles/{vid}/image", data={"kind": "3d", "uid": "a" * 32,
                                                "thumb": "https://media.sketchfab.com/t.jpg", "title": "Ninja"})
    page = client.get(f"/vehicles/{vid}").text
    # privacy: by default only connect to Sketchfab after a click, unless you switch that on yourself
    assert f"sketchfab.com/models/{'a' * 32}/embed" in page and 'id="load-3d"' in page and "<iframe" not in page
    client.post("/privacy", data={"sketchfab_autoload": "1"})
    page = client.get(f"/vehicles/{vid}").text
    assert "<iframe" in page and 'id="load-3d"' not in page
    assert client.get(f"/vehicles/{vid}/image.jpg").status_code == 200
    assert "image.jpg" in client.get("/").text

    client.post(f"/vehicles/{vid}/image", data={"kind": "photo", "thumb": "https://thumb.wikimedia.org/n.jpg", "title": "Ninja",
                                                "source": "https://commons.wikimedia.org/wiki/File:Ninja.jpg"})
    assert "Wikimedia Commons" not in client.get(f"/vehicles/{vid}").text  # no source credit on top of the photo
    r = client.post(f"/vehicles/{vid}/image", data={"kind": "photo", "thumb": "https://evil.example/x.jpg"})
    assert r.status_code == 502  # only known image sources

    client.post(f"/vehicles/{vid}/image/upload", files={"photo": ("mijn.jpg", jpg.getvalue(), "image/jpeg")})
    assert len(list((db.DATA_DIR / "beelden").glob("*.jpg"))) == 1  # old image cleaned up
    assert "sketchfab" not in client.get(f"/vehicles/{vid}").text
    assert client.post(f"/vehicles/{vid}/image/upload",
                       files={"photo": ("x.jpg", b"geen foto", "image/jpeg")}).status_code == 400


def test_image_without_internet(client, monkeypatch):
    def broken(*_, **__):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(image.httpx, "get", broken)
    vid = add_vehicle(client)
    r = client.get(f"/vehicles/{vid}/image")
    assert r.status_code == 200 and "geen internet" in r.text


# ---------- other ----------

def test_new_photo_gets_new_url(client):
    """Otherwise the browser (WebView2) still shows the old one from its cache after changing the photo."""
    import re
    vid = add_vehicle(client)
    urls = []
    for color in ("red", "blue"):
        jpg = io.BytesIO()
        Image.new("RGB", (40, 30), color).save(jpg, "JPEG")
        client.post(f"/vehicles/{vid}/image/upload", files={"photo": ("f.jpg", jpg.getvalue(), "image/jpeg")})
        urls.append(re.search(rf'src="(/vehicles/{vid}/image\.jpg\?v=\w+)"', client.get(f"/vehicles/{vid}").text)[1])
    assert urls[0] != urls[1]
    assert urls[1] in client.get("/").text
    r = client.get(urls[1])
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.getpixel((20, 15))[2] > 200  # the blue one, not the red one


def test_image_one_source_fails_other_stays_visible(client, monkeypatch):
    def fake_get(url, **_):
        if "wikimedia" in url:  # e.g. Wikimedia refusing a User-Agent
            return httpx.Response(403, request=httpx.Request("GET", url))
        data = {"results": [{"uid": "b" * 32, "name": "Bandit 1200",
                             "thumbnails": {"images": [{"url": "https://media.sketchfab.com/t.jpg", "width": 640}]}}]}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(image.httpx, "get", fake_get)
    vid = add_vehicle(client)
    page = client.get(f"/vehicles/{vid}/image").text
    assert "Bandit 1200" in page and "weigert het verzoek (fout 403)" in page and "geen internet" not in page
    assert image.search_terms("SUZUKI", "GSF 600 BANDIT") == ["SUZUKI GSF 600 BANDIT", "SUZUKI GSF", "SUZUKI BANDIT"]


def test_plate_format_and_planning_text():
    assert format_plate("00MDB8") == "00-MDB-8"
    assert format_plate("JHK73J") == "JHK-73-J"
    assert format_plate("12ABCD") == "12-AB-CD"
    s = planning.overview([S("2026-10-01", 10000, T(km=6000))], 15100, TODAY)[0]
    assert s["text"] == "nog 900 km" and 0.8 < s["progress"] < 0.9
    s = planning.overview([S("2025-08-01", None, T(months=12))], None, TODAY)[0]
    assert s["text"] == "3 maanden te laat"


def test_migration_of_existing_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    old = sqlite3.connect(tmp_path / "onderhoud.db")
    old.executescript(
        "CREATE TABLE voertuig (id INTEGER PRIMARY KEY, kenteken TEXT NOT NULL, voertuigsoort TEXT NOT NULL,"
        " merk TEXT, model TEXT, datum_eerste_toelating TEXT, kleur TEXT, cilinderinhoud INTEGER, bijnaam TEXT);"
        "CREATE TABLE factuur (id INTEGER PRIMARY KEY, beurt_id INTEGER NOT NULL, pad TEXT NOT NULL,"
        " originele_naam TEXT NOT NULL, mime_type TEXT NOT NULL);"
        "INSERT INTO voertuig (kenteken, voertuigsoort) VALUES ('AB12CD', 'Motorfiets');")
    old.close()
    db.init()
    with db.connect() as conn:
        v = conn.execute("SELECT * FROM vehicle").fetchone()
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(invoice)")}
    assert v["license_plate"] == "AB12CD" and v["image_type"] is None
    assert {"original_path", "reviewed"} <= columns


# ---------- regressions from the second review ----------

def test_export_leaves_out_unreviewed_invoice(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_with_address(), "application/pdf"))])
    export = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content))
    assert len(export.pages) == 2
    assert "Jan de Vries" not in export.pages[1].extract_text()
    assert "nog niet gecontroleerd" in export.pages[1].extract_text()
    assert "niet meegenomen" in client.get(f"/vehicles/{vid}").text


def test_redacting_again_builds_on_earlier_boxes(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_with_address(), "application/pdf"))])
    f = first_invoice()
    name = [s for s in redaction.suggestions(db.DATA_DIR / f["path"], f["mime_type"], ["Jan de Vries"])
            if s["reason"] == "jouw gegevens"]
    client.post(f"/invoices/{f['id']}/redact", data={"redactions": json.dumps(name)})
    page = client.get(f"/invoices/{f['id']}/redact").text
    assert "eerder gelakt" in page  # the earlier box is on again in the redaction screen
    # if the browser sends those boxes back (as the page does), the name stays away
    earlier = json.loads(first_invoice()["redactions"])
    client.post(f"/invoices/{f['id']}/redact", data={"redactions": json.dumps(earlier)})
    text = PdfReader(io.BytesIO(client.get(f"/invoices/{f['id']}").content)).pages[0].extract_text()
    assert "Jan de Vries" not in text


def test_annotations_and_text_outside_cropbox_disappear(client):
    import pymupdf
    doc = pymupdf.open(stream=pdf_with_address(), filetype="pdf")
    page = doc[0]
    note = page.add_text_annot((300, 300), "Geheim-notitie Jansen")
    note.set_info(title="Jansen-auteur")
    page.insert_text((72, 20), "Verborgen 0612345678")  # ends up outside the cropbox
    page.set_cropbox(pymupdf.Rect(0, 40, page.rect.width, page.rect.height))
    doc.set_metadata({"author": "Jan de Vries"})
    buf = doc.tobytes()
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", buf, "application/pdf"))])
    review_all(client)
    redacted = client.get(f"/invoices/{first_invoice()['id']}").content
    d = pymupdf.open(stream=redacted, filetype="pdf")
    assert not list(d[0].annots())
    all_text = "".join(p.get_text() for p in d)
    for secret in ("Geheim-notitie", "Jansen-auteur", "Verborgen"):
        assert secret not in all_text
    assert "Jan de Vries" not in str(d.metadata)
    assert b"Jansen" not in redacted and b"0612345678" not in redacted


def test_jpeg_comment_disappears(client):
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, "JPEG", comment=b"Jansen-comment")
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.jpg", buf.getvalue(), "image/jpeg"))])
    review_all(client)
    assert b"Jansen-comment" not in client.get(f"/invoices/{first_invoice()['id']}").content
    client.post(f"/vehicles/{vid}/image/upload", files={"photo": ("m.jpg", buf.getvalue(), "image/jpeg")})
    assert b"Jansen-comment" not in client.get(f"/vehicles/{vid}/image.jpg").content


def test_image_for_unknown_vehicle_leaves_no_file(client):
    buf = io.BytesIO()
    Image.new("RGB", (5, 5)).save(buf, "JPEG")
    assert client.post("/vehicles/999/image/upload", files={"photo": ("m.jpg", buf.getvalue(), "image/jpeg")}).status_code == 404
    assert not list((db.DATA_DIR / "beelden").glob("*")) if (db.DATA_DIR / "beelden").exists() else True
