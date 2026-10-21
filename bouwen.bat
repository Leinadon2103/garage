@echo off
rem Bouwt Garage.exe (PyInstaller) en het installatieprogramma (Inno Setup) in build\installer.
cd /d "%~dp0"
if not exist .venv python -m venv .venv || goto fout
echo Pakketten...
.venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller || goto fout
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
