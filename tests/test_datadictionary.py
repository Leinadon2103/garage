"""De datadictionary (docs/datadictionary.md) beschrijft precies de tabellen en kolommen van de database."""
import re
from pathlib import Path

from app import db

DICTIONARY = Path(__file__).parent.parent / "docs" / "datadictionary.md"


def beschreven() -> dict[str, set[str]]:
    """{tabel: kolommen} uit de kopjes '### tabel' en de eerste kolom van de tabel eronder."""
    tabellen = {}
    for blok in re.split(r"^### ", DICTIONARY.read_text(encoding="utf-8"), flags=re.M)[1:]:
        naam, _, rest = blok.partition("\n")
        tabellen[naam.strip()] = set(re.findall(r"^\| `(\w+)` \| (?:INTEGER|TEXT|REAL)", rest, flags=re.M))
    return tabellen


def test_elke_tabel_en_kolom_staat_in_de_datadictionary(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    with db.connect() as conn:
        werkelijk = {t[0]: {k["name"] for k in conn.execute(f"PRAGMA table_info({t[0]})")}
                     for t in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert beschreven() == werkelijk
    assert f"schemaversie {db.SCHEMAVERSIE}" in DICTIONARY.read_text(encoding="utf-8")
