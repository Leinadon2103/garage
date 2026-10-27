# Licenties

*English: [LICENSES.md](LICENSES.md)*

Garage valt onder de **GNU Affero General Public License v3.0** (zie [LICENSE](LICENSE)).
Copyright (C) 2026 Daniel Goedknegt.

## Waarom AGPL?
Garage gebruikt PyMuPDF voor het weglakken en uitlezen van PDF's. PyMuPDF heeft twee licenties: de AGPL-3.0 of een betaalde licentie van Artifex. Wie Garage (of `Garage.exe`) aan anderen geeft, moet dus de broncode onder de AGPL beschikbaar stellen. De broncode staat op https://github.com/Leinadon2103/garage.

## Gebruikte pakketten
Deze pakketten zitten in `Garage.exe` (versies zoals vastgelegd in `requirements.txt`). Alle andere licenties zijn verenigbaar met de AGPL.

| Pakket | Licentie |
|---|---|
| PyMuPDF | AGPL-3.0 of Artifex Commercial License |
| certifi | MPL-2.0 |
| opentelemetry-api, python-multipart | Apache-2.0 |
| packaging | Apache-2.0 of BSD-2-Clause |
| cryptography | Apache-2.0 of BSD-3-Clause |
| typing-extensions | PSF-2.0 |
| pillow | MIT-CMU |
| click, colorama, httpcore, httpx, idna, jinja2, markupsafe, pycparser, pygments, pypdf, pywebview, reportlab, starlette, uvicorn | BSD |
| annotated-doc, annotated-types, anyio, bottle, cffi, charset-normalizer, clr-loader, fastapi, h11, proxy-tools, pydantic, pydantic-core, pythonnet, typing-inspection | MIT |

Daarnaast:
- **Python** zelf (PSF-licentie) zit in `Garage.exe`.
- **PyInstaller** bouwt de exe. De GPL-licentie van PyInstaller staat met een uitzondering toe dat je het resultaat onder een eigen licentie verspreidt.
- **pytest**, **httpx2** (met httpcore2 en truststore), **pip-audit** en **bandit** worden alleen gebruikt bij het ontwikkelen en zitten niet in de exe.

Bijwerken: na een wijziging in `requirements.txt` deze lijst nalopen (licenties staan in de metadata van elk pakket).
