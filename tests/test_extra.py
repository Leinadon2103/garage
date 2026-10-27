"""Tests for backup, odometer readings, OCR, reminders, costs, documents, specs, storage and theme."""
import io
import json
import os
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from app import backup, db, extra, invoice_reader, ocr, planning, redaction
from tests.test_app import add_vehicle, client, new_service, pdf_bytes, review_all, type_id  # noqa: F401

OCR = pytest.mark.skipif(not ocr.available(), reason="Windows OCR not available")


def first(table):
    with db.connect() as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchone()


def fake_clock(start=datetime(2026, 1, 1)):
    """datetime replacement for backup: every now() is one minute later (distinct backup names)."""
    counter = iter(range(1000))
    return type("D", (), {"now": staticmethod(lambda: start + timedelta(minutes=next(counter))),
                          "fromisoformat": datetime.fromisoformat, "fromtimestamp": datetime.fromtimestamp})


# ---------- 1. backup ----------

def test_make_backup_list_and_retention(client, monkeypatch, tmp_path):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    assert client.post("/backup/now", follow_redirects=False).headers["location"].endswith("backup=made#backup")
    zips = list((tmp_path / "backups").glob("Garage-backup-*.zip"))
    assert len(zips) == 1 and not list((tmp_path / "backups").glob("*.deel"))
    with zipfile.ZipFile(zips[0]) as z:
        names = z.namelist()
    assert "onderhoud.db" in names and any(n.startswith("facturen/") for n in names) and "garage-backup.json" in names

    monkeypatch.setattr(backup, "datetime", fake_clock())
    for _ in range(backup.KEEP + 3):
        backup.create()
    assert len(backup.list_backups(tmp_path / "backups")) == backup.KEEP  # old ones are cleaned up


real_copy_db = backup._copy_db


def corrupt_copy(source, target):
    """_copy_db replacement for create(): the copy in the zip gets a broken second page."""
    real_copy_db(source, target)
    with open(target, "r+b") as f:
        page_size = int.from_bytes(f.read(18)[16:18])
        f.seek(65536 if page_size == 1 else page_size)  # 1 in the header means 65536
        f.write(b"\xff" * 64)


@pytest.mark.parametrize("fault", ["database", "crc"])
def test_backup_is_checked_before_it_counts(client, monkeypatch, tmp_path, fault):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    if fault == "database":
        monkeypatch.setattr(backup, "_copy_db", corrupt_copy)
    else:  # testzip() returns the first file with a wrong checksum
        monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "facturen/f.pdf")
    with pytest.raises(backup.BackupError, match="controle"):
        backup.create()
    assert not list((tmp_path / "backups").iterdir())  # no zip that looks good, no .deel left
    with db.connect() as conn:
        assert not db.setting(conn, "backup_last")


def test_restore_brings_back_data_and_keeps_current_state(client, tmp_path):
    vid = add_vehicle(client, plate="AA-11-BB")
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    made = backup.create()
    client.post(f"/vehicles/{vid}/delete")  # 'accident'
    add_vehicle(client, plate="ZZ-99-ZZ")
    r = client.post("/backup/restore", data={"name": made.name}, follow_redirects=False)
    assert r.status_code == 303 and "restored=" in r.headers["location"]
    with db.connect() as conn:
        assert [v["license_plate"] for v in conn.execute("SELECT license_plate FROM vehicle")] == ["AA11BB"]
        f = conn.execute("SELECT * FROM invoice").fetchone()
    assert (db.DATA_DIR / f["path"]).is_file()  # invoices back too
    # the state from before the restore is ready as a safety backup
    safety = r.headers["location"].split("restored=")[1]
    with zipfile.ZipFile(tmp_path / "backups" / safety) as z, z.open("onderhoud.db") as db_file:
        assert b"ZZ99ZZ" in db_file.read()


@pytest.mark.parametrize("content", [
    {"../buiten.txt": b"x", "onderhoud.db": b"x"},     # path outside the data folder
    {"facturen/a.pdf": b"x"},                          # no database
    {"onderhoud.db": b"geen sqlite"},                  # broken database
    {"onderhoud.db": b"x", "C:/Windows/x.dll": b"x"},  # absolute path
])
def test_restore_refuses_suspicious_zip(client, tmp_path, content):
    add_vehicle(client)
    zp = tmp_path / "suspicious.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for name, data in content.items():
            z.writestr(name, data)
    with pytest.raises(backup.BackupError):
        backup.restore(zp)
    assert first("vehicle") is not None  # nothing broken
    r = client.post("/backup/restore-file", files={"file": ("v.zip", zp.read_bytes(), "application/zip")})
    assert r.status_code == 400
    assert client.post("/backup/restore", data={"name": "../../etc.zip"}).status_code == 404


def test_restore_refuses_empty_sqlite_database(client, tmp_path):
    """Migration 1 would create the tables in any SQLite file; a backup must contain a Garage database."""
    import sqlite3
    sqlite3.connect(tmp_path / "onderhoud.db").execute("CREATE TABLE x (y)").connection.close()
    with zipfile.ZipFile(tmp_path / "empty.zip", "w") as z:
        z.write(tmp_path / "onderhoud.db", "onderhoud.db")
    with pytest.raises(backup.BackupError, match="ongeldige gegevens"):
        backup.restore(tmp_path / "empty.zip")


def test_backup_folder_not_in_data_folder_and_automatic(client, tmp_path):
    assert client.post("/backup/settings", data={"folder": str(db.DATA_DIR / "sub"), "auto": "1"}).status_code == 400
    assert client.post("/backup/settings", data={"folder": "relative"}).status_code == 400
    add_vehicle(client)
    backup.automatic()  # never made -> make it now
    assert len(backup.list_backups(tmp_path / "backups")) == 1
    backup.automatic()  # just made -> not again
    assert len(backup.list_backups(tmp_path / "backups")) == 1
    with db.connect() as conn:
        db.set_setting(conn, "backup_last", (datetime.now() - timedelta(days=8)).isoformat())
        db.set_setting(conn, "backup_folder", str(db.DATA_DIR))  # invalid -> record the error, do not crash
    backup.automatic()
    with db.connect() as conn:
        assert db.setting(conn, "backup_error")
    assert "automatische back-up is mislukt" in client.get("/").text


# ---------- 2. odometer readings ----------

def test_add_reading_and_checks(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2026-01-10", "odometer_km": "5000"})
    assert client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "7.250"}, follow_redirects=False).status_code == 303
    with db.connect() as conn:
        assert db.current_km(conn, vid) == 7250
    assert "7.250" in client.get(f"/vehicles/{vid}").text
    r = client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "4000"})  # lower than before
    assert r.status_code == 400 and "lager dan" in r.text
    r = client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "9000", "date": "2026-01-01"})  # higher than later
    assert r.status_code == 400 and "hoger dan" in r.text
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "8000", "date": future}).status_code == 400
    # a service with a reading that is too low is refused too; editing the same service is allowed
    assert new_service(client, vid, {"date": date.today().isoformat(), "odometer_km": "6000"}).status_code == 400
    sid = first("service")["id"]
    r = client.post(f"/services/{sid}/edit", data={"date": "2026-01-10", "odometer_km": "5100",
                                                   "types": [str(type_id("Olie vervangen"))]}, follow_redirects=False)
    assert r.status_code == 303
    rid = first("odometer_reading")["id"]
    client.post(f"/odometer/{rid}/delete")
    with db.connect() as conn:
        assert db.current_km(conn, vid) == 5100


def test_reading_counts_in_planning():
    oil = {"id": 1, "name": "Olie", "interval_km": 6000, "interval_months": None}
    plan = planning.overview([{"date": "2026-10-01", "odometer_km": 10000, "types": [oil]}], 15500, date(2026, 10, 20))
    assert plan[0]["color"] == "orange" and plan[0]["text"] == "nog 500 km"


# ---------- 3. OCR (photos and scans) ----------

def invoice_image(*lines, width=1200) -> Image.Image:
    im = Image.new("RGB", (width, 90 + 70 * len(lines)), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype("arial.ttf", 34)
    for i, t in enumerate(lines):
        d.text((60, 40 + i * 70), t, fill="black", font=font)
    return im


INVOICE_LINES = ("Motorgarage Snel B.V.", "Factuurdatum: 14-03-2025", "Aan: Jan de Vries", "Dorpsstraat 12",
                 "1234 AB Utrecht", "Kilometerstand: 31.250", "Motorolie 10W-40   54,00", "Oliefilter HF303   18,50",
                 "Totaal incl. BTW  EUR 72,50")


@OCR
def test_ocr_read_invoice_photo(client):
    buf = io.BytesIO()
    invoice_image(*INVOICE_LINES).save(buf, "JPEG")
    with db.connect() as conn:
        types = db.types_for(conn, "Motorfiets")
    r = invoice_reader.read(buf.getvalue(), "image/jpeg", types)
    names = {t["id"]: t["name"] for t in types}
    assert r["readable"] and r["via_ocr"]
    assert (r["date"], r["cost"], r["odometer_km"]) == ("2025-03-14", 72.5, 31250)
    assert {names[t] for t in r["types"]} == {"Olie vervangen", "Oliefilter vervangen"}


@OCR
def test_ocr_suggestions_and_amounts_on_photo_and_scan(tmp_path):
    photo = tmp_path / "f.png"
    invoice_image(*INVOICE_LINES).save(photo)
    sugg = redaction.suggestions(photo, "image/png", ["Jan de Vries"])
    reasons = {s["reason"] for s in sugg}
    assert {"jouw gegevens", "postcode", "adres"} <= reasons

    # redacting amounts on a photo: afterwards OCR reads no amounts any more, the rest it does
    content, mime = redaction.redact_amounts(photo, "image/png")
    text = " ".join(w["text"] for w in ocr.recognize(Image.open(io.BytesIO(content))))
    assert mime == "image/png" and "54,00" not in text and "72,50" not in text and "Oliefilter" in text

    # scanned PDF (only an image, no text layer)
    import pymupdf
    scan = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(photo))
    doc.save(scan)
    assert {"postcode"} <= {s["reason"] for s in redaction.suggestions(scan, "application/pdf", [])}
    content, mime = redaction.redact_amounts(scan, "application/pdf")
    redacted = pymupdf.open(stream=content, filetype="pdf")
    text = " ".join(w["text"] for w in ocr.page(redacted[0]))
    assert mime == "application/pdf" and "54,00" not in text and "Oliefilter" in text


def test_without_ocr_everything_falls_back_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: False)
    photo = tmp_path / "f.png"
    invoice_image("Totaal 10,00").save(photo)
    assert redaction.suggestions(photo, "image/png", []) == []
    assert redaction.redact_amounts(photo, "image/png") is None
    assert invoice_reader.read(photo.read_bytes(), "image/png", []) == {"readable": False}


# ---------- 4. reminders ----------

def test_reminders_content_and_at_most_once_a_day(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2024-01-01", "odometer_km": "1000"})  # oil: well overdue
    lines = extra.check_notifications()
    assert any("olie vervangen" in t for _, t in lines)
    assert extra.may_notify(lines) is True
    extra.notified(lines)
    assert extra.may_notify(lines) is False  # not again on the same day
    client.post("/reminders", data={})  # switch off
    with db.connect() as conn:
        db.set_setting(conn, "last_notification", "{}")
    assert extra.may_notify(lines) is False
    # vehicle in storage: no reminder
    client.post("/reminders", data={"on": "1"})
    client.post(f"/vehicles/{vid}/storage/start", data={"date": date.today().isoformat()})
    assert not any("olie" in t for _, t in extra.check_notifications())


def test_expiry_dates_apk_and_documents_in_planning(client):
    vid = add_vehicle(client, vehicle_type="Personenauto", plate="JHK-73-J")
    in_20_days = (date.today() + timedelta(days=20)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE vehicle SET rdw_json = ? WHERE id = ?", (json.dumps({"apk_expiry_date": in_20_days}), vid))
    client.post(f"/vehicles/{vid}/documents", data={"kind": "Verzekeringspolis", "title": "Polis",
                                                    "expiry_date": (date.today() - timedelta(days=3)).isoformat()},
                files={"file": ("p.pdf", pdf_bytes(), "application/pdf")})
    html = client.get(f"/vehicles/{vid}").text
    assert "APK verloopt" in html and "Polis verloopt" in html and "3 dagen verlopen" in html
    s = planning.expiry_status("x", in_20_days, "rdw")
    assert s["color"] == "orange" and s["text"] == "nog 20 dagen"


# ---------- 5. costs ----------

def test_cost_overview(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2025-03-01", "odometer_km": "1000", "cost": "100,00"})
    new_service(client, vid, {"date": "2026-03-01", "odometer_km": "2000", "cost": "50,00",
                              "types": [str(type_id("Olie vervangen")), str(type_id("Oliefilter vervangen"))]})
    html = client.get(f"/vehicles/{vid}/costs").text
    assert "€ 150,00" in html and "€ 0,15" in html  # 150 / 1000 km
    assert html.count('class="bar-group"') == 2 and "Beurten met meerdere klussen" in html
    assert 'data-tip="2025: € 100,00"' in html and 'class="line"' in html


# ---------- 6. documents ----------

def test_add_open_delete_documents(client):
    vid = add_vehicle(client)
    r = client.post(f"/vehicles/{vid}/documents", data={"kind": "Kentekenbewijs / kentekencard"},
                    files={"file": ("kenteken.pdf", pdf_bytes(), "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    d = first("document")
    assert client.get(f"/documents/{d['id']}").status_code == 200
    assert client.post(f"/vehicles/{vid}/documents", data={"kind": "Onzin"},
                       files={"file": ("x.pdf", pdf_bytes(), "application/pdf")}).status_code == 400
    assert client.post(f"/vehicles/{vid}/documents", data={"kind": "Overig"},
                       files={"file": ("x.exe", b"MZ", "application/x")}).status_code == 400
    # documents never go into the sale PDF
    pdf = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content))
    assert len(pdf.pages) == 1
    client.post(f"/documents/{d['id']}/delete")
    assert not (db.DATA_DIR / d["path"]).exists()


def test_deleting_vehicle_cleans_up_documents(client):
    vid = add_vehicle(client)
    client.post(f"/vehicles/{vid}/documents", data={"kind": "Overig"},
                files={"file": ("x.pdf", pdf_bytes(), "application/pdf")})
    path = db.DATA_DIR / first("document")["path"]
    client.post(f"/vehicles/{vid}/delete")
    assert not path.exists() and first("document") is None


# ---------- 7. specs ----------

def test_save_specs_and_in_export(client):
    vid = add_vehicle(client)
    html = client.get(f"/vehicles/{vid}/specs").text
    assert "Bandenspanning voor" in html and "Ketting" in html
    client.post(f"/vehicles/{vid}/specs", data={"key": ["Motorolie", "Ketting", "Eigen ding", ""],
                                                "value": ["10W-40", "", "abc <b>", ""]})
    with db.connect() as conn:
        specs = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM spec")}
    assert specs == {"Motorolie": "10W-40", "Eigen ding": "abc <b>"}  # empty rows not stored
    text = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content)).pages[0].extract_text()
    assert "Specificaties" in text and "10W-40" in text and "abc <b>" in text


# ---------- 8. cover page of the sale PDF ----------

def test_cover_summary_km_and_footer(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2025-03-01", "odometer_km": "1000"},
                files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "2500"})
    text = PdfReader(io.BytesIO(client.get(f"/vehicles/{vid}/export.pdf").content)).pages[0].extract_text()
    for part in ("1 onderhoudsbeurt vastgelegd", "Bij 1 van de 1 beurten", "Kilometerstanden", "genoteerd door eigenaar",
                 "2.500", "Pagina 1", "gemaakt met Garage"):
        assert part in text, part


# ---------- 9. storage ----------

def test_storage_start_and_end(client):
    vid = add_vehicle(client)
    assert "In de stalling zetten" in client.get(f"/vehicles/{vid}/storage").text
    client.post(f"/vehicles/{vid}/storage/start", data={"date": "2025-11-01", "odometer_km": "8000"})
    assert client.post(f"/vehicles/{vid}/storage/start", data={"date": "2025-11-02"}).status_code == 400
    assert "In de stalling sinds" in client.get(f"/vehicles/{vid}").text and "In stalling" in client.get("/").text
    assert client.post(f"/vehicles/{vid}/storage/end", data={"date": "2025-10-01"}).status_code == 400
    assert client.post(f"/vehicles/{vid}/storage/end", data={"date": "2026-03-15", "odometer_km": "7000"}).status_code == 400
    client.post(f"/vehicles/{vid}/storage/end", data={"date": "2026-03-15", "odometer_km": "8010"})
    with db.connect() as conn:
        assert [r["source"] for r in db.odometer_history(conn, vid)] == ["storage_start", "storage_end"]
        assert db.active_storage(conn, vid) is None


# ---------- theme (light / dark) ----------

def test_choose_theme(client):
    assert 'data-theme="system"' in client.get("/").text
    assert client.post("/theme", data={"theme": "dark"}).json() == {"theme": "dark"}
    assert 'data-theme="dark"' in client.get("/").text
    assert client.post("/theme", data={"theme": "zon"}).status_code == 400  # setting that no longer exists
    with db.connect() as conn:  # whoever still had 'zon' (sun) stored gets 'like Windows'
        db.set_setting(conn, "theme", "zon")
    assert 'data-theme="system"' in client.get("/").text
    assert client.post("/theme", data={"theme": "purple"}).status_code == 400


def test_title_bar_colors_equal_to_css():
    from tests.test_features import load_starter
    garage = load_starter()
    css = (Path(__file__).parent.parent / "app" / "static" / "app.css").read_text(encoding="utf-8")
    light = css[:css.index(":root[data-dark]")]
    dark = css[css.index(":root[data-dark]"):]
    for block, (bar, text, line) in ((light, garage.BAR_LIGHT), (dark, garage.BAR_DARK)):
        assert re.search(r"--bar: (#\w+)", block).group(1) == bar
        assert re.search(r"--text: (#\w+)", block).group(1) == text
        assert re.search(r"--line: (#\w+)", block).group(1) == line
    assert garage.mix(garage.BAR_LIGHT, garage.BAR_DARK, 0) == garage.BAR_LIGHT
    assert garage.mix(garage.BAR_LIGHT, garage.BAR_DARK, 1) == garage.BAR_DARK
    assert garage.starts_dark("dark") is True and garage.starts_dark("light") is False


def test_starter_check_without_data_does_nothing(monkeypatch, tmp_path):
    from tests.test_features import load_starter
    garage = load_starter()
    monkeypatch.setenv("GARAGE_DATA", str(tmp_path / "empty"))
    monkeypatch.setattr(garage, "show_notification", lambda *a: (_ for _ in ()).throw(AssertionError("no notification")))
    garage.check()  # no database: quietly done


# ---------- regressions from the three reviews ----------

def test_other_websites_cannot_change_or_read_anything(client):
    vid = add_vehicle(client)
    # CSRF: form from another site
    r = client.post("/theme", data={"theme": "dark"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post(f"/vehicles/{vid}/delete", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and first("vehicle") is not None
    # DNS rebinding: wrong Host, also for reading
    assert client.get("/", headers={"host": "evil.example:8000"}).status_code == 403
    # own pages are allowed
    assert client.post("/theme", data={"theme": "light"}, headers={"origin": "http://testserver",
                                                                   "sec-fetch-site": "same-origin"}).status_code == 200
    # 'Origin: null' comes from e.g. a sandboxed iframe of another site
    assert client.post("/theme", data={"theme": "dark"}, headers={"origin": "null"}).status_code == 403


def test_request_too_large_refused(client):
    vid = add_vehicle(client)
    large = {"content-length": str(200 * 1024 ** 2 + 1)}
    assert client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "1"}, headers=large).status_code == 413
    assert client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "1"}, headers={"content-length": "x"}).status_code == 400
    assert first("odometer_reading") is None


def test_file_paths_outside_the_data_folder_refused(client, tmp_path):
    for bad in ("C:/Windows/win.ini", "../geheim.txt", "facturen/../../x", "\\\\server\\share\\x", "", None, "data/x"):
        with pytest.raises(ValueError):
            db.data_file(bad)
    assert db.data_file("facturen/1/abc.pdf") == (db.DATA_DIR / "facturen/1/abc.pdf").resolve()
    # a tampered database in a backup is not restored
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    with db.connect() as conn:
        conn.execute("UPDATE invoice SET path = 'C:/Windows/win.ini'")
    evil = backup.create()
    with db.connect() as conn:
        conn.execute("UPDATE invoice SET path = 'facturen/1/x.pdf'")
    with pytest.raises(backup.BackupError, match="ongeldige gegevens"):
        backup.restore(evil)


def test_mime_type_from_extension_and_backup_with_html_refused(client):
    """An 'invoice' called text/html in the database must never run as a web page inside Garage."""
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    client.post(f"/vehicles/{vid}/documents", data={"kind": "Overig"},
                files={"file": ("d.pdf", pdf_bytes(), "application/pdf")})
    with db.connect() as conn:
        conn.execute("UPDATE invoice SET mime_type = 'text/html'")
        conn.execute("UPDATE document SET mime_type = 'text/html'")
    f, d = first("invoice"), first("document")
    for url in (f"/invoices/{f['id']}", f"/invoices/{f['id']}/original", f"/documents/{d['id']}"):
        assert client.get(url).headers["content-type"] == "application/pdf"
    evil = backup.create()
    with pytest.raises(backup.BackupError, match="ongeldige gegevens"):
        backup.restore(evil)


def test_security_headers(client):
    vid = add_vehicle(client)
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    page = client.get(f"/vehicles/{vid}")
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp and "object-src 'none'" in csp
    pdf = client.get(f"/invoices/{first('invoice')['id']}")
    assert "content-security-policy" not in pdf.headers  # do not hinder the PDF viewer
    for r in (page, pdf, client.get("/", headers={"host": "evil.example"})):
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_no_network_folder_and_backup_folder_not_from_backup(client, tmp_path):
    for network in ("\\\\aanvaller\\share", "//aanvaller/share"):
        r = client.post("/backup/settings", data={"folder": network})
        assert r.status_code == 400 and "netwerkmap" in r.text
    add_vehicle(client)
    other = tmp_path / "other-folder"
    with db.connect() as conn:
        db.set_setting(conn, "backup_folder", str(other))
    zp = backup.create()
    with db.connect() as conn:
        db.set_setting(conn, "backup_folder", str(tmp_path / "backups"))
    backup.restore(zp)
    with db.connect() as conn:  # the folder of this pc stays, even though the backup says otherwise
        assert db.setting(conn, "backup_folder") == str(tmp_path / "backups")


def test_restore_oldest_backup_and_safety_copy_separate(client, monkeypatch, tmp_path):
    add_vehicle(client)
    monkeypatch.setattr(backup, "datetime", fake_clock())
    for _ in range(backup.KEEP):
        backup.create()
    oldest = backup.list_backups(tmp_path / "backups")[-1]["path"]
    backup.restore(oldest)  # used to fail: the safety copy cleaned up the source
    assert oldest.exists() and len(backup.list_backups(tmp_path / "backups")) == backup.KEEP
    assert len(backup.list_backups(tmp_path / "backups", backup.SAFETY_PREFIX)) == 1


def test_restore_with_open_file_changes_nothing(client, monkeypatch):
    vid = add_vehicle(client, plate="AA-11-BB")
    new_service(client, vid, files=[("invoices", ("f.pdf", pdf_bytes(), "application/pdf"))])
    zp = backup.create()
    add_vehicle(client, plate="ZZ-99-ZZ")
    real_replace = os.replace

    def locked(a, b):  # like Windows does when an invoice is open in a PDF reader
        if str(a).endswith("facturen"):
            raise PermissionError(32, "in use")
        return real_replace(a, b)
    monkeypatch.setattr(backup.os, "replace", locked)
    with pytest.raises(backup.BackupError, match="open"):
        backup.restore(zp)
    with db.connect() as conn:  # nothing changed: both vehicles still there, invoices intact
        assert conn.execute("SELECT COUNT(*) FROM vehicle").fetchone()[0] == 2
        assert db.data_file(conn.execute("SELECT path FROM invoice").fetchone()[0]).is_file()


def test_correct_storage_and_date_checks(client):
    vid = add_vehicle(client)
    client.post(f"/vehicles/{vid}/storage/start", data={"date": "2025-11-01", "odometer_km": "150000"})  # typo
    r = client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "15500"})
    assert r.status_code == 400 and "bij het stallen" in r.text
    client.post(f"/storage/{first('storage_period')['id']}/delete")
    assert client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "15500"}, follow_redirects=False).status_code == 303
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.post(f"/vehicles/{vid}/storage/start", data={"date": tomorrow}).status_code == 400
    client.post(f"/vehicles/{vid}/storage/start", data={"date": "2025-11-01"})
    client.post(f"/vehicles/{vid}/storage/end", data={"date": "2026-03-01"})
    assert client.post(f"/vehicles/{vid}/storage/start", data={"date": "2026-01-01"}).status_code == 400  # overlap


def test_reminder_not_every_day_because_of_counting_down_text(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2024-01-01", "odometer_km": "1000"})
    lines = extra.check_notifications()
    extra.notified(lines)
    with db.connect() as conn:  # 'tomorrow', same situation but a different text (9 instead of 10 days left)
        m = json.loads(db.setting(conn, "last_notification"))
        m["date"] = "2000-01-01"
        db.set_setting(conn, "last_notification", json.dumps(m))
    other_text = [(k, t + " (other countdown)") for k, t in lines]
    assert extra.may_notify(other_text) is False
    assert extra.may_notify(lines + [("new|x|red", "something new")]) is True


def test_amount_variants_and_half_scan(tmp_path, monkeypatch):
    for w in ("1250,00", "€1250,00", "72,5", "€72", "€1.250,00", "54,00", "EUR"):
        assert redaction.AMOUNT_WORD.fullmatch(w), w
    for w in ("2026", "14-03-2025", "12.345", "HF303", "10W-40", "21%"):
        assert not redaction.AMOUNT_WORD.fullmatch(w), w
    # PDF with a text page and a page on which nothing can be recognized: not safe -> leave out
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Totaal 50,00")
    doc.new_page()  # empty (e.g. a scan on which OCR fails)
    path = tmp_path / "half.pdf"
    doc.save(path)
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert redaction.redact_amounts(path, "application/pdf") is None


def test_invalid_dates_break_no_page(client):
    vid = add_vehicle(client)
    with db.connect() as conn:
        conn.execute("UPDATE vehicle SET rdw_json = ? WHERE id = ?", (json.dumps({"apk_expiry_date": "morgen"}), vid))
    assert client.get("/").status_code == 200 and client.get(f"/vehicles/{vid}").status_code == 200
    assert extra.check_notifications() is not None
