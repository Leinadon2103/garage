"""Works out per maintenance type when it is due again (by km and/or time, whichever comes first)."""
from datetime import date

ORANGE_KM_FRACTION = 0.10
ORANGE_DAYS = 31


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    # clip the day to the end of the month (31 Jan + 1 month = 28/29 Feb)
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def _km(km: int) -> str:
    return f"{km:,}".replace(",", ".")


def _duration(days: int) -> str:
    if days < 45:
        return f"{days} dag{'en' if days != 1 else ''}"
    months = round(days / 30.4)
    return f"{months} maand{'en' if months != 1 else ''}"


def status(type_, last_km: int | None, last_date: date, current_km: int | None, today: date) -> dict:
    """Status of one type, based on the last time it was done."""
    next_km = last_km + type_["interval_km"] if type_["interval_km"] and last_km is not None else None
    next_date = add_months(last_date, type_["interval_months"]) if type_["interval_months"] else None

    color = "green"
    if next_km is not None and current_km is not None:
        if current_km >= next_km:
            color = "red"
        elif next_km - current_km <= type_["interval_km"] * ORANGE_KM_FRACTION:
            color = "orange"
    if next_date is not None and color != "red":
        days = (next_date - today).days
        if days <= 0:
            color = "red"
        elif days <= ORANGE_DAYS:
            color = "orange"

    # progress (0..1+) and a readable text, based on whatever is closest
    km_part = time_part = None
    if next_km is not None and current_km is not None:
        km_part = (current_km - last_km) / type_["interval_km"]
    if next_date is not None:
        time_part = (today - last_date).days / max(1, (next_date - last_date).days)
    if km_part is not None and (time_part is None or km_part >= time_part):
        left = next_km - current_km
        text = f"nog {_km(left)} km" if left > 0 else f"{_km(-left)} km te laat"
    elif time_part is not None:
        left = (next_date - today).days
        text = f"nog {_duration(left)}" if left > 0 else f"{_duration(-left)} te laat"
    else:
        text = ""

    return {
        "name": type_["name"],
        "progress": max(d for d in (km_part, time_part, 0) if d is not None),
        "text": text,
        "last_date": last_date,
        "last_km": last_km,
        "next_km": next_km,
        "next_date": next_date,
        "color": color,
    }


def overview(services: list[dict], current_km: int | None, today: date | None = None,
             intervals: dict | None = None) -> list[dict]:
    """Per type with an interval: the status relative to the last service that included it.

    `services` must be sorted newest first (as db.services returns them).
    `intervals` = custom values of this vehicle ({type_id: (km, months)}); otherwise the rule of thumb applies.
    Types that were never done are not shown: without a starting point there is nothing to plan.
    """
    today = today or date.today()
    intervals = intervals or {}
    seen = {}
    for s in services:
        for t in s["types"]:
            if t["id"] in seen:
                continue
            custom = t["id"] in intervals
            km, months = intervals[t["id"]] if custom else (t["interval_km"], t["interval_months"])
            if not (km or months):
                continue
            t = {"id": t["id"], "name": t["name"], "interval_km": km, "interval_months": months}
            seen[t["id"]] = {**status(t, s["odometer_km"], date.fromisoformat(s["date"]), current_km, today),
                             "source": "custom" if custom else "rule_of_thumb", "type_id": t["id"]}
    order = {"red": 0, "orange": 1, "green": 2}
    return sorted(seen.values(), key=lambda st: (order[st["color"]], st["name"]))


# Rounded national averages (km per year); only for a rough estimate, never for the planning.
KM_PER_YEAR = {"Motorfiets": 3500, "Bromfiets": 2000, "Personenauto": 13000, "Bedrijfsauto": 18000}


def km_estimate(vehicle_type: str, first_registration: str | None, today: date | None = None) -> int | None:
    if vehicle_type not in KM_PER_YEAR or not first_registration:
        return None
    try:
        start = date.fromisoformat(first_registration)
    except ValueError:
        return None
    years = max(0, ((today or date.today()) - start).days / 365.25)
    return int(round(years * KM_PER_YEAR[vehicle_type], -2))


def expiry_status(name: str, expiry: str, source: str, today: date | None = None) -> dict:
    """Status of something with a fixed expiry date (APK, insurance, warranty)."""
    today = today or date.today()
    expires = date.fromisoformat(expiry)
    days = (expires - today).days
    color = "red" if days < 0 else "orange" if days <= ORANGE_DAYS else "green"
    text = f"nog {_duration(days)}" if days > 0 else "vandaag" if days == 0 else f"{_duration(-days)} verlopen"
    return {"name": name, "color": color, "text": text, "progress": min(1.0, max(0.0, 1 - days / 365)),
            "last_date": None, "last_km": None, "next_km": None, "next_date": expires, "source": source}
