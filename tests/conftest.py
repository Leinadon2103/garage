"""Safety net: tests never get near the real data, whatever GARAGE_DATA / ONDERHOUD_DATA say on this pc.

pytest loads this file before the test modules import app.db, so the default data folder (db.DATA_DIR) is a
throwaway folder. Tests that need data set db.DATA_DIR to their own tmp_path; when a test undoes that
(monkeypatch.undo), it falls back to this folder and not to the real data.
"""
import os
import tempfile

os.environ["GARAGE_DATA"] = tempfile.mkdtemp(prefix="garage-tests-")
os.environ.pop("ONDERHOUD_DATA", None)
