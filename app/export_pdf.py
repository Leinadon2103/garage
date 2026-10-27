"""Verkoop-PDF: voorblad + historietabel, met alle facturen als bijlagen erachter."""
import io
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import db, lakken
from .web import euro as _euro

STYLES = getSampleStyleSheet()


def _km(km) -> str:
    return "" if km is None else f"{km:,}".replace(",", ".")


def _nl_datum(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return iso or ""


ORANJE = colors.HexColor("#f26b0f")
DONKERBLAUW = colors.HexColor("#2c3e50")


def _tabel(rijen, breedtes, kop=True) -> Table:
    t = Table(rijen, colWidths=[w * cm for w in breedtes], repeatRows=1 if kop else 0)
    stijl = [("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bdc3c7"))]
    if kop:
        stijl += [("BACKGROUND", (0, 0), (-1, 0), DONKERBLAUW), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f5")])]
    t.setStyle(TableStyle(stijl))
    return t


def _geleden(d: date) -> str:
    dagen = (date.today() - d).days
    if dagen < 1:
        return "vandaag"
    if dagen < 45:
        return f"{dagen} dag{'en' if dagen != 1 else ''} geleden"
    maanden = round(dagen / 30.4)
    return f"{maanden} maand{'en' if maanden != 1 else ''} geleden" if maanden < 24 else f"{round(dagen / 365)} jaar geleden"


def _foto(voertuig):
    """Foto van het voertuig voor het voorblad (max. 7 x 5 cm), of None."""
    pad = voertuig["beeld_pad"] if "beeld_pad" in voertuig.keys() else None
    try:
        pad = db.bestand(pad)
        with Image.open(pad) as im:
            b, h = im.size
        schaal = min(7 * cm / b, 5 * cm / h)
        return RLImage(str(pad), width=b * schaal, height=h * schaal)
    except (OSError, ValueError):
        return None


def _voettekst(canv, doc):
    canv.saveState()
    canv.setFont("Helvetica", 8)
    canv.setFillColor(colors.HexColor("#7f8c8d"))
    canv.drawString(2 * cm, 1.2 * cm, f"Onderhoudshistorie gemaakt met Garage op {date.today():%d-%m-%Y}")
    canv.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Pagina {doc.page}")
    canv.restoreState()


def _overzicht(voertuig, beurten: list[dict], bijlage_nrs: dict, met_kosten: bool,
               km: list[dict] | None = None, specs: list | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=f"Onderhoudshistorie {voertuig['kenteken']}")
    p = lambda tekst, stijl="BodyText": Paragraph(tekst, STYLES[stijl])
    km = km or []
    huidige_km = max((r["kilometerstand"] for r in km), default=None) if km else \
        max((b["kilometerstand"] for b in beurten if b["kilometerstand"] is not None), default=None)

    gegevens = [
        ["Kenteken", voertuig["kenteken"]],
        ["Voertuig", f"{voertuig['merk'] or ''} {voertuig['model'] or ''}".strip()],
        ["Soort", voertuig["voertuigsoort"]],
        ["Eerste toelating", _nl_datum(voertuig["datum_eerste_toelating"])],
        ["Kleur", voertuig["kleur"] or ""],
        ["Laatst bekende km-stand", _km(huidige_km)],
        ["Aantal onderhoudsbeurten", str(len(beurten))],
        ["Datum uitdraai", date.today().strftime("%d-%m-%Y")],
    ]
    gegevens = [rij for rij in gegevens if rij[1]]
    foto = _foto(voertuig)
    info = Table(gegevens, colWidths=[4.6 * cm, (5.2 if foto else 11.9) * cm])
    info.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    kopblok = info
    if foto:
        kopblok = Table([[foto, info]], colWidths=[7.4 * cm, 9.6 * cm])
        kopblok.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    # samenvatting voor de koper
    chronologisch = sorted(beurten, key=lambda b: (b["datum"], b["id"]))
    samenvatting = []
    if chronologisch:
        eerste, laatste = date.fromisoformat(chronologisch[0]["datum"]), date.fromisoformat(chronologisch[-1]["datum"])
        samenvatting.append(f"<b>{len(beurten)}</b> onderhoudsbeurt{'en' if len(beurten) != 1 else ''} vastgelegd"
                            + (f" tussen {eerste:%d-%m-%Y} en {laatste:%d-%m-%Y}" if eerste != laatste else f" op {eerste:%d-%m-%Y}") + ".")
        samenvatting.append(f"Laatste beurt: <b>{laatste:%d-%m-%Y}</b> ({_geleden(laatste)}).")
        met_factuur = sum(1 for b in beurten if b["facturen"])
        samenvatting.append(f"Bij <b>{met_factuur} van de {len(beurten)}</b> beurten is een factuur als bijlage bijgevoegd.")
    if km:
        samenvatting.append(f"<b>{len(km)}</b> geregistreerde kilometerstanden, van {_km(km[0]['kilometerstand'])} km "
                            f"({_nl_datum(km[0]['datum'])}) tot {_km(km[-1]['kilometerstand'])} km ({_nl_datum(km[-1]['datum'])}).")

    kop = ["Datum", "Km-stand", "Werkzaamheden", "Uitgevoerd door"] + (["Kosten"] if met_kosten else []) + ["Bijlage"]
    rijen = [kop]
    for b in chronologisch:
        werk = _escape(", ".join(t["naam"] for t in b["types"]))
        if b["opmerking"]:
            werk += f"<br/><i>{_escape(b['opmerking'])}</i>"
        rij = [date.fromisoformat(b["datum"]).strftime("%d-%m-%Y"), _km(b["kilometerstand"]), p(werk),
               p(_escape(b["uitgevoerd_door"] or ""))]
        if met_kosten:
            rij.append(_euro(b["kosten"]))
        rij.append(", ".join(str(bijlage_nrs[f["id"]]) for f in b["facturen"]))
        rijen.append(rij)
    historie = _tabel(rijen, [2.3, 2.2, 6.0, 3.3] + ([2.2] if met_kosten else []) + [1.5])

    verhaal = [p(f"Onderhoudshistorie {voertuig['kenteken']}", "Title"), kopblok, Spacer(1, 0.5 * cm)]
    if samenvatting:
        samen = Table([[p("<br/>".join(samenvatting))]], colWidths=[17 * cm])
        samen.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff4ec")),
                                   ("LINEBEFORE", (0, 0), (0, -1), 3, ORANJE), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                   ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        verhaal += [samen, Spacer(1, 0.6 * cm)]
    verhaal += [p("Onderhoudshistorie", "Heading2"), historie if beurten else p("Nog geen onderhoud vastgelegd.")]
    if km:
        bron = {"beurt": "onderhoudsbeurt", "notitie": "genoteerd door eigenaar", "stalling in": "stalling in",
                "stalling uit": "stalling uit"}
        verhaal += [Spacer(1, 0.5 * cm), p("Kilometerstanden", "Heading2"),
                    _tabel([["Datum", "Km-stand", "Vastgelegd bij"]] +
                           [[_nl_datum(r["datum"]), _km(r["kilometerstand"]), bron.get(r["bron"], r["bron"])] for r in km],
                           [4, 4, 9])]
    if specs:
        verhaal += [Spacer(1, 0.5 * cm), p("Specificaties", "Heading2"),
                    _tabel([["Onderdeel", "Waarde"]] + [[p(_escape(s["sleutel"])), p(_escape(s["waarde"]))] for s in specs], [6, 11])]
    if bijlage_nrs:
        verhaal += [Spacer(1, 0.5 * cm), p("De facturen zijn als genummerde bijlagen toegevoegd achter dit overzicht.")]
    doc.build(verhaal, onFirstPage=_voettekst, onLaterPages=_voettekst)
    return buf.getvalue()


def _escape(tekst: str) -> str:
    return tekst.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


KOP_HOOGTE = 20


def _kop_boven_pagina(pagina, tekst: str) -> None:
    """Maakt de zichtbare pagina bovenaan KOP_HOOGTE hoger en zet daar de kopregel, zodat de factuur intact blijft."""
    pagina.transfer_rotation_to_content()  # gedraaide scans: kop komt dan echt bovenaan
    crop, media = pagina.cropbox, pagina.mediabox
    links, onder, rechts, boven = (float(v) for v in (crop.left, crop.bottom, crop.right, crop.top))
    nieuw_boven = boven + KOP_HOOGTE
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(rechts, nieuw_boven))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(links + 10, nieuw_boven - 14, tekst)
    c.save()
    pagina.mediabox = RectangleObject([float(media.left), float(media.bottom), float(media.right),
                                       max(float(media.top), nieuw_boven)])
    pagina.cropbox = RectangleObject([links, onder, rechts, nieuw_boven])
    pagina.merge_page(PdfReader(buf).pages[0], over=True)


def _afbeelding_pdf(pad: Path, kop: str) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    b, h = A4
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, h - 1.5 * cm, kop)
    with Image.open(pad) as im:
        img = ImageReader(ImageOps.exif_transpose(im))  # telefoonfoto's rechtop zetten
        iw, ih = img.getSize()
        schaal = min((b - 4 * cm) / iw, (h - 4 * cm) / ih)
        c.drawImage(img, 2 * cm, h - 2.2 * cm - ih * schaal, iw * schaal, ih * schaal, mask="auto")
    c.save()
    return PdfReader(buf)


def _foutpagina(kop: str, reden: str) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, A4[1] - 1.5 * cm, kop)
    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, A4[1] - 2.5 * cm, f"Deze bijlage kon niet worden ingevoegd: {reden}")
    c.save()
    return PdfReader(buf)


def lees_pdf(bron) -> PdfReader:
    """Opent een PDF en controleert dat hij leesbaar is (niet versleuteld of kapot)."""
    reader = PdfReader(bron)
    if reader.is_encrypted:
        raise ValueError("PDF is beveiligd met een wachtwoord")
    if len(reader.pages) == 0:
        raise ValueError("PDF heeft geen pagina's")
    return reader


def maak(voertuig, beurten: list[dict], met_kosten: bool = True, km: list[dict] | None = None,
         specs: list | None = None) -> bytes:
    chronologisch = sorted(beurten, key=lambda b: (b["datum"], b["id"]))
    bijlagen = [(b, f) for b in chronologisch for f in b["facturen"]]
    bijlage_nrs = {f["id"]: i for i, (_, f) in enumerate(bijlagen, start=1)}

    # Foutpagina's noemen de bijlage alleen via de kop (nummer, datum, onderhoud), nooit via de oorspronkelijke
    # bestandsnaam: die kan een naam bevatten, bijv. 'factuur Jansen.pdf'.
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_overzicht(voertuig, beurten, bijlage_nrs, met_kosten, km, specs))))

    for nr, (b, f) in enumerate(bijlagen, start=1):
        kop = (f"Bijlage {nr} - {date.fromisoformat(b['datum']).strftime('%d-%m-%Y')} - "
               f"{', '.join(t['naam'] for t in b['types'])}")
        try:
            pad = db.bestand(f["pad"])
        except ValueError:
            writer.append(_foutpagina(kop, "het bestand heeft een ongeldig bestandspad"))
            continue
        # één kapotte of ontbrekende bijlage mag de hele uitdraai niet blokkeren
        if not f["gecontroleerd"]:  # nooit een ongecontroleerd origineel delen
            writer.append(_foutpagina(kop, "de factuur is nog niet gecontroleerd op persoonsgegevens"))
            continue
        try:
            if not met_kosten:
                # kosten verbergen geldt ook voor de facturen zelf: bedragen echt verwijderen (alleen in deze uitdraai)
                zonder_bedragen = lakken.bedragen_weglakken(pad, f["mime_type"])
                if zonder_bedragen is None:
                    writer.append(_foutpagina(kop, "de factuur is weggelaten: er was geen tekst te herkennen, "
                                                   "dus bedragen kunnen niet worden verborgen"))
                    continue
                pad, mime = io.BytesIO(zonder_bedragen[0]), zonder_bedragen[1]
            else:
                mime = f["mime_type"]
            if mime == "application/pdf":
                reader = lees_pdf(pad)
                start = len(writer.pages)
                writer.append(reader)
                _kop_boven_pagina(writer.pages[start], kop)
            else:
                writer.append(_afbeelding_pdf(pad, kop))
        except FileNotFoundError:
            writer.append(_foutpagina(kop, "het bestand ontbreekt"))
        except Exception as e:  # noqa: BLE001 - pypdf/PIL gooien uiteenlopende fouten
            writer.append(_foutpagina(kop, f"het bestand is onleesbaar ({type(e).__name__})"))

    writer.add_metadata({"/Title": f"Onderhoudshistorie {voertuig['kenteken']}"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
