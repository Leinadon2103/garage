# Licenses

*Nederlands: [LICENSES.nl.md](LICENSES.nl.md)*

Garage is licensed under the **GNU Affero General Public License v3.0** (see [LICENSE](LICENSE)).
Copyright (C) 2026 Daniel Goedknegt.

## Why AGPL?
Garage uses PyMuPDF for redacting and reading PDFs. PyMuPDF has two licenses: the AGPL-3.0 or a paid license from Artifex. Whoever gives Garage (or `Garage.exe`) to others must therefore make the source code available under the AGPL. The source code is at https://github.com/Leinadon2103/garage.

## Packages used
These packages are in `Garage.exe` (versions as pinned in `requirements.txt`). All other licenses are compatible with the AGPL.

| Package | License |
|---|---|
| PyMuPDF | AGPL-3.0 or Artifex Commercial License |
| certifi | MPL-2.0 |
| opentelemetry-api, python-multipart | Apache-2.0 |
| packaging | Apache-2.0 or BSD-2-Clause |
| cryptography | Apache-2.0 or BSD-3-Clause |
| typing-extensions | PSF-2.0 |
| pillow | MIT-CMU |
| click, colorama, httpcore, httpx, idna, jinja2, markupsafe, pycparser, pygments, pypdf, pywebview, reportlab, starlette, uvicorn | BSD |
| annotated-doc, annotated-types, anyio, bottle, cffi, charset-normalizer, clr-loader, fastapi, h11, proxy-tools, pydantic, pydantic-core, pythonnet, typing-inspection | MIT |

In addition:
- **Python** itself (PSF license) is in `Garage.exe`.
- **PyInstaller** builds the exe. PyInstaller's GPL license has an exception that allows distributing the result under your own license.
- **pytest**, **httpx2** (with httpcore2 and truststore), **pip-audit** and **bandit** are only used for development and are not in the exe.

Updating: after a change in `requirements.txt`, go through this list (licenses are in the metadata of each package).
