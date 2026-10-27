"""The data dictionary (docs/data-dictionary.md, English and Dutch) describes exactly the tables and columns."""
import re
from pathlib import Path

import pytest

from app import db

DOCS = Path(__file__).parent.parent / "docs"


def described(path: Path) -> dict[str, set[str]]:
    """{table: columns} from the headings '### table' and the first column of the table below them."""
    tables = {}
    for block in re.split(r"^### ", path.read_text(encoding="utf-8"), flags=re.M)[1:]:
        name, _, rest = block.partition("\n")
        tables[name.strip()] = set(re.findall(r"^\| `(\w+)` \| (?:INTEGER|TEXT|REAL)", rest, flags=re.M))
    return tables


@pytest.mark.parametrize("file", ["data-dictionary.md", "data-dictionary.nl.md"])
def test_every_table_and_column_is_in_the_data_dictionary(tmp_path, monkeypatch, file):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    with db.connect() as conn:
        actual = {t[0]: {c["name"] for c in conn.execute(f"PRAGMA table_info({t[0]})")}
                  for t in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert described(DOCS / file) == actual
    assert re.search(rf"\b{db.SCHEMA_VERSION}\b.*user_version", (DOCS / file).read_text(encoding="utf-8"))
