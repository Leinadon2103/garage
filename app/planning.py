"""Bepaalt per onderhoudstype wanneer het weer nodig is (op km en/of tijd, wat eerst komt)."""
from datetime import date

ORANJE_KM_FRACTIE = 0.10
ORANJE_DAGEN = 31


def plus_maanden(d: date, maanden: int) -> date:
    m = d.month - 1 + maanden
    jaar, maand = d.year + m // 12, m % 12 + 1
    # dag afkappen op einde maand (31 jan + 1 maand = 28/29 feb)
    for dag in (d.day, 30, 29, 28):
        try:
            return date(jaar, maand, dag)
        except ValueError:
            continue
    raise ValueError(d)


def _km(km: int) -> str:
    return f"{km:,}".replace(",", ".")


def _duur(dagen: int) -> str:
    if dagen < 45:
        return f"{dagen} dag{'en' if dagen != 1 else ''}"
    maanden = round(dagen / 30.4)
    return f"{maanden} maand{'en' if maanden != 1 else ''}"


def status(type_, laatste_km: int | None, laatste_datum: date, huidige_km: int | None, vandaag: date) -> dict:
    """Status van één type op basis van de laatste keer dat het gedaan is."""
    volgende_km = (
        laatste_km + type_["interval_km"] if type_["interval_km"] and laatste_km is not None else None
    )
    volgende_datum = (
        plus_maanden(laatste_datum, type_["interval_maanden"]) if type_["interval_maanden"] else None
    )

    kleur = "groen"
    if volgende_km is not None and huidige_km is not None:
        if huidige_km >= volgende_km:
            kleur = "rood"
        elif volgende_km - huidige_km <= type_["interval_km"] * ORANJE_KM_FRACTIE:
            kleur = "oranje"
    if volgende_datum is not None and kleur != "rood":
        dagen = (volgende_datum - vandaag).days
        if dagen <= 0:
            kleur = "rood"
        elif dagen <= ORANJE_DAGEN:
            kleur = "oranje"

    # voortgang (0..1+) en een leesbare tekst, op basis van wat het dichtst bij is
    km_deel = tijd_deel = None
    if volgende_km is not None and huidige_km is not None:
        km_deel = (huidige_km - laatste_km) / type_["interval_km"]
    if volgende_datum is not None:
        tijd_deel = (vandaag - laatste_datum).days / max(1, (volgende_datum - laatste_datum).days)
    if km_deel is not None and (tijd_deel is None or km_deel >= tijd_deel):
        rest = volgende_km - huidige_km
        tekst = f"nog {_km(rest)} km" if rest > 0 else f"{_km(-rest)} km te laat"
    elif tijd_deel is not None:
        rest = (volgende_datum - vandaag).days
        tekst = f"nog {_duur(rest)}" if rest > 0 else f"{_duur(-rest)} te laat"
    else:
        tekst = ""

    return {
        "naam": type_["naam"],
        "deel": max(d for d in (km_deel, tijd_deel, 0) if d is not None),
        "tekst": tekst,
        "laatste_datum": laatste_datum,
        "laatste_km": laatste_km,
        "volgende_km": volgende_km,
        "volgende_datum": volgende_datum,
        "kleur": kleur,
    }


def overzicht(beurten: list[dict], huidige_km: int | None, vandaag: date | None = None,
              intervallen: dict | None = None) -> list[dict]:
    """Per type met een interval: status t.o.v. de laatste beurt waarin het zat.

    `beurten` moet nieuwste-eerst gesorteerd zijn (zoals db.beurten teruggeeft).
    `intervallen` = eigen waarden van dit voertuig ({type_id: (km, maanden)}); anders geldt de vuistregel.
    Types die nooit gedaan zijn, tonen we niet: zonder startpunt valt er niets te plannen.
    """
    vandaag = vandaag or date.today()
    intervallen = intervallen or {}
    gezien = {}
    for b in beurten:
        for t in b["types"]:
            if t["id"] in gezien:
                continue
            eigen = t["id"] in intervallen
            km, mnd = intervallen[t["id"]] if eigen else (t["interval_km"], t["interval_maanden"])
            if not (km or mnd):
                continue
            t = {"id": t["id"], "naam": t["naam"], "interval_km": km, "interval_maanden": mnd}
            gezien[t["id"]] = {**status(t, b["kilometerstand"], date.fromisoformat(b["datum"]), huidige_km, vandaag),
                               "bron": "eigen" if eigen else "vuistregel"}
    volgorde = {"rood": 0, "oranje": 1, "groen": 2}
    return sorted(gezien.values(), key=lambda s: (volgorde[s["kleur"]], s["naam"]))


# Afgeronde landelijke gemiddelden (km per jaar); alleen voor een ruwe schatting, nooit voor de planning.
KM_PER_JAAR = {"Motorfiets": 3500, "Bromfiets": 2000, "Personenauto": 13000, "Bedrijfsauto": 18000}


def km_schatting(voertuigsoort: str, eerste_toelating: str | None, vandaag: date | None = None) -> int | None:
    if voertuigsoort not in KM_PER_JAAR or not eerste_toelating:
        return None
    try:
        start = date.fromisoformat(eerste_toelating)
    except ValueError:
        return None
    jaren = max(0, ((vandaag or date.today()) - start).days / 365.25)
    return int(round(jaren * KM_PER_JAAR[voertuigsoort], -2))


def vervalstatus(naam: str, datum: str, bron: str, vandaag: date | None = None) -> dict:
    """Status van iets met een vaste vervaldatum (APK, verzekering, garantie)."""
    vandaag = vandaag or date.today()
    verval = date.fromisoformat(datum)
    dagen = (verval - vandaag).days
    kleur = "rood" if dagen < 0 else "oranje" if dagen <= ORANJE_DAGEN else "groen"
    tekst = f"nog {_duur(dagen)}" if dagen > 0 else "vandaag" if dagen == 0 else f"{_duur(-dagen)} verlopen"
    return {"naam": naam, "kleur": kleur, "tekst": tekst, "deel": min(1.0, max(0.0, 1 - dagen / 365)),
            "laatste_datum": None, "laatste_km": None, "volgende_km": None, "volgende_datum": verval, "bron": bron}
