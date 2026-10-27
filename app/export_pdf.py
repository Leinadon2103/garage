"""Sale PDF: cover page + history table, with all invoices as attachments behind it."""
import io
from datetime import date

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

from . import db, redaction
from .web import euro as _euro

STYLES = getSampleStyleSheet()


def _km(km) -> str:
    return "" if km is None else f"{km:,}".replace(",", ".")


def _nl_date(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return iso or ""


ORANGE = colors.HexColor("#f26b0f")
DARK_BLUE = colors.HexColor("#2c3e50")


def _table(rows, widths, header=True) -> Table:
    t = Table(rows, colWidths=[w * cm for w in widths], repeatRows=1 if header else 0)
    style = [("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bdc3c7"))]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f5")])]
    t.setStyle(TableStyle(style))
    return t


def _ago(d: date) -> str:
    days = (date.today() - d).days
    if days < 1:
        return "vandaag"
    if days < 45:
        return f"{days} dag{'en' if days != 1 else ''} geleden"
    months = round(days / 30.4)
    return f"{months} maand{'en' if months != 1 else ''} geleden" if months < 24 else f"{round(days / 365)} jaar geleden"


def _photo(vehicle):
    """Photo of the vehicle for the cover page (max. 7 x 5 cm), or None."""
    path = vehicle["image_path"] if "image_path" in vehicle.keys() else None
    try:
        content = db.read_file(path)
        with Image.open(io.BytesIO(content)) as im:
            w, h = im.size
        scale = min(7 * cm / w, 5 * cm / h)
        return RLImage(io.BytesIO(content), width=w * scale, height=h * scale)
    except (OSError, ValueError):
        return None


def _footer(canv, doc):
    canv.saveState()
    canv.setFont("Helvetica", 8)
    canv.setFillColor(colors.HexColor("#7f8c8d"))
    canv.drawString(2 * cm, 1.2 * cm, f"Onderhoudshistorie gemaakt met Garage op {date.today():%d-%m-%Y}")
    canv.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Pagina {doc.page}")
    canv.restoreState()


def _overview(vehicle, services: list[dict], attachment_nrs: dict, with_costs: bool,
              km: list[dict] | None = None, specs: list | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=f"Onderhoudshistorie {vehicle['license_plate']}")
    p = lambda text, style="BodyText": Paragraph(text, STYLES[style])
    km = km or []
    current_km = max((r["odometer_km"] for r in km), default=None) if km else \
        max((s["odometer_km"] for s in services if s["odometer_km"] is not None), default=None)

    details = [
        ["Kenteken", vehicle["license_plate"]],
        ["Voertuig", f"{vehicle['make'] or ''} {vehicle['model'] or ''}".strip()],
        ["Soort", vehicle["vehicle_type"]],
        ["Eerste toelating", _nl_date(vehicle["first_registration_date"])],
        ["Kleur", vehicle["color"] or ""],
        ["Laatst bekende km-stand", _km(current_km)],
        ["Aantal onderhoudsbeurten", str(len(services))],
        ["Datum uitdraai", date.today().strftime("%d-%m-%Y")],
    ]
    details = [row for row in details if row[1]]
    photo = _photo(vehicle)
    info = Table(details, colWidths=[4.6 * cm, (5.2 if photo else 11.9) * cm])
    info.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    header_block = info
    if photo:
        header_block = Table([[photo, info]], colWidths=[7.4 * cm, 9.6 * cm])
        header_block.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    # summary for the buyer
    chronological = sorted(services, key=lambda s: (s["date"], s["id"]))
    summary = []
    if chronological:
        first, last = date.fromisoformat(chronological[0]["date"]), date.fromisoformat(chronological[-1]["date"])
        summary.append(f"<b>{len(services)}</b> onderhoudsbeurt{'en' if len(services) != 1 else ''} vastgelegd"
                       + (f" tussen {first:%d-%m-%Y} en {last:%d-%m-%Y}" if first != last else f" op {first:%d-%m-%Y}") + ".")
        summary.append(f"Laatste beurt: <b>{last:%d-%m-%Y}</b> ({_ago(last)}).")
        with_invoice = sum(1 for s in services if s["invoices"])
        summary.append(f"Bij <b>{with_invoice} van de {len(services)}</b> beurten is een factuur als bijlage bijgevoegd.")
    if km:
        summary.append(f"<b>{len(km)}</b> geregistreerde kilometerstanden, van {_km(km[0]['odometer_km'])} km "
                       f"({_nl_date(km[0]['date'])}) tot {_km(km[-1]['odometer_km'])} km ({_nl_date(km[-1]['date'])}).")

    header = ["Datum", "Km-stand", "Werkzaamheden", "Uitgevoerd door"] + (["Kosten"] if with_costs else []) + ["Bijlage"]
    rows = [header]
    for s in chronological:
        work = _escape(", ".join(t["name"] for t in s["types"]))
        if s["note"]:
            work += f"<br/><i>{_escape(s['note'])}</i>"
        row = [date.fromisoformat(s["date"]).strftime("%d-%m-%Y"), _km(s["odometer_km"]), p(work),
               p(_escape(s["performed_by"] or ""))]
        if with_costs:
            row.append(_euro(s["cost"]))
        row.append(", ".join(str(attachment_nrs[f["id"]]) for f in s["invoices"]))
        rows.append(row)
    history = _table(rows, [2.3, 2.2, 6.0, 3.3] + ([2.2] if with_costs else []) + [1.5])

    story = [p(f"Onderhoudshistorie {vehicle['license_plate']}", "Title"), header_block, Spacer(1, 0.5 * cm)]
    if summary:
        box = Table([[p("<br/>".join(summary))]], colWidths=[17 * cm])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff4ec")),
                                 ("LINEBEFORE", (0, 0), (0, -1), 3, ORANGE), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        story += [box, Spacer(1, 0.6 * cm)]
    story += [p("Onderhoudshistorie", "Heading2"), history if services else p("Nog geen onderhoud vastgelegd.")]
    if km:
        source = {"service": "onderhoudsbeurt", "reading": "genoteerd door eigenaar", "storage_start": "stalling in",
                  "storage_end": "stalling uit"}
        story += [Spacer(1, 0.5 * cm), p("Kilometerstanden", "Heading2"),
                  _table([["Datum", "Km-stand", "Vastgelegd bij"]] +
                         [[_nl_date(r["date"]), _km(r["odometer_km"]), source.get(r["source"], r["source"])] for r in km],
                         [4, 4, 9])]
    if specs:
        story += [Spacer(1, 0.5 * cm), p("Specificaties", "Heading2"),
                  _table([["Onderdeel", "Waarde"]] + [[p(_escape(s["key"])), p(_escape(s["value"]))] for s in specs], [6, 11])]
    if attachment_nrs:
        story += [Spacer(1, 0.5 * cm), p("De facturen zijn als genummerde bijlagen toegevoegd achter dit overzicht.")]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


HEADER_HEIGHT = 20


def _header_above_page(page, text: str) -> None:
    """Makes the visible page HEADER_HEIGHT taller at the top and puts the header there, so the invoice stays intact."""
    page.transfer_rotation_to_content()  # rotated scans: the header then really ends up on top
    crop, media = page.cropbox, page.mediabox
    left, bottom, right, top = (float(v) for v in (crop.left, crop.bottom, crop.right, crop.top))
    new_top = top + HEADER_HEIGHT
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(right, new_top))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left + 10, new_top - 14, text)
    c.save()
    page.mediabox = RectangleObject([float(media.left), float(media.bottom), float(media.right),
                                     max(float(media.top), new_top)])
    page.cropbox = RectangleObject([left, bottom, right, new_top])
    page.merge_page(PdfReader(buf).pages[0], over=True)


def _image_pdf(content: bytes, header: str) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, h - 1.5 * cm, header)
    with Image.open(io.BytesIO(content)) as im:
        img = ImageReader(ImageOps.exif_transpose(im))  # put phone photos upright
        iw, ih = img.getSize()
        scale = min((w - 4 * cm) / iw, (h - 4 * cm) / ih)
        c.drawImage(img, 2 * cm, h - 2.2 * cm - ih * scale, iw * scale, ih * scale, mask="auto")
    c.save()
    return PdfReader(buf)


def _error_page(header: str, reason: str) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, A4[1] - 1.5 * cm, header)
    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, A4[1] - 2.5 * cm, f"Deze bijlage kon niet worden ingevoegd: {reason}")
    c.save()
    return PdfReader(buf)


def read_pdf(source) -> PdfReader:
    """Opens a PDF and checks that it is readable (not encrypted or broken)."""
    reader = PdfReader(source)
    if reader.is_encrypted:
        raise ValueError("PDF is beveiligd met een wachtwoord")
    if len(reader.pages) == 0:
        raise ValueError("PDF heeft geen pagina's")
    return reader


def create(vehicle, services: list[dict], with_costs: bool = True, km: list[dict] | None = None,
           specs: list | None = None) -> bytes:
    chronological = sorted(services, key=lambda s: (s["date"], s["id"]))
    attachments = [(s, f) for s in chronological for f in s["invoices"]]
    attachment_nrs = {f["id"]: i for i, (_, f) in enumerate(attachments, start=1)}

    # Error pages name the attachment only through the header (number, date, maintenance), never through the
    # original file name: that can contain a name, e.g. 'factuur Jansen.pdf'.
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_overview(vehicle, services, attachment_nrs, with_costs, km, specs))))

    for nr, (s, f) in enumerate(attachments, start=1):
        header = (f"Bijlage {nr} - {date.fromisoformat(s['date']).strftime('%d-%m-%Y')} - "
                  f"{', '.join(t['name'] for t in s['types'])}")
        # one broken or missing attachment must not block the whole printout
        if not f["reviewed"]:  # never share an unreviewed original
            writer.append(_error_page(header, "de factuur is nog niet gecontroleerd op persoonsgegevens"))
            continue
        try:
            content, mime = db.read_file(f["path"]), f["mime_type"]
        except ValueError:
            writer.append(_error_page(header, "het bestand heeft een ongeldig bestandspad"))
            continue
        except FileNotFoundError:
            writer.append(_error_page(header, "het bestand ontbreekt"))
            continue
        try:
            if not with_costs:
                # hiding costs also applies to the invoices: really remove amounts (only in this printout)
                without_amounts = redaction.redact_amounts(content, mime)
                if without_amounts is None:
                    writer.append(_error_page(header, "de factuur is weggelaten: er was geen tekst te herkennen, "
                                                      "dus bedragen kunnen niet worden verborgen"))
                    continue
                content, mime = without_amounts
            if mime == "application/pdf":
                reader = read_pdf(io.BytesIO(content))
                start = len(writer.pages)
                writer.append(reader)
                _header_above_page(writer.pages[start], header)
            else:
                writer.append(_image_pdf(content, header))
        except Exception as e:  # noqa: BLE001 - pypdf/PIL raise all kinds of errors
            writer.append(_error_page(header, f"het bestand is onleesbaar ({type(e).__name__})"))

    writer.add_metadata({"/Title": f"Onderhoudshistorie {vehicle['license_plate']}"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
