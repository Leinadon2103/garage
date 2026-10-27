"""Rooktest van de starter ('Garage.exe --zelftest'), hier zonder exe."""
from app import db
from tests.test_nieuw import laad_starter


def test_zelftest_doorloopt_de_schermen_in_een_tijdelijke_map(tmp_path):
    garage = laad_starter()
    voor = db.DATA_DIR
    verslag = tmp_path / "verslag.txt"
    assert garage.zelftest(verslag), verslag.read_text(encoding="utf-8")
    tekst = verslag.read_text(encoding="utf-8")
    assert "OK   verkoop-PDF" in tekst and "OK   weglakken" in tekst and tekst.endswith("Zelftest geslaagd")
    assert db.DATA_DIR == voor  # echte gegevensmap weer terug


def test_zelftest_meldt_een_fout(tmp_path, monkeypatch):
    garage = laad_starter()
    monkeypatch.setattr(garage, "_zelftest_stappen", lambda *a: (_ for _ in ()).throw(RuntimeError("scherm kapot")))
    verslag = tmp_path / "verslag.txt"
    assert not garage.zelftest(verslag)
    tekst = verslag.read_text(encoding="utf-8")
    assert "scherm kapot" in tekst and tekst.endswith("Zelftest MISLUKT")
