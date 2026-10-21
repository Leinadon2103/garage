"""Voertuiggegevens ophalen uit RDW Open Data (dataset Gekentekende voertuigen)."""
import re

import httpx

URL = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"
URL_BRANDSTOF = "https://opendata.rdw.nl/resource/8ys7-d773.json"


class RdwFout(Exception):
    pass


def normaliseer(kenteken: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", kenteken.upper())


def _datum(waarde: str | None) -> str | None:
    """'20240122' -> '2024-01-22'."""
    return f"{waarde[:4]}-{waarde[4:6]}-{waarde[6:]}" if waarde and re.fullmatch(r"\d{8}", waarde) else None


def _getal(waarde, soort=int):
    try:
        return soort(float(waarde)) if waarde not in (None, "") else None
    except ValueError:
        return None


def _brandstof(c: httpx.Client, kenteken: str) -> dict:
    """Brandstof en vermogen; niet essentieel, dus bij een fout gewoon leeg."""
    try:
        resp = c.get(URL_BRANDSTOF, params={"kenteken": kenteken})
        resp.raise_for_status()
        rijen = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(rijen, list) or not rijen:
        return {}
    soorten = [r.get("brandstof_omschrijving") for r in rijen if r.get("brandstof_omschrijving")]
    kw = [_getal(r.get("nettomaximumvermogen"), float) or _getal(r.get("netto_max_vermogen_elektrisch"), float) for r in rijen]
    kw = [k for k in kw if k]
    return {"brandstof": " + ".join(dict.fromkeys(soorten)) or None, "vermogen_kw": round(max(kw)) if kw else None}


def zoek(kenteken: str, client: httpx.Client | None = None) -> dict:
    kenteken = normaliseer(kenteken)
    if not kenteken:
        raise RdwFout("Vul een kenteken in.")
    try:
        with client or httpx.Client(timeout=10) as c:
            resp = c.get(URL, params={"kenteken": kenteken})
            resp.raise_for_status()
            data = resp.json()
            extra = _brandstof(c, kenteken) if data else {}
    except (httpx.HTTPError, ValueError):
        raise RdwFout("RDW is niet bereikbaar. Controleer je internet of vul de gegevens zelf in.")
    if not data:
        raise RdwFout(f"Kenteken {kenteken} niet gevonden bij de RDW.")

    r = data[0]
    toelating = r.get("datum_eerste_toelating", "")  # formaat JJJJMMDD
    if re.fullmatch(r"\d{8}", toelating):
        toelating = f"{toelating[:4]}-{toelating[4:6]}-{toelating[6:]}"
    cilinder = r.get("cilinderinhoud")
    kleur = r.get("eerste_kleur", "")
    if kleur.upper() in ("N.V.T.", "NIET GEREGISTREERD"):
        kleur = ""
    return {
        "kenteken": kenteken,
        "voertuigsoort": r.get("voertuigsoort", ""),
        "merk": r.get("merk", "").title(),
        "model": r.get("handelsbenaming", ""),
        "datum_eerste_toelating": toelating,
        "kleur": kleur.capitalize(),
        "cilinderinhoud": int(cilinder) if cilinder and cilinder.isdigit() else None,
        "rdw": {
            "apk_vervaldatum": _datum(r.get("vervaldatum_apk")),
            "terugroepactie": r.get("openstaande_terugroepactie_indicator") == "Ja",
            "tellerstandoordeel": r.get("tellerstandoordeel"),
            "jaar_tellerstand": _getal(r.get("jaar_laatste_registratie_tellerstand")),
            "catalogusprijs": _getal(r.get("catalogusprijs")),
            "datum_tenaamstelling_nl": _datum(r.get("datum_eerste_tenaamstelling_in_nederland")),
            "massa_rijklaar": _getal(r.get("massa_rijklaar")),
            **extra,
        },
    }
