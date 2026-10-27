"""Read an invoice locally: date, total amount, odometer reading, garage and work done.

Works on the text layer of PDFs; for photos and scans via the built-in Windows OCR.
Everything is a suggestion; the form lets the user check it.
The patterns match Dutch invoices, so they contain Dutch words.
"""
import io
import re
from datetime import date

import pymupdf
from PIL import Image, ImageOps

from . import ocr

MONTHS = {m: i for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november december".split(), start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["mrt"] = 3

DATE_NUM = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")
DATE_TEXT = re.compile(r"\b(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})\b", re.IGNORECASE)
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# dates that are not the invoice date
NOT_INVOICE_DATE = re.compile(r"verval|uiterlijk|betalen v[oó]{2}r|toelating|lever|apk|geldig|volgende|geboorte", re.IGNORECASE)

AMOUNT = re.compile(r"(?:€|eur\b|euro\b)?[ \t]*(-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2})(?!\d)", re.IGNORECASE)
TOTAL = re.compile(r"\btotaal|\btotal\b|te betalen|totaalbedrag|te voldoen", re.IGNORECASE)
NOT_TOTAL = re.compile(r"sub|excl|uur|arbeid|onderdelen|materia|korting|aanbetaling|te late|rente|kosten van"
                       r"|^(?!.*incl).*\b(btw|vat)\b", re.IGNORECASE)  # VAT line, except 'totaal incl. btw'
COLUMN_HEADERS = re.compile(r"omschrijving|aantal|prijs|artikel|stuks|eenheid", re.IGNORECASE)

KM_LABEL = re.compile(r"(?<![a-z])(?:kilometer[ \t]?stand|km[ \t.\-]?stand|tellerstand|stand[ \t]teller|km)[ \t]*[:.]?[ \t]*"
                      r"(\d{1,3}(?:\.\d{3})+|\d{2,7})(?![\d,])", re.IGNORECASE)
KM_AFTER = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d{3,7})[ \t]*km\b", re.IGNORECASE)
NOT_KM = re.compile(r"volgende|binnen|over |haal|breng|afstand|advies|max", re.IGNORECASE)

COMPANY = re.compile(r"\bb\.?v\.?(?:\s|$)|\bv\.o\.f\.|\bgarage|garage\b|\bmotor|\bauto(?!mat)|\bservice\b|\bdealer\b"
                     r"|\btweewieler|\bbanden\b|\bscooter", re.IGNORECASE)
NOT_COMPANY = re.compile(r"klant|^aan\b|t\.a\.v|factuur aan|debiteur", re.IGNORECASE)

# lines with advice or planning: not done
NOT_WORK = re.compile(r"volgende|advies|geadviseerd|aanbevolen|geldig tot|vervaldatum|binnenkort", re.IGNORECASE)
FRONT, REAR = r"v[oó]{2}r", r"achter"
# default type name -> patterns per line (part), lower case
KEYWORDS = {
    "Oliefilter vervangen": [r"olie[ -]?filter", r"oil ?filter"],
    "Olie vervangen": [r"(?<![a-z])(motor)?olie\b(?![ -]?(peil|filter|lek|niveau|druk))", r"\b\d{1,2}w-?\d{2}\b",
                       r"olie verversen"],
    "Luchtfilter vervangen": [r"lucht[ -]?filter", r"air ?filter"],
    "Bougies vervangen": [r"bougie"],
    "Remvloeistof vervangen": [r"remvloeistof", r"\bdot ?[345]\b", r"remolie"],
    "Koelvloeistof vervangen": [r"koelvloeistof", r"antivries", r"coolant"],
    "Remblokken voor vervangen": [rf"remblok\w*[^;,]{{0,30}}\b{FRONT}\b", rf"\b{FRONT}\b[^;,]{{0,30}}remblok"],
    "Remblokken achter vervangen": [rf"remblok\w*[^;,]{{0,30}}\b{REAR}\b", rf"\b{REAR}\b[^;,]{{0,30}}remblok"],
    "Banden vervangen": [r"(?<![a-z])(v[oó]{2}r|achter|binnen|buiten)?band(en)?\b",
                         r"\b(michelin|pirelli|metzeler|dunlop|bridgestone|continental|avon)\b"],
    "Accu vervangen": [r"\baccu\b", r"\bbatterij\b"],
    "Kleine beurt": [r"kleine (onderhouds)?beurt"],
    "Grote beurt": [r"grote (onderhouds)?beurt"],
    "Ketting smeren/spannen": [r"ketting (smeren|spannen|reinigen)", r"kettingspray"],
    "Ketting + tandwielen vervangen": [r"kettingset", r"kettingkit", r"tandwiel", r"ketting vervangen"],
    "Kleppen stellen": [r"klep(pen)? ?(stellen|speling)", r"klepspeling"],
    "APK": [r"\bapk\b"],
    "Distributieriem vervangen": [r"distributieriem", r"distributie(set|kit)", r"tandriem"],
    "Interieurfilter vervangen": [r"interieurfilter", r"pollenfilter", r"cabinefilter"],
    "Ruitenwissers vervangen": [r"ruitenwisser(?!vloeistof|sproeier)", r"wisserblad"],
    "Airco-service": [r"airco"],
}


def _text(content: bytes) -> str:
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        doc.bake(annots=True, widgets=True)
        return "\n".join(p.get_text() for p in doc)


def _ocr_words(content: bytes, mime: str) -> list[list[dict]]:
    if mime != "application/pdf":
        with Image.open(io.BytesIO(content)) as im:
            return [ocr.recognize(ImageOps.exif_transpose(im))]
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        return [ocr.page(p) for p in list(doc)[:5]]  # invoices are short; limit the waiting time


def _lines(text: str) -> list[str]:
    return [r.strip() for r in text.splitlines() if r.strip()]


def _dates_in(line: str):
    for m in DATE_NUM.finditer(line):
        d, month, y = (int(x) for x in m.groups())
        yield y + 2000 if y < 100 else y, month, d
    for m in DATE_TEXT.finditer(line):
        if month := MONTHS.get(m.group(2).lower()):
            yield int(m.group(3)), month, int(m.group(1))
    for m in DATE_ISO.finditer(line):
        yield tuple(int(x) for x in m.groups())


def _date(lines: list[str]) -> str | None:
    candidates = []  # (preference, position, date)
    for i, line in enumerate(lines):
        if NOT_INVOICE_DATE.search(line):
            continue
        previous = lines[i - 1].lower() if i else ""
        low = line.lower()
        if "factuurdatum" in low or (previous.rstrip(":") in ("factuurdatum", "datum") and not NOT_INVOICE_DATE.search(previous)):
            preference = 0  # also when the label is on the line before (separate text blocks)
        elif re.search(r"\bdatum\b|\bdate\b", low):
            preference = 1
        else:
            preference = 2
        for y, month, d in _dates_in(line):
            candidates.append((preference, i, (y, month, d)))
    for _, _, (y, month, d) in sorted(candidates):
        try:
            found = date(y, month, d)
        except ValueError:
            continue
        if date(1980, 1, 1) <= found <= date.today():  # an invoice is not in the future
            return found.isoformat()
    return None


def _amount_to_float(s: str) -> float:
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _amounts(line: str) -> list[float]:
    return [_amount_to_float(a) for a in AMOUNT.findall(line)]


def _cost(lines: list[str]) -> float | None:
    candidates = []
    for i, line in enumerate(lines):
        if not TOTAL.search(line) or NOT_TOTAL.search(line) or len(COLUMN_HEADERS.findall(line)) >= 2:
            continue
        amounts = _amounts(line)
        if not amounts and i + 1 < len(lines) and len(line) <= 40:
            # label and amount in separate text blocks: the amount is on the next line
            following = lines[i + 1]
            if not NOT_TOTAL.search(following) and not re.search(r"[a-z]{4,}", following, re.IGNORECASE):
                amounts = _amounts(following)
        candidates += [a for a in amounts if a > 0]
    if candidates:
        return max(candidates)  # the total incl. VAT is the highest 'total'
    every = [a for line in lines for a in _amounts(line) if a > 0]
    return max(every) if every else None


def _km(lines: list[str]) -> int | None:
    for pattern in (KM_LABEL, KM_AFTER):
        for line in lines:
            if NOT_KM.search(line):
                continue
            for m in pattern.finditer(line):
                value = int(m.group(1).replace(".", ""))
                # not a postcode (1234 AB) and a realistic reading
                if 10 <= value <= 2_000_000 and not re.match(r"[ \t]?[A-Z]{2}\b", line[m.end():]):
                    return value
    return None


def _company(lines: list[str]) -> str | None:
    for line in lines[:8]:
        if 3 <= len(line) <= 60 and COMPANY.search(line) and not NOT_COMPANY.search(line) \
                and not re.search(r"\d{4}\s?[A-Z]{2}|@", line):
            return line
    return None


def _types(lines: list[str], types) -> list[int]:
    # search per line part, so 'uitlijnen voor; remblokken achter' does not hit both
    parts = [p.strip().lower() for line in lines if not NOT_WORK.search(line) for p in re.split(r"[;|]", line)]
    found = []
    for t in types:
        if t["name"] == "Overig":
            continue
        patterns = KEYWORDS.get(t["name"], []) + [rf"(?<![a-z]){re.escape(t['name'].lower())}\b"]
        if any(re.search(p, part) for p in patterns for part in parts):
            found.append(t["id"])
    return found


def read(content: bytes, mime: str, types) -> dict:
    """`types`: the maintenance types of the vehicle (rows with id and name)."""
    via_ocr = False
    try:
        text = _text(content) if mime == "application/pdf" else ""
        if len(text.strip()) < 5:  # photo or scan without a text layer: Windows OCR
            text, via_ocr = "\n".join(ocr.lines(_ocr_words(content, mime))), True
    except Exception:  # noqa: BLE001 - broken file: then just suggest nothing
        return {"readable": False}
    if len(text.strip()) < 5:
        return {"readable": False}
    lines = _lines(text)
    return {"readable": True, "via_ocr": via_ocr, "date": _date(lines), "cost": _cost(lines), "odometer_km": _km(lines),
            "performed_by": _company(lines), "types": _types(lines, types)}


def combine(results: list[dict]) -> dict:
    """Several invoices for one service: add up costs, merge types, latest date, highest km."""
    readable = [r for r in results if r.get("readable")]
    if not readable:
        return {"readable": False}
    costs = [r["cost"] for r in readable if r["cost"] is not None]
    dates = [r["date"] for r in readable if r["date"]]
    kms = [r["odometer_km"] for r in readable if r["odometer_km"]]
    return {
        "readable": True,
        "unreadable": len(results) - len(readable),
        "date": max(dates) if dates else None,
        "cost": round(sum(costs), 2) if costs else None,
        "odometer_km": max(kms) if kms else None,
        "performed_by": next((r["performed_by"] for r in readable if r["performed_by"]), None),
        "types": list(dict.fromkeys(t for r in readable for t in r["types"])),
    }
