# Garage – voertuigonderhoud

*English: [README.md](README.md)*

Een app voor op je eigen computer om het onderhoud van je motor, auto of ander voertuig bij te houden. Je bewaart er ook je facturen in, en bij verkoop maak je er een PDF mee met de complete historie.

De app zelf is Nederlands: hij is gemaakt voor Nederlandse voertuigen (kenteken opzoeken bij de RDW, APK). De code en de documentatie zijn Engels, met van elk document een Nederlandse versie.

## Installeren
Voer `build\installer\Garage-installatie-<versie>.exe` uit. Je hebt geen beheerdersrechten nodig.

Daarna staat **Garage** in het startmenu, en desgewenst ook op het bureaublad. Garage is een eigen Windows-programma met een eigen venster en icoon. De schermen worden getoond met WebView2, dat standaard in Windows 11 zit.

**Verwijderen** gaat via *Instellingen > Apps > Garage*. Je krijgt dan de vraag of je je gegevens wilt bewaren. Standaard blijven ze staan.

## Gegevens en back-up
Het geïnstalleerde programma bewaart alles in **Documenten\Garage**: de database (`onderhoud.db`), de facturen en de voertuigafbeeldingen. De map- en bestandsnamen daar zijn Nederlands, omdat je ze in de Verkenner ziet.

Welke gegevens Garage bewaart en wat er naar buiten gaat, staat in [PRIVACY.nl.md](PRIVACY.nl.md) en in de app bij *Instellingen > Privacy*.

## Ontwikkelen en bouwen
- `start.bat` start de app in je browser, met de gegevens in `data\` van deze projectmap.
- Met `.venv\Scripts\pythonw.exe Garage.pyw` start je hem in een eigen venster, ook met de gegevens in `data\`. De omgevingsvariabele `GARAGE_DATA` wijst naar een andere gegevensmap.
- **Pakketten** staan met vaste versies en hashes in `requirements.txt` (app en tests) en `requirements-build.txt` (PyInstaller, pip-audit). Pas `requirements.in` of `requirements-build.in` aan en genereer daarna opnieuw met Python 3.14 (Dependabot kent 3.15 nog niet; de versies zijn gelijk):
  ```
  uvx --python 3.14 --from pip-tools pip-compile --generate-hashes --strip-extras --output-file=requirements.txt requirements.in
  uvx --python 3.14 --from pip-tools pip-compile --allow-unsafe --generate-hashes --strip-extras --output-file=requirements-build.txt requirements-build.in
  ```
  Dependabot (`.github/dependabot.yml`) meldt wekelijks nieuwe versies.
- `build.bat` doet het volgende:
  - controleert de pakketten op bekende lekken (`pip-audit`) en de code op onveilige constructies (`bandit` via `tools/bandit_scan.py` en `uvx`; [uv](https://docs.astral.sh/uv/) is dus nodig);
  - draait de tests;
  - bouwt `Garage.exe` met PyInstaller en draait de zelftest daarvan (`Garage.exe --self-test`);
  - bouwt het installatieprogramma met Inno Setup 6, in `build\installer\`. Pas het versienummer aan in `installer.iss` en `tools\version.txt`.
- **CI** (`.github/workflows/ci.yml`) draait dezelfde controles en de tests bij elke push naar `main` en elk pull request.

Verder: in [docs/](docs/) staan de [roadmap](docs/ROADMAP.nl.md), het [gegevensregister](docs/data-register.nl.md), de [datadictionary](docs/data-dictionary.nl.md) en een [woordenlijst](docs/glossary.md) met Nederlandse en Engelse termen.

## Wat kan het
- **Voertuig toevoegen via kenteken.** Merk, model, bouwjaar, brandstof, vermogen en APK-datum komen uit RDW Open Data. Je ziet ook of er een terugroepactie openstaat, en of het een importvoertuig is.
- **Factuur inladen.** Bij een PDF met tekst worden bedrag, datum, km-stand, garage en werkzaamheden automatisch ingevuld. Bij foto's en scans vul je het zelf in.
- **Persoonsgegevens weglakken** op facturen voordat ze in de verkoop-PDF komen. Het origineel blijft alleen voor jou bewaard.
- **Binnenkort nodig:** per onderhoud zie je wanneer het weer nodig is, op km of tijd, wat het eerst komt.
  - Standaard gelden algemene vuistregels.
  - Via *Onderhoudsschema* op de voertuigpagina vul je de intervallen uit je instructieboekje in.
- **Uitdraai voor verkoop:** één PDF met een voorblad (foto, samenvatting, km-verloop, specificaties), de historie en alle gelakte facturen als bijlage. Kies je "kosten niet tonen", dan worden de bedragen ook op de facturen zelf weggelakt.
- **Km-stand noteren** tussen beurten door: de planning blijft kloppen en de koper ziet een doorlopend km-verloop.
- **Tekstherkenning (OCR)** op foto's en scans, met de ingebouwde Windows-OCR (offline). Uitlezen, lak-suggesties en bedragen verbergen werken daardoor ook bij foto's.
- **Automatische back-up:** elke week een zip-bestand naar OneDrive of een eigen map; terugzetten via Instellingen. Er wordt altijd eerst een veiligheidskopie gemaakt.
- **Herinneringen:** bij het aanmelden in Windows een melding als er onderhoud, APK of een document verloopt. Hooguit één melding per dag.
- **Kosten en km-verloop**, **documenten** (kentekenbewijs, polis; die gaan nooit mee in de export), **specificaties** (olie, banden, ketting) en **stalling** met checklists.
- **Weergave:** licht, donker of zoals Windows. Het wisselen gaat vloeiend, en de titelbalk kleurt mee.

## Over de kilometerstand
De RDW maakt de kilometerstand zelf niet openbaar. De app toont alleen het oordeel van de RDW over de tellerstand (bijvoorbeeld "Logisch") en het jaar waarin die voor het laatst is geregistreerd.

Zolang je zelf nog geen km-stand hebt ingevoerd, zie je een ruwe schatting: de leeftijd van het voertuig keer het landelijk gemiddelde aantal km per jaar.

## Tests
```
.venv\Scripts\python -m pytest
```
Waarschuwingen tellen als fout (zie `pytest.ini`).

## Licentie
AGPL-3.0, zie [LICENSE](LICENSE). Waarom, en de licenties van de gebruikte pakketten: [LICENSES.nl.md](LICENSES.nl.md).
