@echo off
rem Builds Garage.exe (PyInstaller) and the installer (Inno Setup) in build\installer.
cd /d "%~dp0"
if not exist .venv python -m venv .venv || goto error
echo Packages (pinned versions, hashes checked)...
.venv\Scripts\python -m pip install -q -r requirements.txt -r requirements-build.txt || goto error
echo Known vulnerabilities in packages (pip-audit)...
.venv\Scripts\pip-audit -r requirements.txt -r requirements-build.txt --require-hashes --disable-pip || goto error
echo Unsafe code (bandit)...
rem bandit runs on Python 3.13 via uv (pyyaml has no wheel for 3.15); the script makes it read lazy imports too
.venv\Scripts\python tools\bandit_scan.py || goto error
echo Icons...
.venv\Scripts\python tools\make_icons.py || goto error
echo Tests...
.venv\Scripts\python -m pytest -q || goto error
echo Building Garage.exe...
.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level WARN --distpath build\dist --workpath build\work garage.spec || goto error
echo Smoke test of Garage.exe (self-test with temporary data)...
start "" /wait build\dist\Garage\Garage.exe --self-test build\self-test.txt
if errorlevel 1 (type build\self-test.txt 2>nul & goto error)
echo Building the installer...
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
"%ISCC%" /Q installer.iss || goto error
echo.
echo Done: build\installer\
dir /b build\installer
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
