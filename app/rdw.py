"""Vehicle data from RDW Open Data (dataset Gekentekende voertuigen)."""
import re

import httpx

URL = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"
URL_FUEL = "https://opendata.rdw.nl/resource/8ys7-d773.json"


class RdwError(Exception):
    pass


def normalize(plate: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", plate.upper())


def _date(value: str | None) -> str | None:
    """'20240122' -> '2024-01-22'."""
    return f"{value[:4]}-{value[4:6]}-{value[6:]}" if value and re.fullmatch(r"\d{8}", value) else None


def _number(value, kind=int):
    try:
        return kind(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def _fuel(c: httpx.Client, plate: str) -> dict:
    """Fuel and power; not essential, so empty on any error."""
    try:
        resp = c.get(URL_FUEL, params={"kenteken": plate})
        resp.raise_for_status()
        rows = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(rows, list) or not rows:
        return {}
    kinds = [r.get("brandstof_omschrijving") for r in rows if r.get("brandstof_omschrijving")]
    kw = [_number(r.get("nettomaximumvermogen"), float) or _number(r.get("netto_max_vermogen_elektrisch"), float) for r in rows]
    kw = [k for k in kw if k]
    return {"fuel": " + ".join(dict.fromkeys(kinds)) or None, "power_kw": round(max(kw)) if kw else None}


def lookup(plate: str, client: httpx.Client | None = None) -> dict:
    plate = normalize(plate)
    if not plate:
        raise RdwError("Vul een kenteken in.")
    try:
        with client or httpx.Client(timeout=10) as c:
            resp = c.get(URL, params={"kenteken": plate})
            resp.raise_for_status()
            data = resp.json()
            extra = _fuel(c, plate) if data else {}
    except (httpx.HTTPError, ValueError):
        raise RdwError("RDW is niet bereikbaar. Controleer je internet of vul de gegevens zelf in.")
    if not data:
        raise RdwError(f"Kenteken {plate} niet gevonden bij de RDW.")

    r = data[0]  # RDW field names are Dutch
    first_registration = r.get("datum_eerste_toelating", "")  # format YYYYMMDD
    if re.fullmatch(r"\d{8}", first_registration):
        first_registration = f"{first_registration[:4]}-{first_registration[4:6]}-{first_registration[6:]}"
    displacement = r.get("cilinderinhoud")
    color = r.get("eerste_kleur", "")
    if color.upper() in ("N.V.T.", "NIET GEREGISTREERD"):
        color = ""
    return {
        "license_plate": plate,
        "vehicle_type": r.get("voertuigsoort", ""),
        "make": r.get("merk", "").title(),
        "model": r.get("handelsbenaming", ""),
        "first_registration_date": first_registration,
        "color": color.capitalize(),
        "displacement_cc": int(displacement) if displacement and displacement.isdigit() else None,
        "rdw": {
            "apk_expiry_date": _date(r.get("vervaldatum_apk")),
            "open_recall": r.get("openstaande_terugroepactie_indicator") == "Ja",
            "odometer_judgement": r.get("tellerstandoordeel"),
            "odometer_year": _number(r.get("jaar_laatste_registratie_tellerstand")),
            "list_price": _number(r.get("catalogusprijs")),
            "first_registration_nl_date": _date(r.get("datum_eerste_tenaamstelling_in_nederland")),
            "curb_weight_kg": _number(r.get("massa_rijklaar")),
            **extra,
        },
    }
