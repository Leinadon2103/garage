"""Persoonsgegevens van facturen weglakken.

Boxen zijn fracties (0-1) van de paginabreedte/-hoogte, zodat de browser ze los van de pixelgrootte
kan tekenen. Bij PDF's wordt de tekst onder een balk echt verwijderd (PyMuPDF redaction), niet alleen
bedekt; bij afbeeldingen worden de pixels zwart gemaakt en metadata (EXIF, ook GPS) weggegooid.
"""
import io
import re
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageOps

from . import ocr

BREEDTE_PX = 1200  # renderbreedte voor het lakscherm

PATRONEN = {
    "postcode": re.compile(r"\b\d{4}\s?[A-Z]{2}\b"),
    "e-mail": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "telefoon": re.compile(r"(?:\+31|0031|\b0)[\s-]?(?:\(0\))?[\s-]?\d(?:[\s-]?\d){8}\b"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z]{4}(?:\s?\d){10}\b"),
    "adres": re.compile(r"\b[A-Za-z][\w'.-]*(?:straat|weg|laan|plein|singel|gracht|kade|dijk|pad|hof|dreef|steeg|markt|ring|park)"
                        r"\s+\d+\s?[a-zA-Z]?\b", re.IGNORECASE),
}


class LakFout(Exception):
    pass


def _open_pdf(pad: Path) -> pymupdf.Document:
    if isinstance(pad, (bytes, io.BytesIO)):
        doc = pymupdf.open(stream=pad.getvalue() if isinstance(pad, io.BytesIO) else pad, filetype="pdf")
    else:
        doc = pymupdf.open(pad)
    # annotaties (ook sticky notes met tekst in een popup) en formuliervelden omzetten naar gewone
    # pagina-inhoud: dan zie je ze in het lakscherm, vinden de suggesties ze en verdwijnen ze onder een balk
    doc.bake(annots=True, widgets=True)
    for pagina in doc:
        if pagina.rotation:
            pagina.remove_rotation()  # daarna zijn render- en tekstcoördinaten gelijk
    return doc


def _open_afbeelding(pad: Path) -> Image.Image:
    try:
        with Image.open(pad) as im:
            im = ImageOps.exif_transpose(im).copy()
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise LakFout(f"Afbeelding kan niet worden geopend ({type(e).__name__}).")
    im.info.clear()  # geen EXIF/XMP/commentaar meenemen bij opslaan
    return im


def aantal_paginas(pad: Path, mime: str) -> int:
    if mime != "application/pdf":
        return 1
    with pymupdf.open(pad) as doc:
        return doc.page_count


def pagina_png(pad: Path, mime: str, nr: int) -> bytes:
    if mime == "application/pdf":
        with _open_pdf(pad) as doc:
            pagina = doc[nr]
            zoom = min(BREEDTE_PX / pagina.rect.width, 3)
            return pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    im = _open_afbeelding(pad)
    im.thumbnail((BREEDTE_PX * 2, BREEDTE_PX * 2))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


MARGE = 0.003  # kleine rand links/rechts om elk woord, als fractie van de pagina (boven/onder niet:
               # woordkaders zijn al regelhoog en zouden anders de regel erboven/eronder raken)


def woorden_per_pagina(pad, mime: str) -> list[list[dict]]:
    """Per pagina de woorden met kader (fracties 0-1) en een regel-sleutel.
    Uit de tekstlaag van een PDF; bij foto's en gescande pagina's via Windows-OCR."""
    if mime != "application/pdf":
        return [ocr.herken(_open_afbeelding(pad))]
    resultaat = []
    with _open_pdf(pad) as doc:
        for p in doc:
            r = p.rect
            tekst = p.get_text("words")
            resultaat.append([{"tekst": w[4], "regel": (w[5], w[6]),
                               "x0": (w[0] - r.x0) / r.width, "y0": (w[1] - r.y0) / r.height,
                               "x1": (w[2] - r.x0) / r.width, "y1": (w[3] - r.y0) / r.height} for w in tekst]
                             if tekst else ocr.pagina(p))
    return resultaat


def _box(woorden: list[dict], nr: int, reden: str, actief: bool) -> dict:
    return {"pagina": nr, "reden": reden, "actief": actief,
            "x0": max(0, min(w["x0"] for w in woorden) - MARGE), "y0": max(0, min(w["y0"] for w in woorden)),
            "x1": min(1, max(w["x1"] for w in woorden) + MARGE), "y1": min(1, max(w["y1"] for w in woorden))}


def suggesties(pad: Path, mime: str, prive_termen: list[str]) -> list[dict]:
    """Voorstellen voor balken, uit de tekstlaag of (bij foto's en scans) via OCR.

    Jouw eigen gegevens (Instellingen) staan direct aan. Patronen (postcode, e-mail, ...) raken vaak ook
    de garage zelf; die staan daarom uit zodra je eigen gegevens hebt ingesteld, anders aan.
    """
    patronen_aan = not prive_termen
    # eigen gegevens: hoofdletters en aantal spaties mogen verschillen (PDF en OCR doen dat anders)
    eigen = [re.compile(r"\s+".join(map(re.escape, t.split())), re.IGNORECASE) for t in prive_termen if t.split()]
    result = []
    for nr, woorden in enumerate(woorden_per_pagina(pad, mime)):
        per_regel: dict = {}
        for w in woorden:
            per_regel.setdefault(w["regel"], []).append(w)
        vorige = None
        for sleutel in sorted(per_regel):
            regel = per_regel[sleutel]
            tekst, posities = "", []
            for w in regel:
                posities.append((len(tekst), len(tekst) + len(w["tekst"]), w))
                tekst += w["tekst"] + " "

            def geraakt(m, posities=posities):
                return [w for start, eind, w in posities if start < m.end() and eind > m.start()]

            for patroon in eigen:
                for m in patroon.finditer(tekst):
                    result.append(_box(geraakt(m), nr, "jouw gegevens", True))
            for reden, patroon in PATRONEN.items():
                for m in patroon.finditer(tekst):
                    result.append(_box(geraakt(m), nr, reden, patronen_aan))
                    # straat + huisnummer staat meestal op de regel boven de postcode
                    if reden == "postcode" and vorige and vorige[0][0] == sleutel[0] \
                            and any(c.isdigit() for w in vorige[1] for c in w["tekst"]):
                        result.append(_box(vorige[1], nr, "adres", patronen_aan))
            vorige = (sleutel, regel)
    return result


def toepassen(origineel: Path, mime: str, boxen: list[dict], doel: Path) -> None:
    """Schrijft een gelakte kopie naar `doel`. Het origineel blijft onaangeraakt."""
    if mime == "application/pdf":
        with _open_pdf(origineel) as doc:
            for b in boxen:
                pagina = doc[int(b["pagina"])]
                r = pagina.rect
                pagina.add_redact_annot(pymupdf.Rect(r.x0 + b["x0"] * r.width, r.y0 + b["y0"] * r.height,
                                                     r.x0 + b["x1"] * r.width, r.y0 + b["y1"] * r.height),
                                        fill=(0, 0, 0))
            for pagina in doc:
                # alles buiten het zichtbare deel (buiten de cropbox) ook weg: dat zie je niet in het lakscherm
                r, groot = pagina.rect, 1e5
                for strook in (pymupdf.Rect(-groot, -groot, groot, r.y0), pymupdf.Rect(-groot, r.y1, groot, groot),
                               pymupdf.Rect(-groot, r.y0, r.x0, r.y1), pymupdf.Rect(r.x1, r.y0, groot, r.y1)):
                    pagina.add_redact_annot(strook, fill=False)
                pagina.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
                pagina.set_mediabox(pagina.cropbox)
            doc.scrub()  # metadata, verborgen tekst, bijlagen e.d. weg
            doc.save(doel, garbage=4, deflate=True)
        return

    im = _open_afbeelding(origineel)
    im = im.convert("RGB") if mime == "image/jpeg" else im.convert("RGBA")
    teken = ImageDraw.Draw(im)
    w, h = im.size
    for b in boxen:
        teken.rectangle([b["x0"] * w, b["y0"] * h, b["x1"] * w, b["y1"] * h], fill="black")
    # opnieuw opslaan zonder EXIF/commentaar: daar kunnen GPS-locatie en toestelgegevens in staan
    im.info.clear()
    im.save(doel, "JPEG", quality=92) if mime == "image/jpeg" else im.save(doel, "PNG")


# een bedrag als los woord: 54,00 / 1.234,56 / €72,50 / 54.00 / -15,00 / 54,- ; ook losse €/EUR-tekens
BEDRAG_WOORD = re.compile(r"(€|eur|euro)?[:]?-?(€)?(\d{1,3}(\.\d{3})+|\d+)([,.]\d{1,2}|,-)-?[;:,.)]?"  # 1.250,00 1250,00 72,5
                          r"|(€|eur)-?\d+[;:,.)]?"                                                # €72
                          r"|€|eur|euro|euro's", re.IGNORECASE)


def bedragen_weglakken(pad, mime: str) -> tuple[bytes, str] | None:
    """Kopie van een factuur met alle bedragen echt verwijderd (voor een uitdraai zonder kosten).
    Geeft (inhoud, mime-type) terug, of None als er geen tekst te vinden is (dan kan het niet veilig)."""
    woorden = woorden_per_pagina(pad, mime)
    if not woorden or not all(woorden):  # één pagina zonder herkende tekst = daar kunnen bedragen op staan
        return None
    boxen = [(nr, w) for nr, ws in enumerate(woorden) for w in ws if BEDRAG_WOORD.fullmatch(w["tekst"].strip())]
    if mime != "application/pdf":
        im = _open_afbeelding(pad).convert("RGB")
        teken = ImageDraw.Draw(im)
        for _, w in boxen:
            teken.rectangle([(w["x0"] - MARGE) * im.width, w["y0"] * im.height,
                             (w["x1"] + MARGE) * im.width, w["y1"] * im.height], fill="black")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue(), "image/png"
    with _open_pdf(pad) as doc:
        for nr, w in boxen:
            r = doc[nr].rect
            # alleen het midden van de regelhoogte: raakt het bedrag zelf, niet de (overlappende) buurregels
            hoogte = w["y1"] - w["y0"]
            doc[nr].add_redact_annot(pymupdf.Rect(r.x0 + (w["x0"] - MARGE) * r.width, r.y0 + (w["y0"] + hoogte * .25) * r.height,
                                                  r.x0 + (w["x1"] + MARGE) * r.width, r.y0 + (w["y1"] - hoogte * .25) * r.height),
                                     fill=(0, 0, 0))
        for p in doc:
            p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)  # ook de pixels bij gescande pagina's
        doc.scrub()
        return doc.tobytes(garbage=4, deflate=True), "application/pdf"
