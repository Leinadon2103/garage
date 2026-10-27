# Datadictionary

*English: [data-dictionary.md](data-dictionary.md)*

Betekenis van elke tabel en kolom in `onderhoud.db`, met toegestane waarden en herkomst. Waar een gegeven staat en hoe gevoelig het is, staat in het [gegevensregister](data-register.nl.md). Nederlandse termen en hun Engelse namen: [woordenlijst](glossary.md).

Stand: schemaversie 2 (`PRAGMA user_version`). De test `tests/test_data_dictionary.py` faalt als een kolom in de database hier ontbreekt (in beide talen); voeg bij een nieuwe migratie dus ook de kolommen hier toe.

## Afspraken
- **Namen** in de database zijn Engels (sinds migratie 2). **Waarden** die de gebruiker ziet blijven Nederlands: voertuigsoorten (van de RDW), documentsoorten, namen van onderhoudstypes en specificaties.
- **Datums** zijn tekst in ISO-formaat `JJJJ-MM-DD`. Bij km-standen en stalling worden datums in de toekomst geweigerd.
- **Bedragen** zijn euro's als REAL, afgerond op 2 decimalen. Invoer mag Nederlands (`1.250,50`) of met punt.
- **Km-standen** zijn hele kilometers. Invoer mag met punten (`12.000`).
- **Leeg**: de meeste vrije tekstvelden worden `NULL` als ze leeg zijn. Uitzondering: `make`, `model`, `color` en `first_registration_date` van `vehicle` worden een lege tekst.
- **Bestandspaden** zijn relatief aan de gegevensmap, met `/`, en moeten voldoen aan `db.VALID_PATH` (`facturen/`, `beelden/` of `documenten/` + naam; de mapnamen blijven Nederlands omdat de gebruiker ze ziet). Paden daarbuiten worden geweigerd, ook uit een back-up.
- **Mime-types**: alleen `application/pdf`, `image/jpeg` en `image/png`. Bij het tonen telt de extensie, nooit de databasewaarde.
- **Verwijderen**: alle kindtabellen hebben `ON DELETE CASCADE` naar hun voertuig of beurt; de bijbehorende bestanden ruimt de app zelf op. `PRAGMA secure_delete` overschrijft verwijderde gegevens.

## Tabellen

### vehicle
Voertuig: één rij per voertuig.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `license_plate` | TEXT, verplicht | Kenteken zonder streepjes, hoofdletters (`MD12AB`); de app toont het met streepjes | `[A-Z0-9]+` | RDW of handmatig, genormaliseerd door `rdw.normalize` |
| `vehicle_type` | TEXT, verplicht | Voertuigsoort; bepaalt welke onderhoudstypes en specificaties horen | `Motorfiets`, `Bromfiets`, `Personenauto`, `Bedrijfsauto`, `Aanhangwagen`, `Overig`, of een andere RDW-soort | RDW of keuzelijst |
| `make` | TEXT | Merk | vrij | RDW (in Titelcase) of handmatig |
| `model` | TEXT | Handelsbenaming | vrij | RDW of handmatig |
| `first_registration_date` | TEXT | Eerste toelating op de weg | ISO-datum of leeg | RDW of handmatig |
| `color` | TEXT | Eerste kleur | vrij | RDW of handmatig |
| `displacement_cc` | INTEGER | Cilinderinhoud in cc | ≥ 0 of NULL | RDW of handmatig |
| `nickname` | TEXT | Bijnaam voor het voertuig | vrij of NULL | handmatig |
| `image_type` | TEXT | Soort voertuigbeeld | `3d`, `photo`, `own` of NULL | beeldscherm |
| `image_ref` | TEXT | Sketchfab-uid bij een 3D-model | 32 hex-tekens of NULL | Sketchfab |
| `image_title` | TEXT | Titel van het beeld (naamsvermelding) | vrij of NULL | Sketchfab/Wikimedia, of `Eigen foto` |
| `image_source` | TEXT | Link naar de bron (naamsvermelding) | `https://sketchfab.com/...`, `https://commons.wikimedia.org/...` of NULL | app |
| `image_path` | TEXT | Lokale afbeelding; bij 3D de offline-terugval | `beelden/<uuid>.jpg` of NULL | app |
| `schedule_source` | TEXT | Waar de eigen onderhoudsintervallen vandaan komen | vrij of NULL (bijv. "instructieboekje") | handmatig |
| `rdw_json` | TEXT | Extra RDW-gegevens als JSON, zie [rdw_json](#rdw_json) | JSON-object of NULL | RDW, gefilterd door `main._rdw_extra` |
| `rdw_updated_on` | TEXT | Datum van de laatste RDW-opzoeking | ISO-datum of NULL | app |

### maintenance_type
Onderhoudstype, met een standaardinterval (de "vuistregel"). Bij een lege tabel vult `db.init()` de lijst `DEFAULT_TYPES`.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `name` | TEXT, verplicht | Naam, bijv. "Olie vervangen". `Overig` staat altijd onderaan. | vrij | standaardlijst of instellingen |
| `vehicle_types` | TEXT, verplicht | Voor welke voertuigsoorten dit type geldt | `*` (alle) of soorten gescheiden door komma's, bijv. `Motorfiets,Bromfiets` | standaardlijst of instellingen |
| `interval_km` | INTEGER | Elke zoveel km | ≥ 0 of NULL (geen km-interval) | idem |
| `interval_months` | INTEGER | Elke zoveel maanden | ≥ 0 of NULL (geen tijdsinterval) | idem |

Het eerste interval dat verloopt (km of tijd) telt.

### vehicle_interval
Eigen interval per voertuig dat afwijkt van de vuistregel, bijv. uit het instructieboekje. Geen rij = vuistregel van `maintenance_type`.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `vehicle_id` | INTEGER, verplicht | Voertuig | bestaande `vehicle.id` | app |
| `type_id` | INTEGER, verplicht | Onderhoudstype | bestaande `maintenance_type.id` | app |
| `interval_km` | INTEGER | Eigen km-interval | ≥ 0 of NULL | handmatig |
| `interval_months` | INTEGER | Eigen tijdsinterval | ≥ 0 of NULL | handmatig |

Sleutel: (`vehicle_id`, `type_id`).

### service
Onderhoudsbeurt: één onderhoudsmoment, met één of meer onderhoudstypes en nul of meer facturen.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `vehicle_id` | INTEGER, verplicht | Voertuig | bestaande `vehicle.id` | app |
| `date` | TEXT, verplicht | Datum waarop het onderhoud is uitgevoerd | ISO-datum | handmatig of uit de factuur gelezen |
| `odometer_km` | INTEGER | Km-stand bij het onderhoud | ≥ 0 of NULL; moet passen in het km-verloop (`extra.km_check`) | idem |
| `performed_by` | TEXT | Garage of persoon | vrij of NULL | idem |
| `cost` | REAL | Totaalbedrag in euro | getal of NULL (negatief wordt niet geweigerd) | idem |
| `note` | TEXT | Opmerking; staat in de verkoop-PDF | vrij of NULL | handmatig |

### service_maintenance_type
Koppeling tussen beurt en onderhoudstype (minstens één per beurt).

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `service_id` | INTEGER, verplicht | Beurt | bestaande `service.id` | app |
| `type_id` | INTEGER, verplicht | Onderhoudstype | bestaande `maintenance_type.id` | keuze in het formulier |

Sleutel: (`service_id`, `type_id`).

### invoice
Factuur bij een beurt. Na het lakken zijn er twee bestanden: het origineel en de gelakte versie.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `service_id` | INTEGER, verplicht | Beurt | bestaande `service.id` | app |
| `path` | TEXT, verplicht | Het bestand dat de app toont en exporteert: de gelakte versie als die er is, anders het origineel | `facturen/<voertuig-id>/<uuid>.<ext>` of `.../<uuid>-gelakt.<ext>` | app |
| `original_filename` | TEXT, verplicht | Bestandsnaam bij het uploaden | vrij | upload |
| `mime_type` | TEXT, verplicht | Bestandstype | zie Afspraken | uit de extensie, gecontroleerd op de eerste bytes |
| `original_path` | TEXT | Het niet-gelakte origineel | pad zoals hierboven, of NULL = nog niet gelakt (dan is `path` het origineel) | app |
| `reviewed` | INTEGER, verplicht | Gecontroleerd op persoonsgegevens; alleen dan gaat de factuur mee in de verkoop-PDF | `0` of `1` (standaard `0`) | lakscherm |
| `redactions` | TEXT | Toegepaste lakbalken als JSON-lijst `[{"page", "x0", "y0", "x1", "y1"}]`, posities als fractie 0-1 van de pagina; pagina telt vanaf 0 | JSON of NULL | lakscherm |

### document
Losse documenten bij een voertuig. Gaan nooit mee in de verkoop-PDF.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `vehicle_id` | INTEGER, verplicht | Voertuig | bestaande `vehicle.id` | app |
| `kind` | TEXT, verplicht | Soort document | `extra.DOCUMENT_KINDS` (kentekenbewijs, polis, aankoopfactuur, garantiebewijs, instructieboekje, keuringsrapport, overig) | keuzelijst |
| `title` | TEXT | Eigen omschrijving | vrij of NULL | handmatig |
| `path` | TEXT, verplicht | Bestand | `documenten/<voertuig-id>/<uuid>.<ext>` | app |
| `original_filename` | TEXT, verplicht | Bestandsnaam bij het uploaden | vrij | upload |
| `mime_type` | TEXT, verplicht | Bestandstype | zie Afspraken | uit de extensie |
| `expiry_date` | TEXT | Verloopt op (bijv. polis); geeft een herinnering | ISO-datum of NULL | handmatig |
| `added_on` | TEXT, verplicht | Datum van toevoegen | ISO-datum | app |

### odometer_reading
Losse km-registratie, buiten een beurt om.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `vehicle_id` | INTEGER, verplicht | Voertuig | bestaande `vehicle.id` | app |
| `date` | TEXT, verplicht | Datum van de stand | ISO-datum, niet in de toekomst | handmatig (standaard vandaag) |
| `odometer_km` | INTEGER, verplicht | Km-stand | ≥ 0; moet passen in het km-verloop | handmatig |
| `note` | TEXT | Opmerking | vrij of NULL | handmatig |

### storage_period
Stallingsperiode waarin het voertuig stilstaat (bijv. winterstalling). Tijdens een stalling worden geen herinneringen gestuurd.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `vehicle_id` | INTEGER, verplicht | Voertuig | bestaande `vehicle.id` | app |
| `start_date` | TEXT, verplicht | Begin van de stalling | ISO-datum, niet in de toekomst, niet in een eerdere periode | handmatig |
| `end_date` | TEXT | Einde | ISO-datum ≥ `start_date`, of NULL = staat nog in de stalling (hooguit één per voertuig) | handmatig |
| `start_km` | INTEGER | Km-stand bij wegzetten | ≥ 0 of NULL; moet passen in het km-verloop | handmatig |
| `end_km` | INTEGER | Km-stand bij ophalen | ≥ 0 of NULL; moet passen in het km-verloop | handmatig |

### spec
Specificaties per voertuig (olie, banden, accu, ...). De standaardlijst per soort staat in `extra.SPECS_MOTORCYCLE` en `extra.SPECS_CAR`; eigen sleutels mogen ook.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `vehicle_id` | INTEGER, verplicht | Voertuig | bestaande `vehicle.id` | app |
| `key` | TEXT, verplicht | Onderdeel, bijv. "Motorolie" | vrij, max. 60 tekens | standaardlijst of handmatig |
| `value` | TEXT, verplicht | Waarde, bijv. "10W-40" | vrij, niet leeg, max. 200 tekens | handmatig |
| `position` | INTEGER, verplicht | Volgorde in het scherm en de export | ≥ 0 (standaard 0) | app |

Sleutel: (`vehicle_id`, `key`).

### setting
Instellingen: sleutel-waardeparen voor de hele app.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `key` | TEXT | Naam van de instelling, zie hieronder | | app |
| `value` | TEXT | Waarde als tekst | | app of gebruiker |

| Sleutel | Betekenis | Waarden | Standaard |
|---|---|---|---|
| `theme` | Weergave | `light`, `dark`, `system` | `system` |
| `private_terms` | Eigen lak-termen (naam, adres), één per regel; termen korter dan 3 tekens tellen niet | vrije tekst | leeg |
| `reminders` | Windows-meldingen bij aanmelden | `on`, `off` | `on` |
| `last_notification` | Laatst getoonde melding, om herhaling te beperken | JSON `{"date", "time", "key"}` | leeg |
| `sketchfab_autoload` | 3D-model direct laden in plaats van na een klik | `on`, `off` | `off` |
| `backup_auto` | Automatische back-up (elke 7 dagen) | `on`, `off` | `on` |
| `backup_folder` | Gekozen back-upmap | volledig pad of leeg (= standaardmap) | leeg |
| `backup_last` | Tijdstip van de laatste back-up | ISO-datum en -tijd | leeg |
| `backup_error` | Laatste fout van de automatische back-up | tekst (max. 300 tekens) of leeg | leeg |
| `secure_delete_vacuumed` | Eenmalige `VACUUM` na het aanzetten van `secure_delete` is gedaan | `yes` of leeg | leeg |

`backup_folder`, `backup_auto` en `backup_last` horen bij deze pc: bij het terugzetten van een back-up blijven de huidige waarden staan.

## rdw_json
Velden in `vehicle.rdw_json`. Alleen deze sleutels worden opgeslagen, met eenvoudige waarden (`main.RDW_FIELDS`).

| Sleutel | Betekenis | Waarde | RDW-bron |
|---|---|---|---|
| `apk_expiry_date` | Vervaldatum APK | ISO-datum of null | `vervaldatum_apk` |
| `open_recall` | Openstaande terugroepactie | true/false | `openstaande_terugroepactie_indicator` |
| `odometer_judgement` | Oordeel van de RDW over de km-stand | tekst, bijv. "Logisch" | `tellerstandoordeel` |
| `odometer_year` | Jaar van de laatste geregistreerde km-stand | jaartal of null | `jaar_laatste_registratie_tellerstand` |
| `list_price` | Catalogusprijs in euro | getal of null | `catalogusprijs` |
| `first_registration_nl_date` | Eerste tenaamstelling in Nederland (import) | ISO-datum of null | `datum_eerste_tenaamstelling_in_nederland` |
| `curb_weight_kg` | Massa rijklaar in kg | getal of null | `massa_rijklaar` |
| `fuel` | Brandstof(fen), gescheiden door " + " | tekst of null | brandstofdataset, `brandstof_omschrijving` |
| `power_kw` | Hoogste nettovermogen in kW | getal of null | brandstofdataset |

Bronnen: RDW Open Data, datasets *Gekentekende voertuigen* (`m9d7-ebf2`) en *Brandstof* (`8ys7-d773`).

## Schemaversies
| Versie | Migratie | Wat |
|---|---|---|
| 1 | `db._migration_1` | Basis: het schema van Garage 1.3.0 (Nederlandse namen). Werkt ook op oudere databases zonder versienummer (vult ontbrekende tabellen en kolommen aan). |
| 2 | `db._migration_2` | Engelse namen voor tabellen, kolommen, instellingssleutels en interne waarden (`RENAMES_2` en verwant). Waarden die de gebruiker ziet blijven Nederlands. |

Een nieuwe migratie komt achteraan in `db.MIGRATIONS` en krijgt hier een regel. Gegevens van een nieuwere versie dan de app kent, worden niet geopend en niet teruggezet.
