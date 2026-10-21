"""Factuur lokaal uitlezen: datum, totaalbedrag, km-stand, garage en uitgevoerde werkzaamheden.

Werkt op de tekstlaag van PDF's; bij foto's en scans via de ingebouwde Windows-OCR.
Alles is een voorstel; het formulier laat de gebruiker het controleren.
"""
import re
from datetime import date

import io

import pymupdf
from PIL import Image, ImageOps

from . import ocr

MAANDEN = {m: i for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november december".split(), start=1)}
MAANDEN.update({m[:3]: i for m, i in list(MAANDEN.items())})
MAANDEN["mrt"] = 3

DATUM_NUM = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")
DATUM_TEKST = re.compile(r"\b(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})\b", re.IGNORECASE)
DATUM_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# datums die niet de factuurdatum zijn
GEEN_FACTUURDATUM = re.compile(r"verval|uiterlijk|betalen v[oó]{2}r|toelating|lever|apk|geldig|volgende|geboorte", re.IGNORECASE)

BEDRAG = re.compile(r"(?:€|eur\b|euro\b)?[ \t]*(-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2})(?!\d)", re.IGNORECASE)
TOTAAL = re.compile(r"\btotaal|\btotal\b|te betalen|totaalbedrag|te voldoen", re.IGNORECASE)
GEEN_TOTAAL = re.compile(r"sub|excl|uur|arbeid|onderdelen|materia|korting|aanbetaling|te late|rente|kosten van"
                         r"|^(?!.*incl).*\b(btw|vat)\b", re.IGNORECASE)  # btw-regel, behalve 'totaal incl. btw'
KOLOMKOPPEN = re.compile(r"omschrijving|aantal|prijs|artikel|stuks|eenheid", re.IGNORECASE)

KM_LABEL = re.compile(r"(?<![a-z])(?:kilometer[ \t]?stand|km[ \t.\-]?stand|tellerstand|stand[ \t]teller|km)[ \t]*[:.]?[ \t]*"
                      r"(\d{1,3}(?:\.\d{3})+|\d{2,7})(?![\d,])", re.IGNORECASE)
KM_ACHTER = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d{3,7})[ \t]*km\b", re.IGNORECASE)
GEEN_KM = re.compile(r"volgende|binnen|over |haal|breng|afstand|advies|max", re.IGNORECASE)

BEDRIJF = re.compile(r"\bb\.?v\.?(?:\s|$)|\bv\.o\.f\.|\bgarage|garage\b|\bmotor|\bauto(?!mat)|\bservice\b|\bdealer\b"
                     r"|\btweewieler|\bbanden\b|\bscooter", re.IGNORECASE)
GEEN_BEDRIJF = re.compile(r"klant|^aan\b|t\.a\.v|factuur aan|debiteur", re.IGNORECASE)

# regels met advies of planning: niet uitgevoerd
GEEN_WERK = re.compile(r"volgende|advies|geadviseerd|aanbevolen|geldig tot|vervaldatum|binnenkort", re.IGNORECASE)
VOOR, ACHTER = r"v[oó]{2}r", r"achter"
# standaard type-naam -> patronen per regel(deel), in kleine letters
TREFWOORDEN = {
    "Oliefilter vervangen": [r"olie[ -]?filter", r"oil ?filter"],
    "Olie vervangen": [r"(?<![a-z])(motor)?olie\b(?![ -]?(peil|filter|lek|niveau|druk))", r"\b\d{1,2}w-?\d{2}\b",
                       r"olie verversen"],
    "Luchtfilter vervangen": [r"lucht[ -]?filter", r"air ?filter"],
    "Bougies vervangen": [r"bougie"],
    "Remvloeistof vervangen": [r"remvloeistof", r"\bdot ?[345]\b", r"remolie"],
    "Koelvloeistof vervangen": [r"koelvloeistof", r"antivries", r"coolant"],
    "Remblokken voor vervangen": [rf"remblok\w*[^;,]{{0,30}}\b{VOOR}\b", rf"\b{VOOR}\b[^;,]{{0,30}}remblok"],
    "Remblokken achter vervangen": [rf"remblok\w*[^;,]{{0,30}}\b{ACHTER}\b", rf"\b{ACHTER}\b[^;,]{{0,30}}remblok"],
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


def _tekst(inhoud: bytes) -> str:
    with pymupdf.open(stream=inhoud, filetype="pdf") as doc:
        doc.bake(annots=True, widgets=True)
        return "\n".join(p.get_text() for p in doc)


def _ocr_woorden(inhoud: bytes, mime: str) -> list[list[dict]]:
    if mime != "application/pdf":
        with Image.open(io.BytesIO(inhoud)) as im:
            return [ocr.herken(ImageOps.exif_transpose(im))]
    with pymupdf.open(stream=inhoud, filetype="pdf") as doc:
        return [ocr.pagina(p) for p in list(doc)[:5]]  # facturen zijn kort; begrens de wachttijd


def _regels(tekst: str) -> list[str]:
    return [r.strip() for r in tekst.splitlines() if r.strip()]


def _datums_in(regel: str):
    for m in DATUM_NUM.finditer(regel):
        d, mnd, j = (int(x) for x in m.groups())
        yield j + 2000 if j < 100 else j, mnd, d
    for m in DATUM_TEKST.finditer(regel):
        if mnd := MAANDEN.get(m.group(2).lower()):
            yield int(m.group(3)), mnd, int(m.group(1))
    for m in DATUM_ISO.finditer(regel):
        yield tuple(int(x) for x in m.groups())


def _datum(regels: list[str]) -> str | None:
    kandidaten = []  # (voorkeur, positie, datum)
    for i, regel in enumerate(regels):
        if GEEN_FACTUURDATUM.search(regel):
            continue
        vorige = regels[i - 1].lower() if i else ""
        laag = regel.lower()
        if "factuurdatum" in laag or (vorige.rstrip(":") in ("factuurdatum", "datum") and not GEEN_FACTUURDATUM.search(vorige)):
            voorkeur = 0  # ook als het label op de regel ervoor staat (aparte tekstblokken)
        elif re.search(r"\bdatum\b|\bdate\b", laag):
            voorkeur = 1
        else:
            voorkeur = 2
        for j, mnd, d in _datums_in(regel):
            kandidaten.append((voorkeur, i, (j, mnd, d)))
    for _, _, (j, mnd, d) in sorted(kandidaten):
        try:
            datum = date(j, mnd, d)
        except ValueError:
            continue
        if date(1980, 1, 1) <= datum <= date.today():  # een factuur ligt niet in de toekomst
            return datum.isoformat()
    return None


def _bedrag_naar_float(s: str) -> float:
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _bedragen(regel: str) -> list[float]:
    return [_bedrag_naar_float(b) for b in BEDRAG.findall(regel)]


def _kosten(regels: list[str]) -> float | None:
    kandidaten = []
    for i, regel in enumerate(regels):
        if not TOTAAL.search(regel) or GEEN_TOTAAL.search(regel) or len(KOLOMKOPPEN.findall(regel)) >= 2:
            continue
        bedragen = _bedragen(regel)
        if not bedragen and i + 1 < len(regels) and len(regel) <= 40:
            # label en bedrag in aparte tekstblokken: bedrag staat op de volgende regel
            volgende = regels[i + 1]
            if not GEEN_TOTAAL.search(volgende) and not re.search(r"[a-z]{4,}", volgende, re.IGNORECASE):
                bedragen = _bedragen(volgende)
        kandidaten += [b for b in bedragen if b > 0]
    if kandidaten:
        return max(kandidaten)  # totaal incl. btw is het hoogste 'totaal'
    alle = [b for r in regels for b in _bedragen(r) if b > 0]
    return max(alle) if alle else None


def _km(regels: list[str]) -> int | None:
    for patroon in (KM_LABEL, KM_ACHTER):
        for regel in regels:
            if GEEN_KM.search(regel):
                continue
            for m in patroon.finditer(regel):
                waarde = int(m.group(1).replace(".", ""))
                # geen postcode (1234 AB) en een realistische stand
                if 10 <= waarde <= 2_000_000 and not re.match(r"[ \t]?[A-Z]{2}\b", regel[m.end():]):
                    return waarde
    return None


def _bedrijf(regels: list[str]) -> str | None:
    for regel in regels[:8]:
        if 3 <= len(regel) <= 60 and BEDRIJF.search(regel) and not GEEN_BEDRIJF.search(regel) \
                and not re.search(r"\d{4}\s?[A-Z]{2}|@", regel):
            return regel
    return None


def _types(regels: list[str], types) -> list[int]:
    # per regeldeel zoeken, zodat 'uitlijnen voor; remblokken achter' niet allebei raakt
    delen = [d.strip().lower() for r in regels if not GEEN_WERK.search(r) for d in re.split(r"[;|]", r)]
    gevonden = []
    for t in types:
        if t["naam"] == "Overig":
            continue
        patronen = TREFWOORDEN.get(t["naam"], []) + [rf"(?<![a-z]){re.escape(t['naam'].lower())}\b"]
        if any(re.search(p, d) for p in patronen for d in delen):
            gevonden.append(t["id"])
    return gevonden


def lees(inhoud: bytes, mime: str, types) -> dict:
    """`types`: de onderhoudstypes die bij het voertuig horen (rijen met id en naam)."""
    via_ocr = False
    try:
        tekst = _tekst(inhoud) if mime == "application/pdf" else ""
        if len(tekst.strip()) < 5:  # foto of scan zonder tekstlaag: Windows-OCR
            tekst, via_ocr = "\n".join(ocr.regels(_ocr_woorden(inhoud, mime))), True
    except Exception:  # noqa: BLE001 - kapot bestand: dan gewoon niets voorstellen
        return {"leesbaar": False}
    if len(tekst.strip()) < 5:
        return {"leesbaar": False}
    regels = _regels(tekst)
    return {"leesbaar": True, "via_ocr": via_ocr, "datum": _datum(regels), "kosten": _kosten(regels), "kilometerstand": _km(regels),
            "uitgevoerd_door": _bedrijf(regels), "types": _types(regels, types)}


def combineer(resultaten: list[dict]) -> dict:
    """Meerdere facturen voor één beurt: kosten optellen, types samenvoegen, laatste datum, hoogste km."""
    leesbaar = [r for r in resultaten if r.get("leesbaar")]
    if not leesbaar:
        return {"leesbaar": False}
    kosten = [r["kosten"] for r in leesbaar if r["kosten"] is not None]
    datums = [r["datum"] for r in leesbaar if r["datum"]]
    kms = [r["kilometerstand"] for r in leesbaar if r["kilometerstand"]]
    return {
        "leesbaar": True,
        "onleesbaar": len(resultaten) - len(leesbaar),
        "datum": max(datums) if datums else None,
        "kosten": round(sum(kosten), 2) if kosten else None,
        "kilometerstand": max(kms) if kms else None,
        "uitgevoerd_door": next((r["uitgevoerd_door"] for r in leesbaar if r["uitgevoerd_door"]), None),
        "types": list(dict.fromkeys(t for r in leesbaar for t in r["types"])),
    }
