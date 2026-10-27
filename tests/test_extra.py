"""Tests voor back-up, km-registraties, OCR, herinneringen, kosten, documenten, specificaties, stalling en weergave."""
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

from app import backup, db, extra, factuur_lezen, lakken, ocr, planning
from tests.test_app import client, controleer_alles, nieuwe_beurt, pdf_bytes, type_id, voeg_voertuig_toe  # noqa: F401

OCR = pytest.mark.skipif(not ocr.beschikbaar(), reason="Windows-OCR niet beschikbaar")


def eerste(tabel):
    with db.connect() as conn:
        return conn.execute(f"SELECT * FROM {tabel} ORDER BY id").fetchone()


# ---------- 1. back-up ----------

def test_backup_maken_lijst_en_bewaartermijn(client, monkeypatch, tmp_path):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    assert client.post("/backup/nu", follow_redirects=False).headers["location"].endswith("backup=gemaakt#backup")
    zips = list((tmp_path / "backups").glob("Garage-backup-*.zip"))
    assert len(zips) == 1 and not list((tmp_path / "backups").glob("*.deel"))
    with zipfile.ZipFile(zips[0]) as z:
        namen = z.namelist()
    assert "onderhoud.db" in namen and any(n.startswith("facturen/") for n in namen) and "garage-backup.json" in namen

    teller = iter(range(100))
    monkeypatch.setattr(backup, "datetime", type("D", (), {"now": staticmethod(
        lambda: datetime(2026, 1, 1) + timedelta(minutes=next(teller))), "fromisoformat": datetime.fromisoformat, "fromtimestamp": datetime.fromtimestamp}))
    for _ in range(backup.BEWAAR + 3):
        backup.maak()
    assert len(backup.lijst(tmp_path / "backups")) == backup.BEWAAR  # oude worden opgeruimd


def test_backup_terugzetten_herstelt_en_bewaart_huidige_stand(client, tmp_path):
    vid = voeg_voertuig_toe(client, kenteken="AA-11-BB")
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    gemaakt = backup.maak()
    client.post(f"/voertuigen/{vid}/verwijderen")  # 'ongeluk'
    voeg_voertuig_toe(client, kenteken="ZZ-99-ZZ")
    r = client.post("/backup/terugzetten", data={"naam": gemaakt.name}, follow_redirects=False)
    assert r.status_code == 303 and "hersteld=" in r.headers["location"]
    with db.connect() as conn:
        assert [v["kenteken"] for v in conn.execute("SELECT kenteken FROM voertuig")] == ["AA11BB"]
        f = conn.execute("SELECT * FROM factuur").fetchone()
    assert (db.DATA_DIR / f["pad"]).is_file()  # facturen ook terug
    # de stand van vóór het terugzetten staat als veiligheids-back-up klaar
    veiligheid = r.headers["location"].split("hersteld=")[1]
    with zipfile.ZipFile(tmp_path / "backups" / veiligheid) as z, z.open("onderhoud.db") as dbbestand:
        assert b"ZZ99ZZ" in dbbestand.read()


@pytest.mark.parametrize("inhoud", [
    {"../buiten.txt": b"x", "onderhoud.db": b"x"},     # pad buiten de gegevensmap
    {"facturen/a.pdf": b"x"},                          # geen database
    {"onderhoud.db": b"geen sqlite"},                  # kapotte database
    {"onderhoud.db": b"x", "C:/Windows/x.dll": b"x"},  # absoluut pad
])
def test_backup_terugzetten_weigert_verdachte_zip(client, tmp_path, inhoud):
    voeg_voertuig_toe(client)
    zp = tmp_path / "verdacht.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for naam, data in inhoud.items():
            z.writestr(naam, data)
    with pytest.raises(backup.BackupFout):
        backup.herstel(zp)
    assert eerste("voertuig") is not None  # niets kapotgemaakt
    r = client.post("/backup/terugzetten-bestand", files={"bestand": ("v.zip", zp.read_bytes(), "application/zip")})
    assert r.status_code == 400
    assert client.post("/backup/terugzetten", data={"naam": "../../etc.zip"}).status_code == 404


def test_backup_map_niet_in_gegevensmap_en_automatisch(client, tmp_path):
    assert client.post("/backup/instellingen", data={"map": str(db.DATA_DIR / "sub"), "auto": "1"}).status_code == 400
    assert client.post("/backup/instellingen", data={"map": "relatief"}).status_code == 400
    voeg_voertuig_toe(client)
    backup.automatisch()  # nog nooit gemaakt -> nu maken
    assert len(backup.lijst(tmp_path / "backups")) == 1
    backup.automatisch()  # net gemaakt -> niet opnieuw
    assert len(backup.lijst(tmp_path / "backups")) == 1
    with db.connect() as conn:
        db.zet_instelling(conn, "backup_laatste", (datetime.now() - timedelta(days=8)).isoformat())
        db.zet_instelling(conn, "backup_map", str(db.DATA_DIR))  # ongeldig -> fout vastleggen, niet crashen
    backup.automatisch()
    with db.connect() as conn:
        assert db.instelling(conn, "backup_fout")
    assert "automatische back-up is mislukt" in client.get("/").text


# ---------- 2. km-stand noteren ----------

def test_km_noteren_en_controles(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, {"datum": "2026-01-10", "kilometerstand": "5000"})
    assert client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "7.250"}, follow_redirects=False).status_code == 303
    with db.connect() as conn:
        assert db.huidige_km(conn, vid) == 7250
    assert "7.250" in client.get(f"/voertuigen/{vid}").text
    r = client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "4000"})  # lager dan eerder
    assert r.status_code == 400 and "lager dan" in r.text
    r = client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "9000", "datum": "2026-01-01"})  # hoger dan later
    assert r.status_code == 400 and "hoger dan" in r.text
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "8000", "datum": future}).status_code == 400
    # een beurt met een te lage km-stand wordt ook geweigerd; dezelfde beurt bewerken mag wel
    assert nieuwe_beurt(client, vid, {"datum": date.today().isoformat(), "kilometerstand": "6000"}).status_code == 400
    bid = eerste("beurt")["id"]
    r = client.post(f"/beurt/{bid}/bewerken", data={"datum": "2026-01-10", "kilometerstand": "5100",
                                                    "types": [str(type_id("Olie vervangen"))]}, follow_redirects=False)
    assert r.status_code == 303
    kid = eerste("km_stand")["id"]
    client.post(f"/km/{kid}/verwijderen")
    with db.connect() as conn:
        assert db.huidige_km(conn, vid) == 5100


def test_km_notitie_telt_mee_in_planning():
    olie = {"id": 1, "naam": "Olie", "interval_km": 6000, "interval_maanden": None}
    plan = planning.overzicht([{"datum": "2026-10-01", "kilometerstand": 10000, "types": [olie]}], 15500, date(2026, 10, 20))
    assert plan[0]["kleur"] == "oranje" and plan[0]["tekst"] == "nog 500 km"


# ---------- 3. OCR (foto's en scans) ----------

def factuur_afbeelding(*regels, breed=1200) -> Image.Image:
    im = Image.new("RGB", (breed, 90 + 70 * len(regels)), "white")
    d = ImageDraw.Draw(im)
    letter = ImageFont.truetype("arial.ttf", 34)
    for i, t in enumerate(regels):
        d.text((60, 40 + i * 70), t, fill="black", font=letter)
    return im


FACTUUR_REGELS = ("Motorgarage Snel B.V.", "Factuurdatum: 14-03-2025", "Aan: Jan de Vries", "Dorpsstraat 12",
                  "1234 AB Utrecht", "Kilometerstand: 31.250", "Motorolie 10W-40   54,00", "Oliefilter HF303   18,50",
                  "Totaal incl. BTW  EUR 72,50")


@OCR
def test_ocr_factuur_foto_uitlezen(client):
    buf = io.BytesIO()
    factuur_afbeelding(*FACTUUR_REGELS).save(buf, "JPEG")
    with db.connect() as conn:
        types = db.types_voor(conn, "Motorfiets")
    r = factuur_lezen.lees(buf.getvalue(), "image/jpeg", types)
    namen = {t["id"]: t["naam"] for t in types}
    assert r["leesbaar"] and r["via_ocr"]
    assert (r["datum"], r["kosten"], r["kilometerstand"]) == ("2025-03-14", 72.5, 31250)
    assert {namen[t] for t in r["types"]} == {"Olie vervangen", "Oliefilter vervangen"}


@OCR
def test_ocr_suggesties_en_bedragen_op_foto_en_scan(tmp_path):
    foto = tmp_path / "f.png"
    factuur_afbeelding(*FACTUUR_REGELS).save(foto)
    sugg = lakken.suggesties(foto, "image/png", ["Jan de Vries"])
    redenen = {s["reden"] for s in sugg}
    assert {"jouw gegevens", "postcode", "adres"} <= redenen

    # bedragen weglakken op een foto: daarna leest OCR geen bedragen meer, de rest wel
    inhoud, mime = lakken.bedragen_weglakken(foto, "image/png")
    tekst = " ".join(w["tekst"] for w in ocr.herken(Image.open(io.BytesIO(inhoud))))
    assert mime == "image/png" and "54,00" not in tekst and "72,50" not in tekst and "Oliefilter" in tekst

    # gescande PDF (alleen een plaatje, geen tekstlaag)
    import pymupdf
    scan = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_image(pagina.rect, filename=str(foto))
    doc.save(scan)
    assert {"postcode"} <= {s["reden"] for s in lakken.suggesties(scan, "application/pdf", [])}
    inhoud, mime = lakken.bedragen_weglakken(scan, "application/pdf")
    gelakt = pymupdf.open(stream=inhoud, filetype="pdf")
    tekst = " ".join(w["tekst"] for w in ocr.pagina(gelakt[0]))
    assert mime == "application/pdf" and "54,00" not in tekst and "Oliefilter" in tekst


def test_zonder_ocr_valt_alles_netjes_terug(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "beschikbaar", lambda: False)
    foto = tmp_path / "f.png"
    factuur_afbeelding("Totaal 10,00").save(foto)
    assert lakken.suggesties(foto, "image/png", []) == []
    assert lakken.bedragen_weglakken(foto, "image/png") is None
    assert factuur_lezen.lees(foto.read_bytes(), "image/png", []) == {"leesbaar": False}


# ---------- 4. herinneringen ----------

def test_herinneringen_inhoud_en_hooguit_eens_per_dag(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, {"datum": "2024-01-01", "kilometerstand": "1000"})  # olie: ver over tijd
    regels = extra.controle_meldingen()
    assert any("olie vervangen" in t for _, t in regels)
    assert extra.mag_melden(regels) is True
    extra.gemeld(regels)
    assert extra.mag_melden(regels) is False  # zelfde dag niet nog eens
    client.post("/herinneringen", data={})  # uitzetten
    with db.connect() as conn:
        db.zet_instelling(conn, "melding_laatst", "{}")
    assert extra.mag_melden(regels) is False
    # voertuig in de stalling: geen herinnering
    client.post("/herinneringen", data={"aan": "1"})
    client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": date.today().isoformat()})
    assert not any("olie" in t for _, t in extra.controle_meldingen())


def test_vervaldata_apk_en_documenten_in_planning(client):
    vid = voeg_voertuig_toe(client, soort="Personenauto", kenteken="JHK-73-J")
    over_20_dagen = (date.today() + timedelta(days=20)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE voertuig SET rdw_json = ? WHERE id = ?", (json.dumps({"apk_vervaldatum": over_20_dagen}), vid))
    client.post(f"/voertuigen/{vid}/documenten", data={"soort": "Verzekeringspolis", "titel": "Polis",
                                                       "vervaldatum": (date.today() - timedelta(days=3)).isoformat()},
                files={"bestand": ("p.pdf", pdf_bytes(), "application/pdf")})
    html = client.get(f"/voertuigen/{vid}").text
    assert "APK verloopt" in html and "Polis verloopt" in html and "3 dagen verlopen" in html
    s = planning.vervalstatus("x", over_20_dagen, "rdw")
    assert s["kleur"] == "oranje" and s["tekst"] == "nog 20 dagen"


# ---------- 5. kosten ----------

def test_kostenoverzicht(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, {"datum": "2025-03-01", "kilometerstand": "1000", "kosten": "100,00"})
    nieuwe_beurt(client, vid, {"datum": "2026-03-01", "kilometerstand": "2000", "kosten": "50,00",
                               "types": [str(type_id("Olie vervangen")), str(type_id("Oliefilter vervangen"))]})
    html = client.get(f"/voertuigen/{vid}/kosten").text
    assert "€ 150,00" in html and "€ 0,15" in html  # 150 / 1000 km
    assert html.count('class="staaf-groep"') == 2 and "Beurten met meerdere klussen" in html
    assert 'data-tip="2025: € 100,00"' in html and 'class="lijn"' in html


# ---------- 6. documenten ----------

def test_documenten_toevoegen_openen_verwijderen(client):
    vid = voeg_voertuig_toe(client)
    r = client.post(f"/voertuigen/{vid}/documenten", data={"soort": "Kentekenbewijs / kentekencard"},
                    files={"bestand": ("kenteken.pdf", pdf_bytes(), "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    d = eerste("document")
    assert client.get(f"/documenten/{d['id']}").status_code == 200
    assert client.post(f"/voertuigen/{vid}/documenten", data={"soort": "Onzin"},
                       files={"bestand": ("x.pdf", pdf_bytes(), "application/pdf")}).status_code == 400
    assert client.post(f"/voertuigen/{vid}/documenten", data={"soort": "Overig"},
                       files={"bestand": ("x.exe", b"MZ", "application/x")}).status_code == 400
    # documenten gaan nooit mee in de verkoop-PDF
    pdf = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content))
    assert len(pdf.pages) == 1
    client.post(f"/documenten/{d['id']}/verwijderen")
    assert not (db.DATA_DIR / d["pad"]).exists()


def test_voertuig_verwijderen_ruimt_documenten_op(client):
    vid = voeg_voertuig_toe(client)
    client.post(f"/voertuigen/{vid}/documenten", data={"soort": "Overig"},
                files={"bestand": ("x.pdf", pdf_bytes(), "application/pdf")})
    pad = db.DATA_DIR / eerste("document")["pad"]
    client.post(f"/voertuigen/{vid}/verwijderen")
    assert not pad.exists() and eerste("document") is None


# ---------- 7. specificaties ----------

def test_specificaties_opslaan_en_in_export(client):
    vid = voeg_voertuig_toe(client)
    html = client.get(f"/voertuigen/{vid}/specificaties").text
    assert "Bandenspanning voor" in html and "Ketting" in html
    client.post(f"/voertuigen/{vid}/specificaties", data={"sleutel": ["Motorolie", "Ketting", "Eigen ding", ""],
                                                         "waarde": ["10W-40", "", "abc <b>", ""]})
    with db.connect() as conn:
        specs = {r["sleutel"]: r["waarde"] for r in conn.execute("SELECT * FROM spec")}
    assert specs == {"Motorolie": "10W-40", "Eigen ding": "abc <b>"}  # lege regels niet opgeslagen
    tekst = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content)).pages[0].extract_text()
    assert "Specificaties" in tekst and "10W-40" in tekst and "abc <b>" in tekst


# ---------- 8. voorblad verkoop-PDF ----------

def test_voorblad_samenvatting_km_en_voettekst(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, {"datum": "2025-03-01", "kilometerstand": "1000"},
                 files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "2500"})
    tekst = PdfReader(io.BytesIO(client.get(f"/voertuigen/{vid}/export.pdf").content)).pages[0].extract_text()
    for stuk in ("1 onderhoudsbeurt vastgelegd", "Bij 1 van de 1 beurten", "Kilometerstanden", "genoteerd door eigenaar",
                 "2.500", "Pagina 1", "gemaakt met Garage"):
        assert stuk in tekst, stuk


# ---------- 9. stalling ----------

def test_stalling_in_en_uit(client):
    vid = voeg_voertuig_toe(client)
    assert "In de stalling zetten" in client.get(f"/voertuigen/{vid}/stalling").text
    client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": "2025-11-01", "kilometerstand": "8000"})
    assert client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": "2025-11-02"}).status_code == 400
    assert "In de stalling sinds" in client.get(f"/voertuigen/{vid}").text and "In stalling" in client.get("/").text
    assert client.post(f"/voertuigen/{vid}/stalling/uit", data={"datum": "2025-10-01"}).status_code == 400
    assert client.post(f"/voertuigen/{vid}/stalling/uit", data={"datum": "2026-03-15", "kilometerstand": "7000"}).status_code == 400
    client.post(f"/voertuigen/{vid}/stalling/uit", data={"datum": "2026-03-15", "kilometerstand": "8010"})
    with db.connect() as conn:
        assert [r["bron"] for r in db.kmverloop(conn, vid)] == ["stalling in", "stalling uit"]
        assert db.actieve_stalling(conn, vid) is None


# ---------- weergave (licht / donker) ----------

def test_weergave_kiezen(client):
    assert 'data-thema="systeem"' in client.get("/").text
    assert client.post("/weergave", data={"thema": "donker"}).json() == {"thema": "donker"}
    assert 'data-thema="donker"' in client.get("/").text
    assert client.post("/weergave", data={"thema": "zon"}).status_code == 400  # vervallen stand
    with db.connect() as conn:  # wie 'zon' nog had opgeslagen, krijgt 'Zoals Windows'
        db.zet_instelling(conn, "thema", "zon")
    assert 'data-thema="systeem"' in client.get("/").text
    assert client.post("/weergave", data={"thema": "paars"}).status_code == 400


def test_titelbalk_kleuren_gelijk_aan_css():
    from tests.test_nieuw import laad_starter
    garage = laad_starter()
    css = (Path(__file__).parent.parent / "app" / "static" / "app.css").read_text(encoding="utf-8")
    licht = css[:css.index(":root[data-donker]")]
    donker = css[css.index(":root[data-donker]"):]
    for blok, (balk, tekst, lijn) in ((licht, garage.BALK_LICHT), (donker, garage.BALK_DONKER)):
        assert re.search(r"--balk: (#\w+)", blok).group(1) == balk
        assert re.search(r"--text: (#\w+)", blok).group(1) == tekst
        assert re.search(r"--line: (#\w+)", blok).group(1) == lijn
    assert garage.mix(garage.BALK_LICHT, garage.BALK_DONKER, 0) == garage.BALK_LICHT
    assert garage.mix(garage.BALK_LICHT, garage.BALK_DONKER, 1) == garage.BALK_DONKER
    assert garage.start_donker("donker") is True and garage.start_donker("licht") is False


def test_starter_controle_zonder_gegevens_doet_niets(monkeypatch, tmp_path):
    from tests.test_nieuw import laad_starter
    garage = laad_starter()
    monkeypatch.setenv("ONDERHOUD_DATA", str(tmp_path / "leeg"))
    monkeypatch.setattr(garage, "toon_melding", lambda *a: (_ for _ in ()).throw(AssertionError("geen melding")))
    garage.controle()  # geen database: stil klaar



# ---------- regressies uit de drie reviews ----------

def test_andere_websites_kunnen_niets_wijzigen_of_lezen(client):
    vid = voeg_voertuig_toe(client)
    # CSRF: formulier vanaf een andere site
    r = client.post("/weergave", data={"thema": "donker"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post(f"/voertuigen/{vid}/verwijderen", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and eerste("voertuig") is not None
    # DNS-rebinding: verkeerde Host, ook voor lezen
    assert client.get("/", headers={"host": "evil.example:8000"}).status_code == 403
    # eigen pagina's mogen wel
    assert client.post("/weergave", data={"thema": "licht"}, headers={"origin": "http://testserver",
                                                                     "sec-fetch-site": "same-origin"}).status_code == 200
    # 'Origin: null' komt o.a. uit een sandboxed iframe van een andere site
    assert client.post("/weergave", data={"thema": "donker"}, headers={"origin": "null"}).status_code == 403


def test_te_groot_verzoek_geweigerd(client):
    vid = voeg_voertuig_toe(client)
    groot = {"content-length": str(200 * 1024 ** 2 + 1)}
    assert client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "1"}, headers=groot).status_code == 413
    assert client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "1"}, headers={"content-length": "x"}).status_code == 400
    assert eerste("km_stand") is None


def test_bestandspaden_buiten_de_gegevensmap_geweigerd(client, tmp_path):
    for slecht in ("C:/Windows/win.ini", "../geheim.txt", "facturen/../../x", "\\\\server\\share\\x", "", None, "data/x"):
        with pytest.raises(ValueError):
            db.bestand(slecht)
    assert db.bestand("facturen/1/abc.pdf") == (db.DATA_DIR / "facturen/1/abc.pdf").resolve()
    # een gemanipuleerde database in een back-up wordt niet teruggezet
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    with db.connect() as conn:
        conn.execute("UPDATE factuur SET pad = 'C:/Windows/win.ini'")
    kwaad = backup.maak()
    with db.connect() as conn:
        conn.execute("UPDATE factuur SET pad = 'facturen/1/x.pdf'")
    with pytest.raises(backup.BackupFout, match="ongeldige gegevens"):
        backup.herstel(kwaad)


def test_mime_type_uit_extensie_en_back_up_met_html_geweigerd(client):
    """Een 'factuur' die in de database text/html heet, mag nooit als webpagina binnen Garage draaien."""
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    client.post(f"/voertuigen/{vid}/documenten", data={"soort": "Overig"},
                files={"bestand": ("d.pdf", pdf_bytes(), "application/pdf")})
    with db.connect() as conn:
        conn.execute("UPDATE factuur SET mime_type = 'text/html'")
        conn.execute("UPDATE document SET mime_type = 'text/html'")
    f, d = eerste("factuur"), eerste("document")
    for url in (f"/facturen/{f['id']}", f"/facturen/{f['id']}/origineel", f"/documenten/{d['id']}"):
        assert client.get(url).headers["content-type"] == "application/pdf"
    kwaad = backup.maak()
    with pytest.raises(backup.BackupFout, match="ongeldige gegevens"):
        backup.herstel(kwaad)


def test_beveiligingsheaders(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    pagina = client.get(f"/voertuigen/{vid}")
    csp = pagina.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp and "object-src 'none'" in csp
    pdf = client.get(f"/facturen/{eerste('factuur')['id']}")
    assert "content-security-policy" not in pdf.headers  # PDF-viewer niet hinderen
    for r in (pagina, pdf, client.get("/", headers={"host": "evil.example"})):
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_geen_netwerkmap_en_back_upmap_niet_uit_back_up(client, tmp_path):
    for netwerk in ("\\\\aanvaller\\share", "//aanvaller/share"):
        r = client.post("/backup/instellingen", data={"map": netwerk})
        assert r.status_code == 400 and "netwerkmap" in r.text
    voeg_voertuig_toe(client)
    andere = tmp_path / "andere-map"
    with db.connect() as conn:
        db.zet_instelling(conn, "backup_map", str(andere))
    zp = backup.maak()
    with db.connect() as conn:
        db.zet_instelling(conn, "backup_map", str(tmp_path / "backups"))
    backup.herstel(zp)
    with db.connect() as conn:  # de map van deze pc blijft, ook al staat er iets anders in de back-up
        assert db.instelling(conn, "backup_map") == str(tmp_path / "backups")


def test_oudste_back_up_terugzetten_en_veiligheidskopie_apart(client, monkeypatch, tmp_path):
    voeg_voertuig_toe(client)
    teller = iter(range(1000))
    monkeypatch.setattr(backup, "datetime", type("D", (), {"now": staticmethod(
        lambda: datetime(2026, 1, 1) + timedelta(minutes=next(teller))), "fromisoformat": datetime.fromisoformat,
        "fromtimestamp": datetime.fromtimestamp}))
    for _ in range(backup.BEWAAR):
        backup.maak()
    oudste = backup.lijst(tmp_path / "backups")[-1]["pad"]
    backup.herstel(oudste)  # mocht vroeger mislukken: de veiligheidskopie ruimde de bron op
    assert oudste.exists() and len(backup.lijst(tmp_path / "backups")) == backup.BEWAAR
    assert len(backup.lijst(tmp_path / "backups", backup.VEILIGHEID)) == 1


def test_terugzetten_met_open_bestand_verandert_niets(client, monkeypatch):
    vid = voeg_voertuig_toe(client, kenteken="AA-11-BB")
    nieuwe_beurt(client, vid, files=[("facturen", ("f.pdf", pdf_bytes(), "application/pdf"))])
    zp = backup.maak()
    voeg_voertuig_toe(client, kenteken="ZZ-99-ZZ")
    echte_replace = os.replace

    def vergrendeld(a, b):  # zoals Windows doet als er een factuur open staat in een PDF-lezer
        if str(a).endswith("facturen"):
            raise PermissionError(32, "in gebruik")
        return echte_replace(a, b)
    monkeypatch.setattr(backup.os, "replace", vergrendeld)
    with pytest.raises(backup.BackupFout, match="open"):
        backup.herstel(zp)
    with db.connect() as conn:  # niets veranderd: beide voertuigen nog daar, facturen intact
        assert conn.execute("SELECT COUNT(*) FROM voertuig").fetchone()[0] == 2
        assert db.bestand(conn.execute("SELECT pad FROM factuur").fetchone()[0]).is_file()


def test_stalling_corrigeren_en_datumcontroles(client):
    vid = voeg_voertuig_toe(client)
    client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": "2025-11-01", "kilometerstand": "150000"})  # tikfout
    r = client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "15500"})
    assert r.status_code == 400 and "bij het stallen" in r.text
    client.post(f"/stalling/{eerste('stalling')['id']}/verwijderen")
    assert client.post(f"/voertuigen/{vid}/km", data={"kilometerstand": "15500"}, follow_redirects=False).status_code == 303
    morgen = (date.today() + timedelta(days=1)).isoformat()
    assert client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": morgen}).status_code == 400
    client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": "2025-11-01"})
    client.post(f"/voertuigen/{vid}/stalling/uit", data={"datum": "2026-03-01"})
    assert client.post(f"/voertuigen/{vid}/stalling/in", data={"datum": "2026-01-01"}).status_code == 400  # overlap


def test_herinnering_niet_elke_dag_door_aftellende_tekst(client):
    vid = voeg_voertuig_toe(client)
    nieuwe_beurt(client, vid, {"datum": "2024-01-01", "kilometerstand": "1000"})
    regels = extra.controle_meldingen()
    extra.gemeld(regels)
    with db.connect() as conn:  # 'morgen', zelfde situatie maar andere tekst (nog 9 i.p.v. 10 dagen)
        m = json.loads(db.instelling(conn, "melding_laatst"))
        m["datum"] = "2000-01-01"
        db.zet_instelling(conn, "melding_laatst", json.dumps(m))
    anders_getekst = [(s, t + " (andere aftelling)") for s, t in regels]
    assert extra.mag_melden(anders_getekst) is False
    assert extra.mag_melden(regels + [("nieuw|x|rood", "iets nieuws")]) is True


def test_bedragen_varianten_en_halve_scan(tmp_path, monkeypatch):
    for w in ("1250,00", "€1250,00", "72,5", "€72", "€1.250,00", "54,00", "EUR"):
        assert lakken.BEDRAG_WOORD.fullmatch(w), w
    for w in ("2026", "14-03-2025", "12.345", "HF303", "10W-40", "21%"):
        assert not lakken.BEDRAG_WOORD.fullmatch(w), w
    # PDF met een tekstpagina en een pagina waarop niets te herkennen is: niet veilig -> weglaten
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Totaal 50,00")
    doc.new_page()  # leeg (bijv. scan waarop OCR faalt)
    pad = tmp_path / "half.pdf"
    doc.save(pad)
    monkeypatch.setattr(ocr, "beschikbaar", lambda: False)
    assert lakken.bedragen_weglakken(pad, "application/pdf") is None


def test_ongeldige_datums_breken_geen_pagina(client):
    vid = voeg_voertuig_toe(client)
    with db.connect() as conn:
        conn.execute("UPDATE voertuig SET rdw_json = ? WHERE id = ?", (json.dumps({"apk_vervaldatum": "morgen"}), vid))
    assert client.get("/").status_code == 200 and client.get(f"/voertuigen/{vid}").status_code == 200
    assert extra.controle_meldingen() is not None
