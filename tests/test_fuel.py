"""Fill-ups and fuel consumption (F2): the full-to-full calculation, the page, and the link with odometer readings
and costs."""
import pytest

from app import db, fuel
from tests.test_app import add_vehicle, client, new_service, type_id  # noqa: F401


def entry(id_, km, liters, full=True, cost=None, day="2026-01-01"):
    return {"id": id_, "date": day, "odometer_km": km, "liters": liters, "full_tank": full, "cost": cost}


def test_full_to_full_with_a_partial_fill_up_in_between():
    stats = fuel.consumption([
        entry(1, 10000, 30),           # first full tank: only the starting point
        entry(2, 10200, 6, full=False),  # partial: counts at the next full tank
        entry(3, 10500, 14),           # 20 litres over 500 km
        entry(4, 11000, 25),           # 25 litres over 500 km
    ])
    assert stats["per_entry"] == {3: pytest.approx(4.0), 4: pytest.approx(5.0)}
    assert stats["average"] == pytest.approx(4.5) and stats["last"] == pytest.approx(5.0)
    assert stats["km"] == 1000 and stats["liters"] == 75
    assert fuel.km_per_liter(stats["average"]) == pytest.approx(22.22, abs=0.01)


def test_consumption_needs_two_full_tanks_and_ignores_what_came_before():
    assert fuel.consumption([])["average"] is None
    assert fuel.consumption([entry(1, 1000, 10, full=False), entry(2, 1200, 10)])["average"] is None
    stats = fuel.consumption([entry(1, 1000, 10, full=False), entry(2, 1200, 10), entry(3, 1400, 8)])
    assert stats["per_entry"] == {3: pytest.approx(4.0)}  # the partial fill-up before the first full tank does not count
    assert fuel.km_per_liter(None) is None


def test_order_is_by_odometer_and_prices_only_count_fill_ups_with_an_amount():
    stats = fuel.consumption([entry(2, 1500, 25, cost=50.0), entry(1, 1000, 20, cost=36.0), entry(3, 2000, 20)])
    assert stats["per_entry"] == {2: pytest.approx(5.0), 3: pytest.approx(4.0)}
    assert stats["cost"] == 86.0 and stats["price_per_liter"] == pytest.approx(86 / 45)


def add_fuel(client, vid, **data):
    return client.post(f"/vehicles/{vid}/fuel", data={"date": "2026-03-01", "full_tank": "1", **data},
                       follow_redirects=False)


def test_fuel_page_add_show_and_delete(client):
    vid = add_vehicle(client)
    assert add_fuel(client, vid, odometer_km="10.000", liters="12,5", cost="25,00").status_code == 303
    assert add_fuel(client, vid, date="2026-03-15", odometer_km="10250", liters="10", cost="21,50",
                    note="Shell A12").status_code == 303
    page = client.get(f"/vehicles/{vid}/fuel").text
    assert "4,0 l/100 km" in page and "1 op 25,0" in page and "Shell A12" in page and "€ 2,150" in page
    assert "gemiddeld 4,0 l/100 km" in client.get(f"/vehicles/{vid}").text
    with db.connect() as conn:
        assert db.current_km(conn, vid) == 10250  # a fill-up is an odometer reading too
        fid = conn.execute("SELECT id FROM fuel_entry ORDER BY id DESC").fetchone()[0]
    client.post(f"/fuel/{fid}/delete")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fuel_entry").fetchone()[0] == 1


@pytest.mark.parametrize("data, message", [
    ({"odometer_km": "", "liters": "10"}, "kilometerstand"),
    ({"odometer_km": "1000", "liters": ""}, "liters"),
    ({"odometer_km": "1000", "liters": "0"}, "liters"),
    ({"odometer_km": "1000", "liters": "5000"}, "liters"),
    ({"odometer_km": "1000", "liters": "10", "cost": "-5"}, "negatief"),
    ({"odometer_km": "1000", "liters": "10", "date": "2999-01-01"}, "toekomst"),
])
def test_fuel_input_is_checked(client, data, message):
    vid = add_vehicle(client)
    r = add_fuel(client, vid, **data)
    assert r.status_code == 400 and message in r.text


def test_fill_up_must_fit_the_odometer_history(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2026-02-01", "odometer_km": "12000", "types": [str(type_id("Olie vervangen"))]})
    r = add_fuel(client, vid, odometer_km="11000", liters="10")  # later date, lower reading
    assert r.status_code == 400 and "lager dan" in r.text
    add_fuel(client, vid, odometer_km="12500", liters="10")
    r = client.post(f"/vehicles/{vid}/odometer", data={"odometer_km": "12400", "date": "2026-03-02"})
    assert r.status_code == 400 and "bij het tanken" in r.text  # and the other way round


def test_costs_per_km_include_fuel(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2026-01-01", "odometer_km": "10000", "cost": "100",
                              "types": [str(type_id("Olie vervangen"))]})
    add_fuel(client, vid, odometer_km="11000", liters="50", cost="100")
    page = client.get(f"/vehicles/{vid}/costs").text
    assert "€ 0,20" in page and "met brandstof" in page and "getankt" in page  # (100 + 100) / 1000 km


def test_no_fuel_tile_for_a_trailer(client):
    vid = add_vehicle(client)
    assert "Tanken" in client.get(f"/vehicles/{vid}").text
    with db.connect() as conn:
        conn.execute("UPDATE vehicle SET vehicle_type = 'Aanhangwagen' WHERE id = ?", (vid,))
    assert f"/vehicles/{vid}/fuel" not in client.get(f"/vehicles/{vid}").text


def test_deleting_the_vehicle_deletes_its_fill_ups(client):
    vid = add_vehicle(client)
    add_fuel(client, vid, odometer_km="1000", liters="10")
    client.post(f"/vehicles/{vid}/delete")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fuel_entry").fetchone()[0] == 0
