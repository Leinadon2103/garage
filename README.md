# Garage – voertuigonderhoud

Een app voor op je eigen computer om het onderhoud van je motor, auto of ander voertuig bij te houden. Je bewaart er ook je facturen in, en bij verkoop maak je er een PDF mee met de complete historie.

## Installeren
Voer `build\installer\Garage-installatie-<versie>.exe` uit. Je hebt geen beheerdersrechten nodig.

Daarna staat **Garage** in het startmenu, en desgewenst ook op het bureaublad. Garage is een eigen Windows-programma met een eigen venster en icoon. De schermen worden getoond met WebView2, dat standaard in Windows 11 zit.

**Verwijderen** gaat via *Instellingen > Apps > Garage*. Je krijgt dan de vraag of je je gegevens wilt bewaren. Standaard blijven ze staan.

## Gegevens en back-up
Het geïnstalleerde programma bewaart alles in **Documenten\Garage**: de database (`onderhoud.db`), de facturen en de voertuigafbeeldingen. Maak een back-up door die map te kopiëren.

Welke gegevens Garage bewaart en wat er naar buiten gaat, staat in [PRIVACY.md](PRIVACY.md) en in de app bij *Instellingen > Privacy*.

## Ontwikkelen en bouwen
- `start.bat` start de app in je browser, met de gegevens in `data\` van deze projectmap.
- Met `.venv\Scripts\pythonw.exe Garage.pyw` start je hem in een eigen venster, ook met de gegevens in `data\`.
- **Pakketten** staan met vaste versies en hashes in `requirements.txt` (app en tests) en `requirements-bouwen.txt` (PyInstaller, pip-audit). Pas `requirements.in` of `requirements-bouwen.in` aan en genereer daarna opnieuw:
  ```
  .venv\Scripts\pip-compile --generate-hashes --strip-extras -o requirements.txt requirements.in
  .venv\Scripts\pip-compile --generate-hashes --strip-extras --allow-unsafe -o requirements-bouwen.txt requirements-bouwen.in
  ```
  Dependabot (`.github/dependabot.yml`) meldt wekelijks nieuwe versies.
- `bouwen.bat` doet het volgende:
  - controleert de pakketten op bekende lekken (`pip-audit`) en de code op onveilige constructies (`bandit`, via `uvx`; [uv](https://docs.astral.sh/uv/) is dus nodig);
  - draait de tests;
  - bouwt `Garage.exe` met PyInstaller;
  - bouwt het installatieprogramma met Inno Setup 6, in `build\installer\`. Pas het versienummer aan in `installer.iss` en `tools\versie.txt`.

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

## Licentie
AGPL-3.0, zie [LICENSE](LICENSE). Waarom, en de licenties van de gebruikte pakketten: [LICENTIES.md](LICENTIES.md).
