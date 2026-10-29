"""Vehicle data from RDW Open Data (dataset Gekentekende voertuigen), with inspection defects and recalls."""
import re
from datetime import date

import httpx

URL = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"
URL_FUEL = "https://opendata.rdw.nl/resource/8ys7-d773.json"
URL_DEFECTS = "https://opendata.rdw.nl/resource/a34c-vvps.json"  # Geconstateerde gebreken (per inspection)
URL_DEFECT_TYPES = "https://opendata.rdw.nl/resource/hx2c-gt7k.json"  # Gebreken (descriptions)
URL_RECALL_STATUS = "https://opendata.rdw.nl/resource/t49b-isb7.json"  # Terugroep_actie_status (per vehicle)
URL_RECALLS = "https://opendata.rdw.nl/resource/j9yg-7rg9.json"  # Terugroep_actie (details)
CODE = re.compile(r"[A-Z0-9]{1,20}")  # defect and recall codes; checked before they go into a query
MAX_ITEMS = 50


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


def _rows(c: httpx.Client, url: str, **params) -> list:
    resp = c.get(url, params=params)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise ValueError("unexpected answer")
    return [r for r in rows if isinstance(r, dict)]


def _in(column: str, codes) -> str:
    """SoQL filter 'column in (...)'; only codes that match CODE, so nothing else ends up in the query."""
    return f"{column} in ({','.join(repr(c) for c in codes if CODE.fullmatch(c))})"


def _inspections(c: httpx.Client, plate: str) -> list[dict]:
    """Inspections (APK) at which defects were found, newest first. The RDW does not publish inspections without
    defects. A defect code can have had several descriptions over the years: the one valid on the inspection date."""
    rows = _rows(c, URL_DEFECTS, kenteken=plate, **{"$limit": 1000})
    codes = sorted({r["gebrek_identificatie"] for r in rows if CODE.fullmatch(str(r.get("gebrek_identificatie", "")))})
    texts: dict[str, list[tuple[str, str, str]]] = {}
    if codes:
        for r in _rows(c, URL_DEFECT_TYPES, **{"$where": _in("gebrek_identificatie", codes), "$limit": 5000}):
            texts.setdefault(r.get("gebrek_identificatie"), []).append(
                (r.get("ingangsdatum_gebrek") or "", r.get("einddatum_gebrek") or "99999999", r.get("gebrek_omschrijving") or ""))
    found: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        day, code = r.get("meld_datum_door_keuringsinstantie"), str(r.get("gebrek_identificatie", ""))
        if not _date(day) or not CODE.fullmatch(code):
            continue
        options = sorted(texts.get(code, []))
        valid = [t for start, end, t in options if start <= day < end] or [t for *_, t in options]
        found.setdefault((day, r.get("soort_erkenning_omschrijving") or ""), []).append(
            {"code": code, "description": valid[-1] if valid else "", "count": _number(r.get("aantal_gebreken_geconstateerd")) or 1})
    return [{"date": _date(day), "kind": kind, "defects": defects}
            for (day, kind), defects in sorted(found.items(), reverse=True)][:MAX_ITEMS]


def _recalls(c: httpx.Client, plate: str) -> list[dict]:
    """Recalls for this vehicle with their details; open ones first, then newest first."""
    status = {r["referentiecode_rdw"]: r for r in _rows(c, URL_RECALL_STATUS, kenteken=plate)
              if CODE.fullmatch(str(r.get("referentiecode_rdw", "")))}
    details = {r.get("referentiecode_rdw"): r for r in _rows(c, URL_RECALLS, **{"$where": _in("referentiecode_rdw", sorted(status))})} \
        if status else {}
    recalls = []
    for ref, s in status.items():
        d = details.get(ref, {})
        recalls.append({"ref": ref, "open": s.get("code_status") == "O", "status": s.get("status") or "",
                        "date": _date(d.get("publicatiedatum_rdw")), "manufacturer": d.get("meldende_producent_distributeur") or "",
                        "description": d.get("omschrijving_defect") or "", "consequence": d.get("materi_le_gevolgen") or "",
                        "remedy": d.get("beschrijving_van_het_herstel") or ""})
    recalls.sort(key=lambda r: r["date"] or "", reverse=True)
    return sorted(recalls, key=lambda r: not r["open"])[:MAX_ITEMS]


def _history(c: httpx.Client, plate: str) -> dict:
    """Inspection defects and recalls; not essential, so a part that fails is left out."""
    result = {}
    for key, fetch in (("inspections", _inspections), ("recalls", _recalls)):
        try:
            result[key] = fetch(c, plate)
        except (httpx.HTTPError, ValueError, TypeError):  # TypeError: a field with an unexpected type
            pass
    return result


def _text(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _iso(value) -> str | None:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        return None


def _clean_defect(d: dict) -> dict:
    count = d.get("count")
    return {"code": _text(d.get("code"), 20), "description": _text(d.get("description"), 300),
            "count": count if type(count) is int and 0 < count < 100 else 1}


def _clean_inspection(i: dict) -> dict:
    defects = i.get("defects") if isinstance(i.get("defects"), list) else []
    return {"date": _iso(i.get("date")), "kind": _text(i.get("kind"), 100),
            "defects": [_clean_defect(d) for d in defects[:MAX_ITEMS] if isinstance(d, dict)]}


def _clean_recall(r: dict) -> dict:
    return {"ref": _text(r.get("ref"), 20), "open": r.get("open") is True, "status": _text(r.get("status"), 100),
            "date": _iso(r.get("date")), "manufacturer": _text(r.get("manufacturer"), 200),
            **{k: _text(r.get(k), 1500) for k in ("description", "consequence", "remedy")}}


def clean_history(data: dict) -> dict:
    """'inspections' and 'recalls' in exactly the shape _history makes, with limited texts; for data that comes back
    through the form or from the database."""
    out = {}
    for key, clean in (("inspections", _clean_inspection), ("recalls", _clean_recall)):
        if isinstance(data.get(key), list):
            items = [clean(x) for x in data[key][:MAX_ITEMS] if isinstance(x, dict)]
            out[key] = [x for x in items if key == "recalls" or x["date"]]
    return out


def lookup(plate: str, client: httpx.Client | None = None) -> dict:
    plate = normalize(plate)
    if not plate:
        raise RdwError("Vul een kenteken in.")
    try:
        with client or httpx.Client(timeout=10) as c:
            resp = c.get(URL, params={"kenteken": plate})
            resp.raise_for_status()
            data = resp.json()
            extra = {**_fuel(c, plate), **_history(c, plate)} if data else {}
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
