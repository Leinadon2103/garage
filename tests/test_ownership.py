"""Total cost of ownership (F5): the calculation with amounts you can check by hand, and the page."""
from datetime import date

import pytest

from app import db, ownership
from tests.test_app import add_vehicle, client, new_service, type_id  # noqa: F401

TODAY = date(2026, 12, 31)


def fixed(kind, amount, start, end=None, label=None):
    return {"kind": kind, "label": label, "amount_per_year": amount, "start_date": start, "end_date": end}


def calc(**kw):
    args = {"purchase_price": None, "purchase_date": None, "current_value": None, "fixed": [], "services": [],
            "fuel": [], "today": TODAY}
    return ownership.calculate(**{**args, **kw})


def test_fixed_costs_count_per_day_of_the_year():
    r = calc(fixed=[fixed("insurance", 365, date(2026, 7, 1))])  # 184 days of 2026
    assert r["totals"]["insurance"] == pytest.approx(184)
    r = calc(fixed=[fixed("road_tax", 366, date(2024, 1, 1))], today=date(2024, 12, 31))  # leap year: 366 days
    assert r["totals"]["road_tax"] == pytest.approx(366)


def test_a_new_amount_replaces_the_old_one_from_its_start_date():
    r = calc(fixed=[fixed("insurance", 365, date(2025, 1, 1)), fixed("insurance", 730, date(2026, 1, 1))])
    by_year = {y["year"]: y["amounts"]["insurance"] for y in r["years"]}
    assert by_year == {2025: pytest.approx(365), 2026: pytest.approx(730)}


def test_end_date_counts_and_other_costs_are_separate_by_label():
    r = calc(fixed=[fixed("other", 365, date(2026, 1, 1), end=date(2026, 1, 10), label="Stalling"),
                    fixed("other", 365, date(2026, 1, 5), label="ANWB")])
    assert r["totals"]["other"] == pytest.approx(10 + 361)  # 1-10 Jan inclusive, ANWB 5 Jan - 31 Dec


def test_depreciation_own_value_and_rule_of_thumb():
    r = calc(purchase_price=10000, purchase_date=date(2025, 1, 1), current_value=7000)
    assert r["totals"]["depreciation"] == pytest.approx(3000)
    assert r["years"][0]["amounts"]["depreciation"] == pytest.approx(3000 * 365 / 730)  # spread over the days
    assert not r["value_is_estimate"] and r["value_now"] == 7000
    r = calc(purchase_price=10000, purchase_date=date(2024, 12, 31))
    assert r["value_is_estimate"] and r["value_now"] == pytest.approx(10000 * 0.85 ** 2, rel=1e-3)


def test_a_classic_gaining_value_has_no_negative_cost():
    r = calc(purchase_price=10000, purchase_date=date(2025, 1, 1), current_value=12500)
    assert r["totals"]["depreciation"] == 0 and r["value_gain"] == 2500


def test_maintenance_fuel_per_month_and_per_km():
    r = calc(purchase_price=6000, purchase_date=date(2026, 1, 1), current_value=6000,
             services=[{"date": date(2026, 3, 1), "cost": 300.0}, {"date": date(2026, 4, 1), "cost": None}],
             fuel=[{"date": date(2026, 5, 1), "cost": 60.0}], km_driven=3600)
    assert r["totals"]["maintenance"] == 300 and r["totals"]["fuel"] == 60 and r["total"] == 360
    assert r["per_month"] == pytest.approx(360 / (365 / 30.4375)) and r["per_km"] == pytest.approx(0.1)


def test_nothing_yet_and_less_than_a_month():
    r = calc()
    assert r["total"] == 0 and r["years"] == [] and r["per_month"] is None and r["since"] is None
    assert calc(services=[{"date": date(2026, 12, 20), "cost": 50.0}])["per_month"] is None
    assert calc(fixed=[fixed("insurance", 365, date(2027, 1, 1))])["total"] == 0  # starts in the future


@pytest.mark.parametrize("text, amount", [
    ("8.500", 8500), ("12.345.678", 12345678), ("1.250,00", 1250), ("€ 72,50", 72.5),
    ("45.50", 45.5), ("45.5", 45.5), ("0.500", 0.5), ("1250", 1250),
])
def test_dutch_thousands_without_a_comma(text, amount):
    """'8.500' for a purchase price means eight and a half thousand, not 8.5; '45.50' stays 45.5."""
    from app.web import parse_amount
    assert parse_amount(text) == amount


# ---------- the page ----------

def test_page_shows_the_breakdown_and_takes_input(client):
    vid = add_vehicle(client)
    assert "Nog geen kosten" in client.get(f"/vehicles/{vid}/ownership").text
    r = client.post(f"/vehicles/{vid}/purchase", data={"purchase_price": "8.500", "purchase_date": "2025-01-01",
                                                       "current_value": "6.000,00"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/vehicles/{vid}/fixed-costs", data={"kind": "insurance", "amount": "25", "per": "month",
                                                          "start_date": "2025-01-01"}, follow_redirects=False)
    assert r.status_code == 303
    client.post(f"/vehicles/{vid}/fixed-costs", data={"kind": "other", "label": "Stalling", "amount": "150",
                                                      "per": "year", "start_date": "2025-01-01"})
    new_service(client, vid, {"date": "2025-06-01", "cost": "200", "types": [str(type_id("Olie vervangen"))]})
    page = client.get(f"/vehicles/{vid}/ownership").text
    for text in ("Afschrijving", "Verzekering", "Stalling", "Onderhoud", "€ 6.000,00", "per maand", "2025", "2026"):
        assert text in page, text
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM fixed_cost WHERE kind = 'insurance'").fetchone()
        assert row["amount_per_year"] == 300  # 25 per month
        v = conn.execute("SELECT purchase_price, current_value FROM vehicle").fetchone()
        assert (v["purchase_price"], v["current_value"]) == (8500, 6000)
    assert "per maand" in client.get(f"/vehicles/{vid}").text  # the tile
    client.post(f"/fixed-costs/{row['id']}/delete")
    assert "Verzekering</span>" not in client.get(f"/vehicles/{vid}/ownership").text


@pytest.mark.parametrize("url, data, message", [
    ("purchase", {"purchase_price": "-1", "purchase_date": "2025-01-01"}, "negatief"),
    ("purchase", {"purchase_price": "1000", "purchase_date": "2999-01-01"}, "toekomst"),
    ("fixed-costs", {"kind": "insurance", "amount": "", "per": "year", "start_date": "2025-01-01"}, "bedrag"),
    ("fixed-costs", {"kind": "parking", "amount": "10", "per": "year", "start_date": "2025-01-01"}, "soort"),
    ("fixed-costs", {"kind": "insurance", "amount": "10", "per": "week", "start_date": "2025-01-01"}, "per"),
    ("fixed-costs", {"kind": "other", "amount": "10", "per": "year", "start_date": "2025-01-01"}, "omschrijving"),
    ("fixed-costs", {"kind": "insurance", "amount": "10", "per": "year", "start_date": "2025-02-01",
                     "end_date": "2025-01-01"}, "einddatum"),
])
def test_input_is_checked(client, url, data, message):
    vid = add_vehicle(client)
    r = client.post(f"/vehicles/{vid}/{url}", data=data)
    assert r.status_code == 400 and message in r.text


def test_clearing_the_purchase_and_deleting_the_vehicle(client):
    vid = add_vehicle(client)
    client.post(f"/vehicles/{vid}/purchase", data={"purchase_price": "1000", "purchase_date": "2025-01-01"})
    client.post(f"/vehicles/{vid}/purchase", data={"purchase_price": "", "purchase_date": ""})
    with db.connect() as conn:
        assert conn.execute("SELECT purchase_price FROM vehicle").fetchone()[0] is None
    client.post(f"/vehicles/{vid}/fixed-costs", data={"kind": "road_tax", "amount": "100", "per": "year",
                                                      "start_date": "2025-01-01"})
    client.post(f"/vehicles/{vid}/delete")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fixed_cost").fetchone()[0] == 0
