@echo off
rem Bouwt Garage.exe (PyInstaller) en het installatieprogramma (Inno Setup) in build\installer.
cd /d "%~dp0"
if not exist .venv python -m venv .venv || goto fout
echo Pakketten (vaste versies, hashes gecontroleerd)...
.venv\Scripts\python -m pip install -q -r requirements.txt -r requirements-bouwen.txt || goto fout
echo Bekende lekken in pakketten (pip-audit)...
.venv\Scripts\pip-audit -r requirements.txt -r requirements-bouwen.txt --require-hashes --disable-pip || goto fout
echo Onveilige code (bandit)...
rem bandit draait los via uv: pyyaml heeft nog geen wheel voor de Python van .venv
where uvx >nul 2>&1 || (echo uv ontbreekt: installeer uv of draai bandit zelf. & goto fout)
uvx --python 3.13 bandit==1.9.4 -q -r app Garage.pyw tools -ll || goto fout
echo Iconen...
.venv\Scripts\python tools\maak_iconen.py || goto fout
echo Tests...
.venv\Scripts\python -m pytest -q -p no:warnings || goto fout
echo Garage.exe bouwen...
.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level WARN --distpath build\dist --workpath build\werk garage.spec || goto fout
echo Installatieprogramma bouwen...
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
"%ISCC%" /Q installer.iss || goto fout
echo.
echo Klaar: build\installer\
dir /b build\installer
exit /b 0

:fout
echo.
echo Bouwen mislukt.
exit /b 1
