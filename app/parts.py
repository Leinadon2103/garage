"""Parts and consumables (F7): which parts the next service needs. Pure logic, no database."""

ORDER = {"red": 0, "orange": 1, "green": 2}


def next_service(plan: list[dict]) -> list[dict]:
    """The maintenance of the next service: everything that is due (red) or nearly due (orange).
    Nothing due yet: the one that comes first (the highest progress). Expiry dates (APK, documents) need no parts."""
    maintenance = [p for p in plan if p.get("type_id")]
    due = [p for p in maintenance if p["color"] in ("red", "orange")]
    if due:
        return sorted(due, key=lambda p: (ORDER[p["color"]], -p["progress"], p["name"]))
    return [max(maintenance, key=lambda p: p["progress"])] if maintenance else []


def needed(parts: list[dict], plan: list[dict]) -> dict:
    """Parts for the next service. `parts`: dicts with at least name, in_stock and type_ids.

    Returns the maintenance it is for, the parts (still to buy first, then by name) each with the names of
    the maintenance they are for, and how many are still to buy."""
    service = next_service(plan)
    names = {p["type_id"]: p["name"] for p in service}
    items = [{**part, "for": [names[t] for t in names if t in part["type_ids"]]}
             for part in parts if names.keys() & set(part["type_ids"])]
    items.sort(key=lambda p: (bool(p["in_stock"]), p["name"].lower()))
    return {"service": service, "items": items, "to_buy": sum(1 for p in items if not p["in_stock"])}


def shopping_text(items: list[dict]) -> str:
    """The parts still to buy as plain text, one per line, to paste in an order or a message."""
    lines = []
    for p in items:
        if p["in_stock"]:
            continue
        details = " ".join(filter(None, (p.get("brand"), p.get("part_number"))))
        lines.append(" · ".join(filter(None, (p["name"], details, p.get("quantity")))))
    return "\n".join(lines)
