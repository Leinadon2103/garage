"""Redacting personal data from invoices.

Boxes are fractions (0-1) of the page width/height, so the browser can draw them independent of the pixel
size. In PDFs the text under a box is really removed (PyMuPDF redaction), not just covered; in images the
pixels are blacked out and metadata (EXIF, including GPS) is dropped.

Everything works on file contents (bytes), never on a path: files are read and written through db.read_file
and db.write_file only.
"""
import io
import re

import pymupdf
from PIL import Image, ImageDraw, ImageOps

from . import ocr

WIDTH_PX = 1200  # render width for the redaction screen

# key = the reason shown in the app (Dutch)
PATTERNS = {
    "postcode": re.compile(r"\b\d{4}\s?[A-Z]{2}\b"),
    "e-mail": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "telefoon": re.compile(r"(?:\+31|0031|\b0)[\s-]?(?:\(0\))?[\s-]?\d(?:[\s-]?\d){8}\b"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z]{4}(?:\s?\d){10}\b"),
    "adres": re.compile(r"\b[A-Za-z][\w'.-]*(?:straat|weg|laan|plein|singel|gracht|kade|dijk|pad|hof|dreef|steeg|markt|ring|park)"
                        r"\s+\d+\s?[a-zA-Z]?\b", re.IGNORECASE),
}


class RedactionError(Exception):
    pass


def _open_pdf(content: bytes) -> pymupdf.Document:
    doc = pymupdf.open(stream=content, filetype="pdf")
    # turn annotations (including sticky notes with text in a popup) and form fields into normal page content:
    # then you see them on the redaction screen, the suggestions find them and they disappear under a box
    doc.bake(annots=True, widgets=True)
    for page in doc:
        if page.rotation:
            page.remove_rotation()  # after this, render and text coordinates are the same
    return doc


def _open_image(content: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(content)) as im:
            im = ImageOps.exif_transpose(im).copy()
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise RedactionError(f"Afbeelding kan niet worden geopend ({type(e).__name__}).")
    im.info.clear()  # do not keep EXIF/XMP/comments when saving
    return im


def page_count(content: bytes, mime: str) -> int:
    if mime != "application/pdf":
        return 1
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        return doc.page_count


def page_png(content: bytes, mime: str, nr: int) -> bytes:
    if mime == "application/pdf":
        with _open_pdf(content) as doc:
            page = doc[nr]
            zoom = min(WIDTH_PX / page.rect.width, 3)
            return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    im = _open_image(content)
    im.thumbnail((WIDTH_PX * 2, WIDTH_PX * 2))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


MARGIN = 0.003  # small margin left/right of each word, as a fraction of the page (not top/bottom:
                # word boxes are already line high and would otherwise touch the line above/below)


def words_per_page(content: bytes, mime: str) -> list[list[dict]]:
    """Per page the words with their box (fractions 0-1) and a line key.
    From the text layer of a PDF; for photos and scanned pages via Windows OCR."""
    result = []
    if mime != "application/pdf":
        return [ocr.recognize(_open_image(content))]
    with _open_pdf(content) as doc:
        for p in doc:
            r = p.rect
            text = p.get_text("words")
            result.append([{"text": w[4], "line": (w[5], w[6]),
                            "x0": (w[0] - r.x0) / r.width, "y0": (w[1] - r.y0) / r.height,
                            "x1": (w[2] - r.x0) / r.width, "y1": (w[3] - r.y0) / r.height} for w in text]
                          if text else ocr.page(p))
    return result


def _box(words: list[dict], nr: int, reason: str, active: bool) -> dict:
    return {"page": nr, "reason": reason, "active": active,
            "x0": max(0, min(w["x0"] for w in words) - MARGIN), "y0": max(0, min(w["y0"] for w in words)),
            "x1": min(1, max(w["x1"] for w in words) + MARGIN), "y1": min(1, max(w["y1"] for w in words))}


def suggestions(content: bytes, mime: str, private_terms: list[str]) -> list[dict]:
    """Proposed boxes, from the text layer or (for photos and scans) via OCR.

    Your own details (Settings) are on straight away. Patterns (postcode, e-mail, ...) often also hit the
    garage itself; so they are off once you have set your own details, otherwise on.
    """
    patterns_on = not private_terms
    # own details: case and number of spaces may differ (PDF and OCR do that differently)
    own = [re.compile(r"\s+".join(map(re.escape, t.split())), re.IGNORECASE) for t in private_terms if t.split()]
    result = []
    for nr, words in enumerate(words_per_page(content, mime)):
        per_line: dict = {}
        for w in words:
            per_line.setdefault(w["line"], []).append(w)
        previous = None
        for key in sorted(per_line):
            line = per_line[key]
            text, positions = "", []
            for w in line:
                positions.append((len(text), len(text) + len(w["text"]), w))
                text += w["text"] + " "

            def hit(m, positions=positions):
                return [w for start, end, w in positions if start < m.end() and end > m.start()]

            for pattern in own:
                for m in pattern.finditer(text):
                    result.append(_box(hit(m), nr, "jouw gegevens", True))
            for reason, pattern in PATTERNS.items():
                for m in pattern.finditer(text):
                    result.append(_box(hit(m), nr, reason, patterns_on))
                    # street + house number is usually on the line above the postcode
                    if reason == "postcode" and previous and previous[0][0] == key[0] \
                            and any(c.isdigit() for w in previous[1] for c in w["text"]):
                        result.append(_box(previous[1], nr, "adres", patterns_on))
            previous = (key, line)
    return result


def apply(original: bytes, mime: str, boxes: list[dict]) -> bytes:
    """A redacted copy of the original."""
    if mime == "application/pdf":
        with _open_pdf(original) as doc:
            for b in boxes:
                page = doc[int(b["page"])]
                r = page.rect
                page.add_redact_annot(pymupdf.Rect(r.x0 + b["x0"] * r.width, r.y0 + b["y0"] * r.height,
                                                   r.x0 + b["x1"] * r.width, r.y0 + b["y1"] * r.height),
                                      fill=(0, 0, 0))
            for page in doc:
                # everything outside the visible part (outside the cropbox) goes too: you don't see it on the screen
                r, big = page.rect, 1e5
                for strip in (pymupdf.Rect(-big, -big, big, r.y0), pymupdf.Rect(-big, r.y1, big, big),
                              pymupdf.Rect(-big, r.y0, r.x0, r.y1), pymupdf.Rect(r.x1, r.y0, big, r.y1)):
                    page.add_redact_annot(strip, fill=False)
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
                page.set_mediabox(page.cropbox)
            doc.scrub()  # remove metadata, hidden text, attachments etc.
            return doc.tobytes(garbage=4, deflate=True)

    im = _open_image(original)
    im = im.convert("RGB") if mime == "image/jpeg" else im.convert("RGBA")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    for b in boxes:
        draw.rectangle([b["x0"] * w, b["y0"] * h, b["x1"] * w, b["y1"] * h], fill="black")
    # save again without EXIF/comments: they can contain the GPS location and device details
    im.info.clear()
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92) if mime == "image/jpeg" else im.save(buf, "PNG")
    return buf.getvalue()


# an amount as a separate word: 54,00 / 1.234,56 / €72,50 / 54.00 / -15,00 / 54,- ; also separate €/EUR signs
AMOUNT_WORD = re.compile(r"(€|eur|euro)?[:]?-?(€)?(\d{1,3}(\.\d{3})+|\d+)([,.]\d{1,2}|,-)-?[;:,.)]?"  # 1.250,00 1250,00 72,5
                         r"|(€|eur)-?\d+[;:,.)]?"                                                # €72
                         r"|€|eur|euro|euro's", re.IGNORECASE)


def redact_amounts(content: bytes, mime: str) -> tuple[bytes, str] | None:
    """Copy of an invoice with all amounts really removed (for a printout without costs).
    Returns (content, mime type), or None if no text can be found (then it cannot be done safely)."""
    words = words_per_page(content, mime)
    if not words or not all(words):  # one page without recognized text = amounts could be on it
        return None
    boxes = [(nr, w) for nr, ws in enumerate(words) for w in ws if AMOUNT_WORD.fullmatch(w["text"].strip())]
    if mime != "application/pdf":
        im = _open_image(content).convert("RGB")
        draw = ImageDraw.Draw(im)
        for _, w in boxes:
            draw.rectangle([(w["x0"] - MARGIN) * im.width, w["y0"] * im.height,
                            (w["x1"] + MARGIN) * im.width, w["y1"] * im.height], fill="black")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue(), "image/png"
    with _open_pdf(content) as doc:
        for nr, w in boxes:
            r = doc[nr].rect
            # only the middle of the line height: hits the amount itself, not the (overlapping) neighbouring lines
            height = w["y1"] - w["y0"]
            doc[nr].add_redact_annot(pymupdf.Rect(r.x0 + (w["x0"] - MARGIN) * r.width, r.y0 + (w["y0"] + height * .25) * r.height,
                                                  r.x0 + (w["x1"] + MARGIN) * r.width, r.y0 + (w["y1"] - height * .25) * r.height),
                                     fill=(0, 0, 0))
        for p in doc:
            p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)  # also the pixels of scanned pages
        doc.scrub()
        return doc.tobytes(garbage=4, deflate=True), "application/pdf"
