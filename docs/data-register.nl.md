# Gegevensregister

*English: [data-register.md](data-register.md)*

Welke gegevens Garage verwerkt, waar ze staan en hoe lang. Dit is het verwerkingsoverzicht achter [PRIVACY.nl.md](../PRIVACY.nl.md) en de basis voor versleuteling (P1), bewaartermijnen (P7) en wissen (P3, P4). De betekenis per tabel en kolom staat in de [datadictionary](data-dictionary.nl.md).

Stand: na versie 1.3.0, schemaversie 2. Werk dit bij als er een gegeven, een opslagplek of een bewaartermijn bijkomt.

## Opslagplekken
| Code | Plek | Toelichting |
|---|---|---|
| **db** | `onderhoud.db` in de gegevensmap | SQLite. Geïnstalleerd: `Documenten\Garage`, dus in OneDrive als Documenten daarheen is verplaatst. |
| **fac** | `facturen\<voertuig-id>\` | Origineel `<uuid>.<ext>`, gelakte versie `<uuid>-gelakt.<ext>` |
| **doc** | `documenten\<voertuig-id>\` | `<uuid>.<ext>` |
| **bld** | `beelden\` | `<uuid>.jpg`, zonder EXIF |
| **log** | `app.log` (+ `.1`, `.2`) in de gegevensmap | Foutmeldingen, max. 1 MB per deel |
| **bak** | `Garage back-ups\` | Zip met db + alle mappen. Standaard in OneDrive, anders in de gebruikersmap, of een zelfgekozen map. |
| **pdf** | Waar de gebruiker de verkoop-PDF opslaat | Standaard `Downloads`; bedoeld om te delen met een koper |
| **tmp** | `%TEMP%` en tijdelijke mappen in de gegevensmap | Zie [Tijdelijke kopieën](#tijdelijke-kopieën) |
| **win** | Meldingencentrum van Windows | Herinneringen (toasts) blijven daar staan tot de gebruiker ze wist |

Overal geldt: wat in **db**, **fac**, **doc** of **bld** staat, staat ook in **bak**.

## Register
Gevoeligheid: **hoog** = direct herleidbaar tot een persoon (naam, adres, contactgegevens, documenten); **middel** = indirect herleidbaar of vrije tekst die persoonsgegevens kan bevatten; **laag** = gegevens over het voertuig.

| Gegeven | Bron | Doel | Gevoeligheid | Plek | Bewaartermijn |
|---|---|---|---|---|---|
| Kenteken | RDW-opzoeking of handmatig | Voertuig herkennen, RDW-gegevens ophalen | middel (indirect herleidbaar tot de eigenaar) | db, bak, pdf | tot het voertuig wordt verwijderd |
| Voertuiggegevens (soort, merk, model, kleur, cilinderinhoud, datum eerste toelating) | RDW of handmatig | Overzicht, planning, export | laag | db, bak, pdf | idem |
| Bijnaam van het voertuig | handmatig | Herkenbaarheid in de app en meldingen | laag (vrije tekst) | db, bak, win | idem |
| Extra RDW-gegevens (APK-datum, terugroepactie, tellerstandoordeel, catalogusprijs, datum tenaamstelling NL, massa, brandstof, vermogen) | RDW Open Data | Planning (APK), informatie voor de koper | laag | db (`rdw_json`), bak, pdf | idem; wordt overschreven bij vernieuwen |
| Specificaties (olie, banden, accu, ...) | handmatig | Naslag bij onderhoud, export | laag | db, bak, pdf | idem |
| Onderhoudsbeurt: datum, km-stand, onderhoudstypes | handmatig of uit een factuur gelezen | Historie en planning | laag | db, bak, pdf | tot de beurt of het voertuig wordt verwijderd |
| Beurt: uitgevoerd door | handmatig of uit een factuur gelezen | Historie, vertrouwen koper | middel (kan een persoonsnaam zijn, bijv. een zelfstandige monteur) | db, bak, pdf | idem |
| Beurt: kosten | handmatig of uit een factuur gelezen | Kostenoverzicht, export (optioneel) | laag | db, bak, pdf (tenzij "kosten niet tonen") | idem |
| Beurt: opmerking | handmatig | Toelichting | middel (vrije tekst, staat in de verkoop-PDF, zie P6) | db, bak, pdf | idem |
| Losse km-standen en stallingsperiodes (met opmerking) | handmatig | Km-verloop, planning | laag; opmerking middel | db, bak, pdf (km-verloop, zonder opmerking) | tot verwijderd |
| Factuur, origineel | upload | Bewijs van onderhoud; bron voor de gelakte versie | **hoog** (naam, adres, telefoon, klantnummer van de eigenaar; naam van de monteur) | fac, bak | tot de factuur, beurt of het voertuig wordt verwijderd |
| Factuur, gelakte versie | gemaakt door Garage | Delen met een koper | laag tot middel (zo goed als het lakken) | fac, bak, pdf (alleen als gecontroleerd) | idem |
| Factuur: oorspronkelijke bestandsnaam | upload | Herkenning in de app | middel (kan een naam bevatten, bijv. `factuur Jansen.pdf`) | db, bak | idem |
| Lakbalken (posities) | gebruiker | Opnieuw lakken bouwt hierop voort | laag | db, bak | idem |
| Documenten (kentekenbewijs, polis, aankoopfactuur, ...) en hun vervaldatum | upload | Naslag, herinnering bij verlopen | **hoog** | doc, db, bak | tot het document of voertuig wordt verwijderd |
| Voertuigbeeld: eigen foto | upload | Herkenning in de app | middel (kan een huis, persoon of omgeving tonen; EXIF/GPS wordt verwijderd) | bld, bak | tot vervangen of verwijderd |
| Voertuigbeeld: Sketchfab of Wikimedia, met titel en bronlink | gekozen zoekresultaat | Herkenning, naamsvermelding | laag | bld, db, bak | idem |
| Eigen lak-termen (naam, adres, ...) | gebruiker | Persoonsgegevens op facturen vinden | **hoog** | db (`setting`), bak | tot de gebruiker ze aanpast |
| Instellingen (thema, herinneringen, Sketchfab, back-upmap en -status) | gebruiker en app | Werking van de app | laag (de back-upmap bevat de Windows-gebruikersnaam) | db, bak | doorlopend |
| Administratie van meldingen (`last_notification`) | app | Hooguit één melding per dag | laag | db, bak | wordt overschreven |
| Tekst van herinneringen (voertuignaam + wat er moet gebeuren) | app | Herinnering | laag | win | tot de gebruiker de melding wist |
| Foutmeldingen | app | Problemen oplossen | laag tot middel (paden met gebruikersnaam, foutteksten) | log | ca. 3 MB, daarna overschreven |
| OCR-resultaat van een factuur | Windows-OCR | Uitlezen en lak-suggesties | **hoog** | alleen in het geheugen (cache van de laatste afbeeldingen) | tot de app sluit |

## Wat naar buiten gaat
Alleen bij gebruik van de functie, zonder account of telemetrie (zie ook PRIVACY.md):

| Functie | Gegevens | Ontvanger |
|---|---|---|
| Voertuig opzoeken, RDW vernieuwen | kenteken, IP-adres | RDW Open Data |
| Beeld zoeken en kiezen | merk en model of zoekterm, IP-adres | Sketchfab, Wikimedia Commons |
| 3D-model bekijken | IP-adres | Sketchfab |
| Back-up naar OneDrive of een andere cloudmap | alles, ook originelen en lak-termen | de gekozen map (via de sync-client) |
| Gegevensmap in OneDrive (Documenten verplaatst) | alles | OneDrive |
| Verkoop-PDF | zie register, kolom pdf | wie de gebruiker die geeft |

## Tijdelijke kopieën
| Wat | Plek | Opgeruimd |
|---|---|---|
| Afbeelding van een factuur voor OCR | `%TEMP%\tmp*.png` | direct na de OCR; bij een crash blijft hij staan (P8) |
| Databasekopie voor een back-up | tijdelijke map in `%TEMP%` | direct na het inpakken |
| Back-up die via "bestand kiezen" wordt teruggezet | tijdelijke map in `%TEMP%` | direct na het terugzetten |
| Uitgepakte back-up, kopie van de huidige database | `.herstel-tmp\` in de gegevensmap | direct na het terugzetten; na een crash bij de volgende start (eerst wordt de oude stand teruggezet als het terugzetten niet af was) |
| Huidige mappen tijdens het terugzetten | `.oud-facturen\` enz. in de gegevensmap | na het terugzetten; na een crash bij de volgende start |
| Halve back-up | `*.zip.deel` in de back-upmap | bij een fout direct; na een crash bij de volgende start |

Het programmavenster (WebView2) draait volgens de standaard van pywebview in privémodus, zonder blijvende cache of cookies. Dat is een inschatting op basis van de pywebview-standaard, nog niet gecontroleerd op schijf.

## Bevindingen bij het opstellen
Punten die nog niet in de roadmap stonden of die een bestaand punt aanscherpen:

1. ~~**Verwijderd is niet direct weg uit de database.**~~ Opgelost met P9: `PRAGMA secure_delete = ON` bij elke verbinding, plus eenmalig `VACUUM` voor wat daarvoor al was verwijderd. Kanttekening: oude back-ups bevatten de verwijderde gegevens nog (P3/P7).
2. ~~**Oorspronkelijke bestandsnaam in de verkoop-PDF.**~~ Opgelost met P10: foutpagina's noemen alleen het bijlagenummer, de datum en het onderhoud.
3. ~~**Restanten na een crash.**~~ Opgelost met G9: `backup.clean_up()` bij het starten; `.deel`-bestanden bij de automatische back-upcontrole.
4. **Meldingencentrum van Windows** bewaart de tekst van herinneringen. Laag risico, maar hoort in de privacytekst.
5. **Lak-termen staan leesbaar in elke back-up.** Bevestigt de noodzaak van P2.
