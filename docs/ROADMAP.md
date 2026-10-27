# Garage roadmap: GDPR, governance, security and features

*Nederlands: [ROADMAP.nl.md](ROADMAP.nl.md)*

Status: October 2026, after version 1.3.0. This document is the handover to a new work session: what exists, what still needs doing, and in which order. Every item has a code (e.g. **P1**) so we can refer to it.

## Working agreements
- **Language:** code in English (names, comments, docstrings, database). Texts the user sees in Dutch (screens, notifications, sale PDF, folders and files in Documents\Garage). Documentation in two languages: `X.md` English, `X.nl.md` Dutch, linking to each other. Discussions with Daniel are in Dutch. No em dash (long dash). Terms: [glossary.md](glossary.md).
- **No SecondBrain vault:** this is a private project; do not read or write anything in the vault, not even when a hook asks for it.
- **Git:** Claude does not commit or push; prepare the changes, Daniel decides. Never a Co-Authored-By line.
- **Branches and PRs:** `main` is protected by the ruleset "Protect main" (Settings > Rules): changes only through a pull request, the CI job `check` must pass on the latest `main`, merging only with *Squash and merge*, no force push or deletion, nobody on the bypass list. Way of working: `git switch -c <name>`, commit, `git push -u origin <name>`, open a PR, merge when `check` is green, then `git switch main`, `git pull` and `git fetch --prune`. Renaming the CI job means updating the ruleset too.
- **Do not read real data:** the app uses `Projecten\Garage-data` (through the environment variable `GARAGE_DATA`; the old name `ONDERHOUD_DATA` still works). Testing uses `data\` (`start.bat`) or the pytest fixtures.
- **Every change:** add tests, `pytest` green (warnings count as errors) and CI green; for a release `build.bat` (pip-audit, bandit, tests, exe with self-test, installer).
- **Python 3.15:** the code uses `lazy import` (PEP 810), so 3.15 is required. `pyyaml` has no wheel for 3.15 yet; so bandit runs on 3.13 via `tools/bandit_scan.py`, which also makes it read `lazy import` (before, bandit silently skipped main.py, web.py and image.py). Dependabot compiles on Linux, so Windows-only packages are listed explicitly in `requirements*.in`. It does not know 3.15 yet either: generate `requirements*.txt` with `uvx --python 3.14 --from pip-tools pip-compile` (see `.github/dependabot.yml`).

## What exists (1.3.0 and after)
- Server running locally (`127.0.0.1`), with a host check against DNS rebinding, a CSRF check (`Sec-Fetch-Site`/`Origin`, no `null`) and security headers. The CSP still allows `'unsafe-inline'`.
- File paths and mime types are checked, also when restoring a backup. Zip slip and zip bombs are caught.
- Redaction is real (`apply_redactions` + `scrub`). An unreviewed invoice never ends up in the sale PDF. EXIF is removed from photos.
- A privacy overview in the app and in `PRIVACY.md`. Sketchfab only loads after a click.
- Packages are pinned with hashes, with pip-audit, bandit and Dependabot (also for the GitHub Actions). License AGPL-3.0, with `LICENSES.md`.
- **Built after 1.3.0:** step 1 (S2, S3, G1, G2, G3), P9, P10 and G9, and the conversion to English code (migration 2). CI runs on GitHub; the repo is public and `main` is protected (see Working agreements).

## Legal framework (estimate, not legal advice)
- For personal use, Garage falls under the **household exemption** (Art. 2(2)(c) GDPR).
- If Daniel gives the app to others, he does not become a controller as long as no data reaches him: no telemetry, no server. Keep it that way, also with new features (see F8).
- What does count is **privacy by design (Art. 25)**: data minimisation, security, transparency, and data that really disappears. That also applies to third-party data: the mechanic on an invoice, the previous owner on the registration card, the buyer of the sale PDF.
- A DPIA is not needed as long as the app stays local. If synchronisation or a cloud feature ever comes, reassess this.

## Backlog per area
Done is ~~struck through~~.

### Privacy / GDPR
| Code | What | Why | Size |
|---|---|---|---|
| **P1** | Encrypt originals, documents and redaction terms with Windows DPAPI, with a migration of the existing files | The data is now readable on disk, often in OneDrive (Documents). Encryption also makes deletion effective ("crypto-shredding"). | large |
| **P2** | Backup with a password (AES zip or `cryptography`) | A DPAPI file cannot be restored on another pc; the backup must be able to. Now everything goes to OneDrive readable. | medium |
| **P3** | "Vehicle sold" button: make the export, then erase originals and documents; choose between an anonymised archive or everything gone; optionally clean up old backups | Storage limitation: after a sale the originals are no longer needed | medium |
| **P4** | "Erase everything" (e.g. when selling the pc), including the list of backups | Right to erasure, made practical | small |
| **P5** | Better redaction: ask for name and address at the first start; extra patterns (customer number, date of birth, license plate optionally); a warning without private terms | Names are now only found through own private terms | medium |
| **P6** | Service notes appear in the sale PDF: a hint in the form, plus the choice "hide notes" in the export | Free text can contain personal data | small |
| **P7** | Configurable retention: backups (now 10 + 3 safety copies), clean up expired documents after X months | Storage limitation, transparent | medium |
| **P8** | OCR without a temporary file in `%TEMP%`: pass the image to PowerShell through stdin | After a crash a copy of an invoice is now left behind | small |
| ~~**P9**~~ | ~~`PRAGMA secure_delete = ON` plus a one-off `VACUUM`~~ | Deleted rows stayed in free pages of the database and went into backups | done |
| ~~**P10**~~ | ~~No original file name on the error pages of the sale PDF~~ | A file name can contain a name | done |

### Data governance
| Code | What | Why | Size |
|---|---|---|---|
| ~~**G1**~~ | ~~Data register~~ ([data-register.md](data-register.md)) | Record of processing and basis for P1, P7 and the privacy text | done |
| ~~**G2**~~ | ~~Data dictionary~~ ([data-dictionary.md](data-dictionary.md)), guarded by a test | Maintainability; the link between code and meaning | done |
| ~~**G3**~~ | ~~Schema versions with `PRAGMA user_version` and numbered migrations~~ | Predictable upgrades and restoring old backups | done |
| **G4** | Origin per odometer reading and read field (RDW / invoice / OCR / manual) and "entered on" next to "date performed"; show in the export | A buyer can see what is proven and what was entered afterwards | medium |
| **G5** | Data quality screen: services without an invoice, unreviewed invoices, km gaps, files without a database row (orphans) and vice versa, periodic `PRAGMA integrity_check` | Make quality visible; orphans are a privacy risk too | medium |
| **G6** | Authenticity of the export: SHA-256 and export date on the cover page, plus a simple check ("is this PDF genuine?") | Trust of the buyer | small |
| **G7** | Change log (what was changed or deleted when), without contents of personal data | Traceability; supports G4 | medium |
| **G8** | Export of all data to CSV/Excel (without originals) | Data portability in a readable format, next to the zip | small |
| ~~**G9**~~ | ~~Clean up leftovers after a crash at startup (`.herstel-tmp\`, `.oud-*\`, `*.zip.deel`), restoring an unfinished restore~~ | Full copies of the data were otherwise left behind | done |

### Security
| Code | What | Why | Size |
|---|---|---|---|
| **S1** | Strict CSP: move all `onclick`/`onsubmit` to `static/app.js`, then `script-src 'self'` | Injected code can then no longer run | medium |
| ~~**S2**~~ | ~~GitHub Actions (Windows runner): tests, pip-audit and bandit on every push and Dependabot PR~~ | Dependabot PRs were not tested anywhere | done |
| ~~**S3**~~ | ~~Smoke test of the exe: `Garage.exe --self-test`, in `build.bat`~~ | The built exe was never tested automatically | done |
| **S4** | A random session token in a cookie, set through the start URL | Other Windows users on the same pc can now reach the server | small |
| **S5** | Signing the exe (Azure Trusted Signing, about €10 per month), SHA-256 with releases, `SECURITY.md` | Authenticity for users, no SmartScreen warning | medium, costs money |
| **S6** | Open PDFs in a separate process with a time limit | A malicious PDF can then not make the app hang or crash | medium |

### Features
| Code | What | Value | Size |
|---|---|---|---|
| **F1** | Calendar export (`.ics`) of planned maintenance, APK and expiring documents | Reminders in your own calendar, fully offline | small |
| **F2** | Refuelling and consumption (l/100 km, km/l), cost per km including fuel | A popular feature in maintenance apps; a more complete cost picture | medium |
| **F3** | APK history and defects from RDW Open Data, and details of recalls | More value in the sale PDF, without extra personal data | medium |
| **F4** | Search and filter in the history (type, garage, period, amount) | Convenience with a long history | small |
| **F5** | Total cost of ownership (TCO): depreciation, insurance, tax and maintenance per year | Insight | medium |
| **F6** | Import the previous owner's history (their sale PDF as an attachment to the vehicle) | Continuous history; mind the previous owner's personal data (redaction) | medium |
| **F7** | Parts and consumables (oil, filters) with part numbers; list "needed for the next service" | Practical for people who work on their own vehicle | medium |
| **F8** | Notify about a new version through GitHub Releases, only with consent | Updates without telemetry | small |
| **F9** | Accessibility (WCAG 2.2: keyboard, contrast, screen reader) | Quality, and needed if others start using the app | medium |

### Code quality and professionalisation
| Code | What | Why | Size |
|---|---|---|---|
| **K1** | Split `main.py` into routers per subject: vehicles, services, invoices, image | Overview; the file is about 500 lines | medium |
| **K2** | `ruff` (formatting and lint) and a type check (`pyright`) in `build.bat` and CI | Catch errors earlier | small |
| **K3** | Verify a backup after making it: open the zip and run `PRAGMA integrity_check` | The backup is the only copy of the originals; P2 builds on it | small |
| **K4** | Build the exe and installer in CI, with SHA-256 and a GitHub artifact attestation | Provably built from this source code; the free part of S5 | medium |
| **K5** | `CHANGELOG.md`, one place for the version number, git tags | Traceable releases | small |
| **K6** | Short threat model and recorded design decisions (e.g. DPAPI or password) | Choices in P1/P2 and S4 can be explained | small |
| **K7** | Rule and test: `app.log` contains no invoice contents or redaction terms | Otherwise log files leak personal data | small |
| **K8** | License check (e.g. `pip-licenses`) and SBOM in CI | `LICENSES.md` does not go stale unnoticed; AGPL obligations | small |
| **K9** | Coverage (`pytest-cov`) | See which paths have no test, especially in restoring and migrating | small |
| **K10** | Evaluate PyMuPDF 2: it pulls in onnxruntime, numpy, pyyaml and more (no Python 3.15 wheels yet, much larger exe). Dependabot ignores it until then (`.github/dependabot.yml`) | Security fixes only land in 2.x at some point | medium |

## Suggested order
1. ~~**Foundation:** S2, S3, G1, G2, G3.~~ Done, together with P9, P10, G9 and the conversion to English code.
2. **Biggest privacy gain:** K3, then P1 + P2 (designed together, including migration and restore), then P3, P4 and P6.
3. **Trust for the buyer:** G4, G6, G7, and with it F3.
4. **Finishing:** S1, P5, P7, P8, G5, G8, S4, K1, K2.
5. **Professionalisation, spread out:** K4 to K9.
6. **Features by choice:** F1, F2 and F4 are quick and valuable; the rest on request.

## Open decisions for Daniel
1. ~~Will the GitHub repo be public?~~ Yes, the repo is public. Still open: will the app be actively given to others? That decides S5, F8 and F9.
2. P1: DPAPI (no password, tied to the Windows account) or an own password at startup?
3. P3: after a sale erase everything or keep an anonymised archive?
4. Which features (F1 to F9) are preferred?
5. Does Python 3.15 remain the base? The ecosystem is still catching up (see pyyaml, Dependabot), but `lazy import` depends on it.
6. Which language for commit messages: English (fits the code) or Dutch?
