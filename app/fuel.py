"""Fuel consumption from fill-ups (F2), with the full-to-full method.

Only between two full tanks is it known exactly how much fuel was used: everything put in after the first full
tank, up to and including the next full tank, was burnt over the kilometres in between. A partial fill-up
therefore has no consumption of its own; its litres count at the next full tank. Fill-ups before the first full
tank say nothing about consumption. A forgotten fill-up makes that one interval look too economical.
"""


def consumption(entries: list[dict]) -> dict:
    """entries: dicts with odometer_km, liters, cost (or None) and full_tank. Returns per entry id the consumption
    of the interval that ends there, plus the averages over all complete intervals."""
    per_entry: dict[int, float] = {}
    km_total = liters_total = 0.0
    start_km, liters, last = None, 0.0, None
    for e in sorted(entries, key=lambda e: (e["odometer_km"], e["date"], e["id"])):
        if start_km is None:
            if e["full_tank"]:
                start_km = e["odometer_km"]
            continue
        liters += e["liters"]
        if e["full_tank"]:
            km = e["odometer_km"] - start_km
            if km > 0:
                per_entry[e["id"]] = last = liters / km * 100
                km_total += km
                liters_total += liters
            start_km, liters = e["odometer_km"], 0.0
    priced = [e for e in entries if e["cost"]]
    priced_liters = sum(e["liters"] for e in priced)
    return {
        "per_entry": per_entry,  # {id: litres per 100 km}
        "average": liters_total / km_total * 100 if km_total else None,
        "last": last,  # the most recent complete interval
        "km": km_total,
        "liters": sum(e["liters"] for e in entries),
        "cost": sum(e["cost"] for e in priced),
        "price_per_liter": sum(e["cost"] for e in priced) / priced_liters if priced_liters else None,
    }


def km_per_liter(liters_per_100: float | None) -> float | None:
    return 100 / liters_per_100 if liters_per_100 else None
