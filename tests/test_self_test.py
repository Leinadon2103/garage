"""Smoke test of the starter ('Garage.exe --self-test'), here without the exe."""
from app import db
from tests.test_features import load_starter


def test_self_test_goes_through_the_screens_in_a_temporary_folder(tmp_path):
    garage = load_starter()
    before = db.DATA_DIR
    report = tmp_path / "report.txt"
    assert garage.self_test(report), report.read_text(encoding="utf-8")
    text = report.read_text(encoding="utf-8")
    assert "OK   sale PDF" in text and "OK   redact" in text and text.endswith("Self-test passed")
    assert db.DATA_DIR == before  # the real data folder is back


def test_self_test_reports_an_error(tmp_path, monkeypatch):
    garage = load_starter()
    monkeypatch.setattr(garage, "_self_test_steps", lambda *a: (_ for _ in ()).throw(RuntimeError("screen broken")))
    report = tmp_path / "report.txt"
    assert not garage.self_test(report)
    text = report.read_text(encoding="utf-8")
    assert "screen broken" in text and text.endswith("Self-test FAILED")
