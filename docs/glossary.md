# Glossary / Woordenlijst

The code is English; the app texts are Dutch. This list links the Dutch terms (what the user sees) to the English names (what the code and the database use).

De code is Engels; de teksten in de app zijn Nederlands. Deze lijst koppelt de Nederlandse termen (wat de gebruiker ziet) aan de Engelse namen (wat de code en de database gebruiken).

## Domain / Domein

| Nederlands | English | In code |
|---|---|---|
| voertuig | vehicle | table `vehicle` |
| kenteken | license plate | `license_plate` |
| voertuigsoort | vehicle type | `vehicle_type` |
| merk | make | `make` |
| datum eerste toelating | first registration date | `first_registration_date` |
| cilinderinhoud | engine displacement | `displacement_cc` |
| bijnaam | nickname | `nickname` |
| onderhoudsbeurt, beurt | service | table `service` |
| onderhoudstype | maintenance type | table `maintenance_type` |
| vuistregel | rule of thumb (default interval) | `"rule_of_thumb"` |
| onderhoudsschema, eigen interval | custom schedule, custom interval | table `vehicle_interval`, `"custom"` |
| kilometerstand, km-stand | odometer reading | `odometer_km`, table `odometer_reading` |
| km-verloop | odometer history | `db.odometer_history` |
| uitgevoerd door | performed by | `performed_by` |
| kosten | cost | `cost` |
| opmerking | note | `note` |
| factuur | invoice | table `invoice` |
| (persoonsgegevens) weglakken | redact | `redaction.py`, `redactions` |
| gecontroleerd | reviewed | `reviewed` |
| lakbalk | redaction box | `redactions` (JSON) |
| eigen gegevens om weg te lakken | private terms | `private_terms` |
| document | document | table `document` |
| vervaldatum | expiry date | `expiry_date` |
| specificatie | spec | table `spec` |
| stalling | storage (period) | table `storage_period` |
| wegzetten / ophalen | start / end storage | `/storage/start`, `/storage/end` |
| instelling | setting | table `setting` |
| weergave (licht, donker, zoals Windows) | theme (light, dark, system) | `theme` |
| herinnering, melding | reminder, notification | `reminders`, `show_notification` |
| back-up, terugzetten | backup, restore | `backup.create`, `backup.restore` |
| veiligheidskopie | safety copy | `SAFETY_PREFIX` |
| verkoop-PDF, uitdraai | sale PDF, export | `export_pdf.create` |
| bijlage | attachment | `attachment_nrs` |
| beeld (3D-model, foto) | image (3D model, photo) | `image.py`, `image_type` |
| zelftest | self-test | `Garage.exe --self-test` |

## Kept in Dutch / Nederlands gebleven
Names the user sees in Windows stay Dutch / Namen die de gebruiker in Windows ziet, blijven Nederlands:

- data folder `Documenten\Garage` with `onderhoud.db`, `facturen\`, `beelden\`, `documenten\`;
- backup files `Garage-backup-*.zip` and `Garage-veiligheidskopie-*.zip`, folder `Garage back-ups`;
- installer `Garage-installatie-<version>.exe`;
- values from the RDW and labels in the app: vehicle types (`Motorfiets`, `Personenauto`, ...), document kinds, names of maintenance types and specs.

Proper names / Eigennamen: **RDW** (Dutch vehicle authority / Rijksdienst voor het Wegverkeer), **APK** (periodic roadworthiness test), **NAP** (odometer check).
