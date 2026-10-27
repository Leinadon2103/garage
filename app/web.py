"""Gedeelde webhulpmiddelen: templates, validatie van invoer en uploads, bestanden opruimen."""
import io
import logging
import re
from datetime import date
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import db

lazy from . import export_pdf  # reportlab/pypdf pas laden bij de eerste upload of export (sneller opstarten)

HIER = Path(__file__).resolve().parent
# extensie -> (mime-type, eerste bytes van het bestand)
TOEGESTAAN = {
    ".pdf": ("application/pdf", b"%PDF"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".png": ("image/png", b"\x89PNG"),
}
VOERTUIGSOORTEN = ["Motorfiets", "Bromfiets", "Personenauto", "Bedrijfsauto", "Aanhangwagen", "Overig"]


def euro(bedrag) -> str:
    return "" if bedrag is None else f"€ {bedrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


templates = Jinja2Templates(directory=HIER / "templates")
templates.env.filters["km"] = lambda v: "" if v is None else f"{v:,}".replace(",", ".")
templates.env.filters["euro"] = euro
templates.env.filters["nl_datum"] = lambda d: (date.fromisoformat(d) if isinstance(d, str) else d).strftime("%d-%m-%Y") if d else ""
templates.env.globals["icon"] = lambda naam, klasse="": Markup(  # format() escapet naam en klasse
    '<svg class="i {}" aria-hidden="true"><use href="/static/icons.svg#i-{}"/></svg>').format(klasse, naam)
templates.env.globals["soort_icoon"] = {"Motorfiets": "bike", "Bromfiets": "bike", "Personenauto": "car",
                                        "Bedrijfsauto": "truck", "Aanhangwagen": "truck"}


def kenteken_opmaak(k: str) -> str:
    """'00MDB8' -> '00-MDB-8'; streepjes tussen letter- en cijfergroepen zoals op de NL-plaat."""
    groepen = re.findall(r"[A-Z]+|\d+", k or "")
    if len(groepen) == 2 and len(k) == 6:  # bijv. 99-XX-XX of XX-XX-99
        groepen = [g[i:i + 2] for g in groepen for i in range(0, len(g), 2)]
    return "-".join(groepen) if 2 <= len(groepen) <= 3 else k


templates.env.filters["plaat"] = kenteken_opmaak
MAANDEN = "jan feb mrt apr mei jun jul aug sep okt nov dec".split()
templates.env.filters["kort_datum"] = lambda d: (lambda x: f"{x.day} {MAANDEN[x.month - 1]} {x.year}")(
    date.fromisoformat(d) if isinstance(d, str) else d) if d else ""


def render(request: Request, naam: str, status_code: int = 200, **ctx):
    try:
        with db.connect() as conn:
            thema = db.instelling(conn, "thema", "systeem")
            ctx.setdefault("thema", thema if thema in ("licht", "donker", "systeem") else "systeem")
    except Exception:  # noqa: BLE001 - bijv. database op slot: gewoon de standaardweergave
        ctx.setdefault("thema", "systeem")
    return templates.TemplateResponse(request, naam, ctx, status_code=status_code)


def terug(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


TABELLEN = {"voertuig", "beurt", "factuur", "document", "km_stand", "stalling"}


def haal(conn, tabel: str, id_: int):
    if tabel not in TABELLEN:  # tabelnaam kan geen ?-parameter zijn; daarom alleen vaste namen
        raise ValueError(f"Onbekende tabel: {tabel}")
    rij = conn.execute(f"SELECT * FROM {tabel} WHERE id = ?", (id_,)).fetchone()  # nosec B608
    if not rij:
        raise HTTPException(404, f"{tabel} niet gevonden")
    return rij


def mime_voor(pad: Path) -> str:
    """Mime-type uit de extensie, nooit uit de database: een teruggezette back-up kan daar bijv. text/html in
    zetten, en dan zou een 'factuur' als webpagina binnen Garage draaien."""
    if pad.suffix.lower() not in TOEGESTAAN:
        raise HTTPException(404, "Onbekend bestandstype")
    return TOEGESTAAN[pad.suffix.lower()][0]


def getal(waarde: str | None) -> int | None:
    """'12.000' of '12000' -> 12000; leeg -> None; anders ValueError."""
    waarde = (waarde or "").strip().replace(".", "").replace(" ", "")
    if not waarde:
        return None
    if not waarde.isdigit():
        raise ValueError(f"'{waarde}' is geen geldig getal.")
    return int(waarde)


def bedrag(waarde: str | None) -> float | None:
    """Accepteert o.a. '45,50', '€ 1.250,00' en '45.50'; leeg -> None; anders ValueError."""
    waarde = (waarde or "").replace("€", "").replace(" ", "").strip()
    if not waarde:
        return None
    if "," in waarde:  # Nederlandse notatie: punt = duizendtal, komma = decimaal
        waarde = waarde.replace(".", "").replace(",", ".")
    try:
        return round(float(waarde), 2)
    except ValueError:
        raise ValueError(f"'{waarde}' is geen geldig bedrag.")


def _of_400(parser, waarde):
    try:
        return parser(waarde)
    except ValueError as e:
        raise HTTPException(400, str(e))


def verwijder_bestanden(paden) -> None:
    for pad in filter(None, paden):
        try:
            db.bestand(pad).unlink(missing_ok=True)
        except (OSError, ValueError):  # bijv. bestand nog open in een PDF-viewer; database is al bijgewerkt
            logging.warning("Kon %s niet verwijderen", pad)


def factuur_paden(rijen) -> list[str]:
    return [p for f in rijen for p in (f["pad"], f["origineel_pad"])]


async def lees_uploads(bestanden: list[UploadFile]) -> list[tuple[str, str, bytes]]:
    """Valideert uploads; geeft (originele naam, extensie, inhoud). Lege bestandsvelden worden genegeerd."""
    result = []
    for f in bestanden:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        inhoud = await f.read()
        if ext not in TOEGESTAAN or not inhoud.startswith(TOEGESTAAN[ext][1]):
            raise ValueError(f"'{f.filename}' is geen geldige PDF, JPG of PNG.")
        if ext == ".pdf":
            try:
                export_pdf.lees_pdf(io.BytesIO(inhoud))
            except Exception as e:  # noqa: BLE001 - pypdf gooit uiteenlopende fouten
                reden = str(e) if isinstance(e, ValueError) else "bestand is beschadigd"
                raise ValueError(f"'{f.filename}' kan niet gebruikt worden: {reden}.")
        result.append((Path(f.filename).name, ext, inhoud))
    return result
