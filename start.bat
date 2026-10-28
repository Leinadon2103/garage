@echo off
cd /d "%~dp0"
rem Always the test data in data\, never the real data (GARAGE_DATA / ONDERHOUD_DATA may be set system-wide)
set "GARAGE_DATA=%~dp0data"
set "ONDERHOUD_DATA="
rem and backups of the test data in build\, never between the real backups in OneDrive (Garage keeps only 10)
set "GARAGE_BACKUPS=%~dp0build\test-backups"
if not exist .venv (
    echo First run: creating the virtual environment...
    python -m venv .venv
)
echo Checking packages...
.venv\Scripts\python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
