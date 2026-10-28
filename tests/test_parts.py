"""Parts and consumables (F7): what the next service needs, the page, and the link with the planning."""
from app import db, parts
from tests.test_app import add_vehicle, client, new_service, type_id  # noqa: F401


def plan_item(type_id_, name, color, progress=0.5):
    return {"type_id": type_id_, "name": name, "color": color, "progress": progress, "text": ""}


def part(id_, name, type_ids, in_stock=0, **extra):
    return {"id": id_, "name": name, "type_ids": type_ids, "in_stock": in_stock, **extra}


def test_next_service_is_everything_due_otherwise_the_first_one_coming():
    plan = [plan_item(1, "Olie vervangen", "green", 0.8), plan_item(2, "Kleine beurt", "green", 0.3),
            {"name": "APK verloopt", "color": "red", "progress": 1.0, "text": ""}]  # an expiry date needs no parts
    assert [p["name"] for p in parts.next_service(plan)] == ["Olie vervangen"]
    plan[1]["color"] = "orange"
    plan.append(plan_item(3, "Remvloeistof vervangen", "red", 1.2))
    assert [p["name"] for p in parts.next_service(plan)] == ["Remvloeistof vervangen", "Kleine beurt"]
    assert parts.next_service([]) == []


def test_needed_lists_the_parts_of_that_maintenance_still_to_buy_first():
    plan = [plan_item(1, "Olie vervangen", "red"), plan_item(2, "Kleine beurt", "orange"), plan_item(3, "Ketting", "green")]
    items = [part(1, "Oliefilter", [1, 2], in_stock=1), part(2, "Motorolie", [1]), part(3, "Kettingspray", [3]),
             part(4, "Bougies", [])]
    need = parts.needed(items, plan)
    assert [p["name"] for p in need["items"]] == ["Motorolie", "Oliefilter"]
    assert need["items"][1]["for"] == ["Olie vervangen", "Kleine beurt"] and need["to_buy"] == 1


def test_shopping_text_only_what_is_still_to_buy():
    items = [part(1, "Oliefilter", [1], brand="HiFlo", part_number="HF303", quantity="1 stuk"),
             part(2, "Motorolie", [1], quantity="3,2 liter"), part(3, "Bougies", [1], in_stock=1)]
    assert parts.shopping_text(items) == "Oliefilter · HiFlo HF303 · 1 stuk\nMotorolie · 3,2 liter"


# ---------- the page ----------

def add_part(client, vid, **data):
    return client.post(f"/vehicles/{vid}/parts", data={"name": "Oliefilter", **data}, follow_redirects=False)


def test_add_edit_stock_and_delete(client):
    vid = add_vehicle(client)
    assert "Nog geen onderdelen" in client.get(f"/vehicles/{vid}/parts").text
    oil = str(type_id("Olie vervangen"))
    r = add_part(client, vid, brand="HiFlo", part_number="HF303", quantity="1 stuk", types=[oil])
    assert r.status_code == 303
    with db.connect() as conn:
        pid = conn.execute("SELECT id FROM part").fetchone()[0]
        assert conn.execute("SELECT type_id FROM part_maintenance_type").fetchone()[0] == int(oil)
    page = client.get(f"/vehicles/{vid}/parts").text
    assert "HiFlo HF303" in page and "Olie vervangen" in page

    add_part(client, vid, name="Oliefilter K&N", part_id=str(pid))  # edit: no maintenance chosen any more
    with db.connect() as conn:
        assert conn.execute("SELECT name, brand FROM part").fetchone()[:] == ("Oliefilter K&N", None)
        assert conn.execute("SELECT COUNT(*) FROM part_maintenance_type").fetchone()[0] == 0
    assert 'value="Oliefilter K&amp;N"' in client.get(f"/vehicles/{vid}/parts?edit={pid}").text

    client.post(f"/parts/{pid}/stock")
    with db.connect() as conn:
        assert conn.execute("SELECT in_stock FROM part").fetchone()[0] == 1
    client.post(f"/parts/{pid}/delete")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM part").fetchone()[0] == 0


def test_the_next_service_on_the_parts_page_and_the_vehicle_page(client):
    vid = add_vehicle(client)
    new_service(client, vid, {"date": "2020-01-01", "odometer_km": "1000"})  # oil change long overdue
    add_part(client, vid, name="Motorolie", quantity="3,2 liter", types=[str(type_id("Olie vervangen"))])
    add_part(client, vid, name="Kettingspray")  # not linked to maintenance: not in the list
    page = client.get(f"/vehicles/{vid}/parts").text
    assert "Olie vervangen ·" in page and "Kooplijst kopiëren" in page
    assert 'data-copy="Motorolie · 3,2 liter"' in page
    vehicle = client.get(f"/vehicles/{vid}").text
    assert "Voor de volgende beurt: 1 onderdeel" in vehicle and "1 nog kopen" in vehicle


def test_input_is_checked_and_parts_stay_with_their_vehicle(client):
    vid = add_vehicle(client)
    other = add_vehicle(client, plate="K-123-XB")
    assert add_part(client, vid, name=" ").status_code == 400
    assert add_part(client, vid, types=["abc"]).status_code == 400
    assert add_part(client, vid, types=["99999"]).status_code == 400
    add_part(client, other)
    with db.connect() as conn:
        pid = conn.execute("SELECT id FROM part").fetchone()[0]
    assert add_part(client, vid, part_id=str(pid)).status_code == 404  # someone else's part
    assert client.post("/parts/99999/stock").status_code == 404
    client.post(f"/vehicles/{other}/delete")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM part").fetchone()[0] == 0


def test_a_name_with_quotes_cannot_break_out_of_the_page(client):
    vid = add_vehicle(client)
    add_part(client, vid, name="Band'); alert(1); ('<b>")
    page = client.get(f"/vehicles/{vid}/parts").text
    assert "<b>" not in page and "alert(1); ('" not in page
