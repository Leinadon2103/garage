"""Total cost of ownership (F5): what a vehicle really costs, per year and per category.

Everything is spread over calendar days, so a year that is only partly yours counts only partly:
- depreciation: purchase price minus the value now, evenly over the days since the purchase. Without an own
  estimate of the value the rule of thumb is used: 15% less value per year (on the remaining value);
- fixed costs (insurance, road tax, other): an amount per year that applies from its start date until the next
  amount of the same cost, its own end date, or today;
- maintenance and fuel: on the date they were paid.
"""
from datetime import date, timedelta

# Order = colour slot in the chart (fixed: a category always has the same colour). Key -> text in the app.
CATEGORIES = {"depreciation": "Afschrijving", "maintenance": "Onderhoud", "fuel": "Brandstof",
              "insurance": "Verzekering", "road_tax": "Wegenbelasting", "other": "Overig"}
FIXED_KINDS = ("insurance", "road_tax", "other")
YEARLY_LOSS = 0.15  # rule of thumb for depreciation without an own estimate


def estimated_value(purchase_price: float, purchase_date: date, today: date) -> float:
    years = max((today - purchase_date).days, 0) / 365.25
    return purchase_price * (1 - YEARLY_LOSS) ** years


def _spread(per_year: dict, category: str, start: date, end: date, amount_per_day) -> None:
    """Adds amount_per_day(year) for every day in [start, end) to the year it falls in."""
    day = start
    while day < end:
        year_end = min(date(day.year + 1, 1, 1), end)
        days = (year_end - day).days
        per_year.setdefault(day.year, {}).setdefault(category, 0.0)
        per_year[day.year][category] += amount_per_day(day.year) * days
        day = year_end


def _days_in(year: int) -> int:
    return (date(year + 1, 1, 1) - date(year, 1, 1)).days


def calculate(purchase_price: float | None, purchase_date: date | None, current_value: float | None,
              fixed: list[dict], services: list[dict], fuel: list[dict], today: date,
              km_driven: int | None = None) -> dict:
    """fixed: dicts with kind, label, amount_per_year, start_date, end_date (dates as date or None).
    services, fuel: dicts with date (date) and cost (float or None)."""
    per_year: dict[int, dict[str, float]] = {}
    tomorrow = today + timedelta(days=1)  # today counts as a day of ownership

    value_now = estimated = None
    if purchase_price and purchase_date and purchase_date <= today:
        estimated = estimated_value(purchase_price, purchase_date, today)
        value_now = current_value if current_value is not None else estimated
        # worth more than paid (a classic): no negative cost in the chart, the gain is shown on its own
        loss_per_day = max(purchase_price - value_now, 0) / max((tomorrow - purchase_date).days, 1)
        _spread(per_year, "depreciation", purchase_date, tomorrow, lambda _y: loss_per_day)

    groups: dict[tuple, list[dict]] = {}
    for f in fixed:
        groups.setdefault((f["kind"], (f["label"] or "").strip().lower()), []).append(f)
    for entries in groups.values():
        entries.sort(key=lambda f: f["start_date"])
        for i, f in enumerate(entries):
            ends = [tomorrow] + ([entries[i + 1]["start_date"]] if i + 1 < len(entries) else [])
            if f["end_date"]:
                ends.append(f["end_date"] + timedelta(days=1))  # the end date itself still counts
            if f["start_date"] < min(ends):
                amount = f["amount_per_year"]
                _spread(per_year, f["kind"], f["start_date"], min(ends), lambda y, a=amount: a / _days_in(y))

    for category, rows in (("maintenance", services), ("fuel", fuel)):
        for r in rows:
            if r["cost"] and r["date"] <= today:
                per_year.setdefault(r["date"].year, {}).setdefault(category, 0.0)
                per_year[r["date"].year][category] += r["cost"]

    totals = {c: sum(y.get(c, 0.0) for y in per_year.values()) for c in CATEGORIES}
    total = sum(totals.values())
    starts = [d for d in [purchase_date] + [f["start_date"] for f in fixed] + [r["date"] for r in services + fuel if r["cost"]]
              if d and d <= today]
    since = min(starts) if starts else None
    months = (tomorrow - since).days / 30.4375 if since else 0
    return {
        "years": [{"year": y, "amounts": {c: per_year[y].get(c, 0.0) for c in CATEGORIES},
                   "total": sum(per_year[y].values())} for y in sorted(per_year)],
        "totals": totals,
        "total": total,
        "since": since,
        "per_month": total / months if months >= 1 else None,  # under a month says nothing yet
        "per_km": total / km_driven if km_driven and total else None,
        "value_now": value_now,
        "value_gain": max(value_now - purchase_price, 0) if value_now is not None else 0,
        "estimated_value": estimated,
        "value_is_estimate": current_value is None,
    }
