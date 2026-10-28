"""Shared web helpers: templates, validating input and uploads, cleaning up files."""
import io
import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import db

lazy from . import export_pdf  # load reportlab/pypdf only at the first upload or export (faster startup)

HERE = Path(__file__).resolve().parent
# extension -> (mime type, first bytes of the file)
ALLOWED = {
    ".pdf": ("application/pdf", b"%PDF"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".png": ("image/png", b"\x89PNG"),
}
# shown in the app and used by the RDW, so Dutch
VEHICLE_TYPES = ["Motorfiets", "Bromfiets", "Personenauto", "Bedrijfsauto", "Aanhangwagen", "Overig"]


def euro(amount) -> str:
    return "" if amount is None else f"€ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["km"] = lambda v: "" if v is None else f"{v:,}".replace(",", ".")
templates.env.filters["euro"] = euro
templates.env.filters["nl_date"] = lambda d: (date.fromisoformat(d) if isinstance(d, str) else d).strftime("%d-%m-%Y") if d else ""
templates.env.globals["icon"] = lambda name, cls="": Markup(  # format() escapes name and cls
    '<svg class="i {}" aria-hidden="true"><use href="/static/icons.svg#i-{}"/></svg>').format(cls, name)
# file name in the url: a new image = a new url, so the browser never shows an old copy from its cache
templates.env.globals["image_url"] = lambda v: f"/vehicles/{v['id']}/image.jpg?v={Path(v['image_path']).stem}"
templates.env.globals["type_icon"] = {"Motorfiets": "bike", "Bromfiets": "bike", "Personenauto": "car",
                                      "Bedrijfsauto": "truck", "Aanhangwagen": "truck"}


def format_plate(plate: str) -> str:
    """'00MDB8' -> '00-MDB-8'; dashes between letter and digit groups, as on the Dutch plate."""
    groups = re.findall(r"[A-Z]+|\d+", plate or "")
    if len(groups) == 2 and len(plate) == 6:  # e.g. 99-XX-XX or XX-XX-99
        groups = [g[i:i + 2] for g in groups for i in range(0, len(g), 2)]
    return "-".join(groups) if 2 <= len(groups) <= 3 else plate


templates.env.filters["plate"] = format_plate
templates.env.filters["filename"] = lambda name: db.decrypt_text(name, "original_filename")  # stored encrypted
MONTHS = "jan feb mrt apr mei jun jul aug sep okt nov dec".split()
templates.env.filters["short_date"] = lambda d: (lambda x: f"{x.day} {MONTHS[x.month - 1]} {x.year}")(
    date.fromisoformat(d) if isinstance(d, str) else d) if d else ""


def render(request: Request, name: str, status_code: int = 200, **ctx):
    try:
        with db.connect() as conn:
            theme = db.setting(conn, "theme", "system")
            ctx.setdefault("theme", theme if theme in ("light", "dark", "system") else "system")
    except Exception:  # noqa: BLE001 - e.g. database locked: just the default look
        ctx.setdefault("theme", "system")
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


TABLES = {"vehicle", "service", "invoice", "document", "odometer_reading", "storage_period", "fuel_entry", "fixed_cost",
          "part"}


def fetch(conn, table: str, id_: int):
    if table not in TABLES:  # a table name cannot be a ?-parameter; so only fixed names
        raise ValueError(f"Unknown table: {table}")
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()  # nosec B608
    if not row:
        raise HTTPException(404, "Niet gevonden")
    return row


def mime_for(path: Path) -> str:
    """Mime type from the extension, never from the database: a restored backup could put e.g. text/html there,
    and then an 'invoice' would run as a web page inside Garage."""
    if path.suffix.lower() not in ALLOWED:
        raise HTTPException(404, "Onbekend bestandstype")
    return ALLOWED[path.suffix.lower()][0]


def file_response(rel: str | None, name: str | None, what: str = "Bestand") -> Response:
    """A data file (invoice, document), shown inline in the app window under its original (encrypted) name."""
    name = db.decrypt_text(name, "original_filename")
    try:
        content = db.read_file(rel)
    except ValueError:
        raise HTTPException(404, f"{what} niet gevonden")
    except FileNotFoundError:
        raise HTTPException(404, f"{what} ontbreekt op schijf")
    # filename* (RFC 5987): any name, also with quotes or non-ASCII characters, without breaking the header
    return Response(content, media_type=mime_for(Path(rel)),
                    headers={"Content-Disposition": f"inline; filename*=utf-8''{quote(name or Path(rel).name)}"})


def parse_int(value: str | None) -> int | None:
    """'12.000' or '12000' -> 12000; empty -> None; otherwise ValueError."""
    value = (value or "").strip().replace(".", "").replace(" ", "")
    if not value:
        return None
    if not value.isdigit():
        raise ValueError(f"'{value}' is geen geldig getal.")
    return int(value)


def parse_amount(value: str | None) -> float | None:
    """Accepts e.g. '45,50', '€ 1.250,00' and '45.50'; empty -> None; otherwise ValueError."""
    value = (value or "").replace("€", "").replace(" ", "").strip()
    if not value:
        return None
    if "," in value:  # Dutch notation: dot = thousands, comma = decimals
        value = value.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", value):  # '8.500' is eight and a half thousand here, not 8.5
        value = value.replace(".", "")
    try:
        return round(float(value), 2)
    except ValueError:
        raise ValueError(f"'{value}' is geen geldig bedrag.")


def or_400(parser, value):
    try:
        return parser(value)
    except ValueError as e:
        raise HTTPException(400, str(e))


def delete_files(paths, conn=None) -> None:
    """conn: when a write transaction is open on it (see db.write_file)."""
    for path in filter(None, paths):
        try:
            db.delete_file(path, conn)
        except (OSError, ValueError):  # e.g. file still open in a PDF viewer; the database is already updated
            logging.warning("Could not delete %s", path)


def invoice_paths(rows) -> list[str]:
    return [p for f in rows for p in (f["path"], f["original_path"])]


async def read_uploads(files: list[UploadFile]) -> list[tuple[str, str, bytes]]:
    """Validates uploads; returns (original name, extension, content). Empty file fields are ignored."""
    result = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        content = await f.read()
        if ext not in ALLOWED or not content.startswith(ALLOWED[ext][1]):
            raise ValueError(f"'{f.filename}' is geen geldige PDF, JPG of PNG.")
        if ext == ".pdf":
            try:
                export_pdf.read_pdf(io.BytesIO(content))
            except Exception as e:  # noqa: BLE001 - pypdf raises all kinds of errors
                reason = str(e) if isinstance(e, ValueError) else "bestand is beschadigd"
                raise ValueError(f"'{f.filename}' kan niet gebruikt worden: {reason}.")
        result.append((Path(f.filename).name, ext, content))
    return result
