# Roadmap Garage: AVG, governance, beveiliging en features

Stand: oktober 2026, na versie 1.3.0. Dit document is de overdracht naar een nieuwe werksessie: wat er al is, wat er nog moet, en in welke volgorde. Elk punt heeft een code (bijv. **P1**) zodat we ernaar kunnen verwijzen.

## Werkafspraken
- **Taal:** Nederlands, ook in code-commentaar en commits. Geen em-dash (lang streepje).
- **Geen SecondBrain-vault:** dit is een privéproject; niets lezen of schrijven in de vault, ook niet als een hook erom vraagt.
- **Git:** Claude commit of pusht niet zelf; wijzigingen klaarzetten, Daniel beslist. Nooit een Co-Authored-By-regel.
- **Echte gegevens niet lezen:** de app gebruikt `Projecten\Garage-data` (via de omgevingsvariabele `ONDERHOUD_DATA`). Testen gebeurt met `data\` (`start.bat`) of met de pytest-fixtures.
- **Elke wijziging:** tests erbij, `pytest` groen, en voor een release `bouwen.bat` (pip-audit, bandit via `uvx`, tests, exe, installatieprogramma).
- **Python 3.15:** de code gebruikt `lazy import` (PEP 810), dus 3.15 is vereist. `pyyaml` heeft nog geen wheel voor 3.15; daarom draait bandit via `uvx --python 3.13`.

## Wat er al is (1.3.0)
- Lokaal draaiende server (`127.0.0.1`), met een host-check tegen DNS-rebinding, een CSRF-check (`Sec-Fetch-Site`/`Origin`, geen `null`) en beveiligingsheaders. De CSP staat nog `'unsafe-inline'` toe.
- Bestandspaden en mime-types worden gecontroleerd, ook bij het terugzetten van een back-up. Zip-slip en zip-bommen worden afgevangen.
- Weglakken is echt (`apply_redactions` + `scrub`). Een ongecontroleerde factuur komt nooit in de verkoop-PDF. Van foto's wordt EXIF verwijderd.
- Een privacyoverzicht in de app en in `PRIVACY.md`. Sketchfab laadt pas na een klik.
- Pakketten liggen vast met hashes, met pip-audit, bandit en Dependabot. Licentie AGPL-3.0, met `LICENTIES.md`.
- Nog niet gecommit: de beeld-url bevat een versie, zodat een nieuwe foto direct zichtbaar is.

## Juridisch kader (inschatting, geen juridisch advies)
- Voor eigen gebruik valt Garage onder de **huishoudelijke uitzondering** (art. 2 lid 2 sub c AVG).
- Geeft Daniel de app aan anderen, dan wordt hij geen verwerkingsverantwoordelijke zolang er geen gegevens bij hem binnenkomen: geen telemetrie, geen server. Houd dat zo, ook bij nieuwe features (zie F8).
- Wat wel telt, is **privacy by design (art. 25)**: dataminimalisatie, beveiliging, transparantie, en gegevens die echt verdwijnen. Dat geldt ook voor gegevens van derden: de monteur op een factuur, de vorige eigenaar op de kentekencard, de koper van de verkoop-PDF.
- Een DPIA is niet nodig zolang de app lokaal blijft. Komt er ooit synchronisatie of een cloudfunctie, dan dit opnieuw beoordelen.

## Backlog per invalshoek

### Privacy / AVG
| Code | Wat | Waarom | Omvang |
|---|---|---|---|
| **P1** | Originelen, documenten en lak-termen versleutelen met Windows DPAPI, met een migratie van de bestaande bestanden | De gegevens staan nu leesbaar op schijf, vaak in OneDrive (Documenten). Versleutelen maakt verwijderen ook effectief ("crypto-shredding"). | groot |
| **P2** | Back-up met een wachtwoord (AES-zip of `cryptography`) | Een DPAPI-bestand is op een andere pc niet terug te zetten; de back-up moet dat wel kunnen. Nu gaat alles leesbaar naar OneDrive. | middel |
| **P3** | Knop "Voertuig verkocht": export maken, dan originelen en documenten wissen; kiezen tussen een geanonimiseerd archief of alles weg; optioneel oude back-ups opschonen | Opslagbeperking: na verkoop zijn de originelen niet meer nodig | middel |
| **P4** | "Alles wissen" (bijv. bij verkoop van de pc), inclusief de lijst met back-ups | Recht op vergetelheid, praktisch gemaakt | klein |
| **P5** | Beter lakken: bij de eerste start om naam en adres vragen; extra patronen (klantnummer, geboortedatum, kenteken naar keuze); een waarschuwing zonder privé-termen | Namen worden nu alleen gevonden via eigen privé-termen | middel |
| **P6** | Opmerkingen bij beurten staan in de verkoop-PDF: melding in het formulier, plus de keuze "opmerkingen niet tonen" bij de export | Vrije tekst kan persoonsgegevens bevatten | klein |
| **P7** | Bewaartermijnen instelbaar: back-ups (nu 10 + 3 veiligheidskopieën), verlopen documenten na X maanden opruimen | Opslagbeperking, transparant | middel |
| **P8** | OCR zonder tijdelijk bestand in `%TEMP%`: de afbeelding via stdin aan PowerShell geven | Bij een crash blijft nu een kopie van een factuur achter | klein |
| **P9** | `PRAGMA secure_delete = ON` (of `VACUUM` na verwijderen) | Verwijderde rijen blijven anders in vrije pagina's van de database en gaan mee in back-ups (G1, bevinding 1) | klein |
| **P10** | Geen oorspronkelijke bestandsnaam op de foutpagina's van de verkoop-PDF | Een bestandsnaam kan een naam bevatten (G1, bevinding 2) | klein |

### Data governance
| Code | Wat | Waarom | Omvang |
|---|---|---|---|
| **G1** | Gegevensregister (`docs/gegevens.md`): per gegeven de bron, het doel, de gevoeligheid, de plek (db, mappen, back-up, OneDrive, log, temp) en de bewaartermijn | Verwerkingsoverzicht en basis voor P1, P7 en de privacytekst | klein |
| **G2** | Datadictionary van alle tabellen en kolommen (betekenis, toegestane waarden, herkomst) | Onderhoudbaarheid; de koppeling tussen code en betekenis | klein |
| **G3** | Schemaversies met `PRAGMA user_version` en genummerde migraties, in plaats van `NIEUWE_KOLOMMEN` | Voorspelbare upgrades en terugzetten van oude back-ups | middel |
| **G4** | Herkomst per km-stand en uitgelezen veld (RDW / factuur / OCR / handmatig) en "ingevoerd op" naast "datum uitgevoerd"; in de export tonen | Een koper kan zien wat bewezen is en wat achteraf is ingevoerd | middel |
| **G5** | Datakwaliteitsscherm: beurten zonder factuur, ongecontroleerde facturen, km-gaten, bestanden zonder database-regel (wezen) en andersom, periodiek `PRAGMA integrity_check` | Kwaliteit zichtbaar maken; wezen zijn ook een privacyrisico | middel |
| **G6** | Echtheid van de export: SHA-256 en exportdatum op het voorblad, plus een eenvoudige controle ("klopt deze PDF?") | Vertrouwen van de koper | klein |
| **G7** | Wijzigingslog (wat is wanneer aangepast of verwijderd), zonder inhoud van persoonsgegevens | Herleidbaarheid; ondersteunt G4 | middel |
| **G8** | Export naar CSV/Excel van alle gegevens (zonder originelen) | Dataportabiliteit in een leesbaar formaat, naast de zip | klein |
| **G9** | Restanten na een crash opruimen bij het starten: `.herstel-tmp\`, `.oud-*\`, `*.zip.deel` | Volledige kopieën van de gegevens blijven anders staan (G1, bevinding 3); past bij G5 | klein |

### Beveiliging
| Code | Wat | Waarom | Omvang |
|---|---|---|---|
| **S1** | Strikte CSP: alle `onclick`/`onsubmit` naar `static/app.js`, dan `script-src 'self'` | Ingevoegde code kan dan niet meer draaien | middel |
| **S2** | GitHub Actions (Windows-runner): tests, pip-audit en bandit bij elke push en Dependabot-PR | Dependabot-PR's worden nu nergens getest | klein |
| **S3** | Rooktest van de exe: `Garage.exe --zelftest` start de server zonder venster, roept de belangrijkste pagina's aan en stopt; draaien in `bouwen.bat` | De gebouwde exe wordt nu nooit automatisch getest (lazy imports, uitgesloten Pillow-delen) | klein |
| **S4** | Een willekeurig sessietoken in een cookie, gezet via de start-URL | Andere Windows-gebruikers op dezelfde pc kunnen nu bij de server | klein |
| **S5** | Ondertekenen van de exe (Azure Trusted Signing, ca. €10 per maand), SHA-256 bij releases, `SECURITY.md` | Echtheid voor gebruikers, geen SmartScreen-waarschuwing | middel, kost geld |
| **S6** | PDF's openen in een apart proces met een tijdslimiet | Een kwaadaardige PDF kan de app dan niet laten hangen of crashen | middel |

### Features
| Code | Wat | Waarde | Omvang |
|---|---|---|---|
| **F1** | Agenda-export (`.ics`) van gepland onderhoud, APK en verlopende documenten | Herinneringen in je eigen agenda, volledig offline | klein |
| **F2** | Tankbeurten en verbruik (l/100 km, km/l), kosten per km inclusief brandstof | Een veelgevraagde functie in onderhoudsapps; een completer kostenbeeld | middel |
| **F3** | APK-historie en gebreken uit RDW Open Data, en details van terugroepacties | Meer waarde in de verkoop-PDF, zonder extra persoonsgegevens | middel |
| **F4** | Zoeken en filteren in de historie (type, garage, periode, bedrag) | Gemak bij lange historie | klein |
| **F5** | Totale kosten van bezit (TCO): afschrijving, verzekering, belasting en onderhoud per jaar | Inzicht | middel |
| **F6** | Historie van de vorige eigenaar importeren (diens verkoop-PDF als bijlage bij het voertuig) | Doorlopende historie; let op persoonsgegevens van de vorige eigenaar (lakken) | middel |
| **F7** | Onderdelen en verbruiksmaterialen (olie, filters) met artikelnummers; lijst "nodig voor de volgende beurt" | Praktisch voor wie zelf sleutelt | middel |
| **F8** | Melden van een nieuwe versie via GitHub Releases, alleen na toestemming | Updates zonder telemetrie | klein |
| **F9** | Toegankelijkheid (WCAG 2.2: toetsenbord, contrast, schermlezer) | Kwaliteit, en nodig als anderen de app gaan gebruiken | middel |

### Codekwaliteit
- **K1:** `main.py` (651 regels) opsplitsen in routers per onderwerp: voertuigen, beurten, facturen, beeld.
- **K2:** `ruff` (opmaak en lint) en een typecontrole (`pyright`) in `bouwen.bat` en CI.

## Voorgestelde volgorde
1. **Fundament:** S2, S3, G1, G2, G3. Eerst het vangnet (CI en een rooktest van de exe) en de documentatie van wat er is; G3 is nodig voordat P1 het schema verandert. *Gebouwd, nog niet gecommit; CI nog niet op GitHub gedraaid.*
2. **Grootste privacywinst:** P1 + P2 (samen ontwerpen, inclusief migratie en terugzetten), daarna P3, P4 en P6. *P9, P10 en G9 zijn al gebouwd (nog niet gecommit).*
3. **Vertrouwen voor de koper:** G4, G6, G7, en daarbij F3.
4. **Afronden:** S1, P5, P7, P8, G5, G8, S4.
5. **Features naar keuze:** F1, F2 en F4 zijn snel en waardevol; de rest op aanvraag.

## Open beslissingen voor Daniel
1. Gaat de app naar anderen, en wordt de GitHub-repo dan openbaar? Dat bepaalt de AGPL, S5, F8 en F9.
2. P1: DPAPI (geen wachtwoord, gekoppeld aan het Windows-account) of een eigen wachtwoord bij het opstarten?
3. P3: na verkoop alles wissen of een geanonimiseerd archief bewaren?
4. Welke features (F1 t/m F9) hebben de voorkeur?
5. Blijft Python 3.15 de basis? Het ecosysteem loopt nog achter (zie pyyaml), maar `lazy import` hangt ervan af.
