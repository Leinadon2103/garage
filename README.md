# Garage – vehicle maintenance

*Nederlands: [README.nl.md](README.nl.md)*

An app for your own computer to keep track of the maintenance of your motorcycle, car or other vehicle. You also keep your invoices in it, and when you sell, it makes a PDF with the complete history.

The app itself is in Dutch: it is made for Dutch vehicles (RDW license plate lookup, APK). The code and the documentation are in English, with a Dutch version of each document.

## Installing
Run `build\installer\Garage-installatie-<version>.exe`. No admin rights are needed.

Afterwards **Garage** is in the start menu, and optionally on the desktop. Garage is a Windows program with its own window and icon. The screens are shown with WebView2, which is part of Windows 11.

**Uninstalling** goes through *Settings > Apps > Garage*. You are then asked whether to keep your data. By default it stays.

## Data and backup
The installed program stores everything in **Documents\Garage**: the database (`onderhoud.db`), the invoices and the vehicle images. Folder and file names there are Dutch, because you see them in Explorer.

Which data Garage stores and what goes outside is in [PRIVACY.md](PRIVACY.md) and in the app under *Instellingen > Privacy*.

## Developing and building
- `start.bat` starts the app in your browser, with the data in `data\` of this project folder.
- `.venv\Scripts\pythonw.exe Garage.pyw` starts it in its own window, also with the data in `data\`. The environment variable `GARAGE_DATA` points to another data folder.
- **Packages** are pinned with versions and hashes in `requirements.txt` (app and tests) and `requirements-build.txt` (PyInstaller, pip-audit). Change `requirements.in` or `requirements-build.in` and regenerate with Python 3.14 (Dependabot does not know 3.15 yet; the versions are the same):
  ```
  uvx --python 3.14 --from pip-tools pip-compile --generate-hashes --strip-extras --output-file=requirements.txt requirements.in
  uvx --python 3.14 --from pip-tools pip-compile --allow-unsafe --generate-hashes --strip-extras --output-file=requirements-build.txt requirements-build.in
  ```
  Dependabot (`.github/dependabot.yml`) reports new versions weekly.
- `build.bat` does the following:
  - checks the packages for known vulnerabilities (`pip-audit`) and the code for unsafe constructs (`bandit` via `tools/bandit_scan.py` and `uvx`; so [uv](https://docs.astral.sh/uv/) is needed);
  - runs the tests;
  - builds `Garage.exe` with PyInstaller and runs its self-test (`Garage.exe --self-test`);
  - builds the installer with Inno Setup 6, in `build\installer\`. Change the version number in `installer.iss` and `tools\version.txt`.
- **CI** (`.github/workflows/ci.yml`) runs the same checks and the tests on every push to `main` and every pull request. `main` is protected: changes go through a pull request, which can only be merged when CI passes.

More: [docs/](docs/) has the [roadmap](docs/ROADMAP.md), the [data register](docs/data-register.md), the [data dictionary](docs/data-dictionary.md) and a [glossary](docs/glossary.md) of Dutch and English terms.

## What it does
- **Add a vehicle by license plate.** Make, model, year, fuel, power and APK date come from RDW Open Data. You also see whether there is an open recall, and whether it is an imported vehicle.
- **Load an invoice.** For a PDF with text, amount, date, odometer reading, garage and work done are filled in automatically. For photos and scans you fill it in yourself.
- **Redact personal data** on invoices before they go into the sale PDF. The original is kept for you only.
- **Due soon:** per maintenance item you see when it is due again, by km or time, whichever comes first.
  - By default general rules of thumb apply.
  - Through *Onderhoudsschema* (maintenance schedule) on the vehicle page you enter the intervals from your owner's manual.
- **Printout for sale:** one PDF with a cover page (photo, summary, odometer history, specs), the history and all redacted invoices as attachments. If you choose "hide costs", the amounts are redacted on the invoices themselves too.
- **Note the odometer reading** between services: the planning stays correct and the buyer sees a continuous odometer history.
- **Text recognition (OCR)** on photos and scans, with the built-in Windows OCR (offline). Reading, redaction suggestions and hiding amounts therefore work for photos too.
- **Automatic backup:** a zip file every week to OneDrive or a folder of your choice; restore through Settings. A safety copy is always made first.
- **Reminders:** when signing in to Windows, a notification if maintenance, the APK or a document is due. At most one notification per day.
- **Costs and odometer history**, **documents** (registration, policy; they never go into the export), **specs** (oil, tyres, chain) and **storage** with checklists.
- **Theme:** light, dark or like Windows. Switching is smooth, and the title bar follows.

## About the odometer reading
The RDW does not publish the odometer reading itself. The app only shows the RDW's judgement of the reading (e.g. "Logisch") and the year in which it was last registered.

As long as you have not entered a reading yourself, you see a rough estimate: the age of the vehicle times the national average km per year.

## Tests
```
.venv\Scripts\python -m pytest
```
Warnings count as errors (see `pytest.ini`).

## License
AGPL-3.0, see [LICENSE](LICENSE). Why, and the licenses of the packages used: [LICENSES.md](LICENSES.md).
