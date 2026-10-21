@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Eerste keer: virtuele omgeving maken...
    python -m venv .venv
)
echo Pakketten controleren...
.venv\Scripts\python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
