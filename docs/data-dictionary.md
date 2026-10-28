# Data dictionary

*Nederlands: [data-dictionary.nl.md](data-dictionary.nl.md)*

Meaning of every table and column in `onderhoud.db`, with allowed values and origin. Where data is stored and how sensitive it is, is in the [data register](data-register.md). Dutch terms and their English names: [glossary](glossary.md).

Status: schema version 4 (`PRAGMA user_version`). The test `tests/test_data_dictionary.py` fails if a column in the database is missing here (in both languages); so with a new migration, add the columns here too.

## Conventions
- **Names** in the database are English (since migration 2). **Values** the user sees stay Dutch: vehicle types (from the RDW), document kinds, names of maintenance types and specs.
- **Dates** are text in ISO format `YYYY-MM-DD`. For odometer readings and storage, dates in the future are refused.
- **Amounts** are euros as REAL, rounded to 2 decimals. Input may be Dutch (`1.250,50`) or with a dot.
- **Odometer readings** are whole kilometres. Input may contain dots (`12.000`).
- **Empty**: most free text fields become `NULL` when empty. Exception: `make`, `model`, `color` and `first_registration_date` of `vehicle` become an empty string.
- **File paths** are relative to the data folder, with `/`, and must match `db.VALID_PATH` (`facturen/`, `beelden/` or `documenten/` + name; folder names stay Dutch because users see them). Paths outside it are refused, also from a backup.
- **Mime types**: only `application/pdf`, `image/jpeg` and `image/png`. When shown, the extension counts, never the database value.
- **Deleting**: all child tables have `ON DELETE CASCADE` to their vehicle or service; the app removes the related files itself. `PRAGMA secure_delete` overwrites deleted data.
- **Encrypted** (P1, [design](encryption.md)): all files in the data folder, each with its own key in `file_key`. Encrypted text fields start with `grge:` (`db.encrypt_text`): `invoice.original_filename`, `document.original_filename` and the setting `private_terms`. A field without that prefix is not encrypted yet and gets encrypted at the next start.

## Tables

### vehicle
One row per vehicle.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `license_plate` | TEXT, required | License plate without dashes, upper case (`MD12AB`); the app shows it with dashes | `[A-Z0-9]+` | RDW or manual, normalized by `rdw.normalize` |
| `vehicle_type` | TEXT, required | Kind of vehicle; decides which maintenance types and specs apply | `Motorfiets`, `Bromfiets`, `Personenauto`, `Bedrijfsauto`, `Aanhangwagen`, `Overig`, or another RDW type | RDW or list |
| `make` | TEXT | Make | free | RDW (Title case) or manual |
| `model` | TEXT | Trade name | free | RDW or manual |
| `first_registration_date` | TEXT | First admission to the road | ISO date or empty | RDW or manual |
| `color` | TEXT | First color | free | RDW or manual |
| `displacement_cc` | INTEGER | Engine displacement in cc | ≥ 0 or NULL | RDW or manual |
| `nickname` | TEXT | Own name for the vehicle | free or NULL | manual |
| `image_type` | TEXT | Kind of vehicle image | `3d`, `photo`, `own` or NULL | image screen |
| `image_ref` | TEXT | Sketchfab uid for a 3D model | 32 hex characters or NULL | Sketchfab |
| `image_title` | TEXT | Title of the image (attribution) | free or NULL | Sketchfab/Wikimedia, or `Eigen foto` |
| `image_source` | TEXT | Link to the source (attribution) | `https://sketchfab.com/...`, `https://commons.wikimedia.org/...` or NULL | app |
| `image_path` | TEXT | Local image; for 3D the offline fallback | `beelden/<uuid>.jpg` or NULL | app |
| `schedule_source` | TEXT | Where the custom maintenance intervals come from | free or NULL (e.g. "instructieboekje") | manual |
| `rdw_json` | TEXT | Extra RDW data as JSON, see [rdw_json](#rdw_json) | JSON object or NULL | RDW, filtered by `main._rdw_extra` |
| `rdw_updated_on` | TEXT | Date of the last RDW lookup | ISO date or NULL | app |

### maintenance_type
Kinds of maintenance, with a default interval (the "rule of thumb"). With an empty table, `db.init()` fills in `DEFAULT_TYPES`.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `name` | TEXT, required | Name, e.g. "Olie vervangen". `Overig` (other) is always listed last. | free | default list or settings |
| `vehicle_types` | TEXT, required | Vehicle types this type applies to | `*` (all) or types separated by commas, e.g. `Motorfiets,Bromfiets` | default list or settings |
| `interval_km` | INTEGER | Every so many km | ≥ 0 or NULL (no km interval) | idem |
| `interval_months` | INTEGER | Every so many months | ≥ 0 or NULL (no time interval) | idem |

Whichever interval runs out first (km or time) counts.

### vehicle_interval
Custom interval per vehicle that differs from the rule of thumb, e.g. from the owner's manual. No row = rule of thumb of `maintenance_type`.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `type_id` | INTEGER, required | Maintenance type | existing `maintenance_type.id` | app |
| `interval_km` | INTEGER | Custom km interval | ≥ 0 or NULL | manual |
| `interval_months` | INTEGER | Custom time interval | ≥ 0 or NULL | manual |

Key: (`vehicle_id`, `type_id`).

### service
One maintenance moment ("beurt"), with one or more maintenance types and zero or more invoices.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `date` | TEXT, required | Date on which the maintenance was done | ISO date | manual or read from the invoice |
| `odometer_km` | INTEGER | Odometer reading at the service | ≥ 0 or NULL; must fit the odometer history (`extra.km_check`) | idem |
| `performed_by` | TEXT | Garage or person | free or NULL | idem |
| `cost` | REAL | Total amount in euros | number or NULL (negative is not refused) | idem |
| `note` | TEXT | Explanation; appears in the sale PDF | free or NULL | manual |

### service_maintenance_type
Link between service and maintenance type (at least one per service).

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `service_id` | INTEGER, required | Service | existing `service.id` | app |
| `type_id` | INTEGER, required | Maintenance type | existing `maintenance_type.id` | choice in the form |

Key: (`service_id`, `type_id`).

### invoice
Attachment to a service. After redaction there are two files: the original and the redacted version.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `service_id` | INTEGER, required | Service | existing `service.id` | app |
| `path` | TEXT, required | The file the app shows and exports: the redacted version if there is one, otherwise the original | `facturen/<vehicle-id>/<uuid>.<ext>` or `.../<uuid>-gelakt.<ext>` | app |
| `original_filename` | TEXT, required | File name at upload | free, encrypted (`grge:`) | upload |
| `mime_type` | TEXT, required | File type | see Conventions | from the extension, checked on the first bytes |
| `original_path` | TEXT | The unredacted original | path as above, or NULL = not redacted yet (then `path` is the original) | app |
| `reviewed` | INTEGER, required | Reviewed for personal data; only then does the invoice go into the sale PDF | `0` or `1` (default `0`) | redaction screen |
| `redactions` | TEXT | Applied boxes as a JSON list `[{"page", "x0", "y0", "x1", "y1"}]`, positions as a fraction 0-1 of the page; pages count from 0 | JSON or NULL | redaction screen |

### document
Separate documents of a vehicle. Never included in the sale PDF.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `kind` | TEXT, required | Kind of document | `extra.DOCUMENT_KINDS` (registration, insurance policy, purchase invoice, warranty, owner's manual, inspection report, other; Dutch labels) | list |
| `title` | TEXT | Own description | free or NULL | manual |
| `path` | TEXT, required | File | `documenten/<vehicle-id>/<uuid>.<ext>` | app |
| `original_filename` | TEXT, required | File name at upload | free, encrypted (`grge:`) | upload |
| `mime_type` | TEXT, required | File type | see Conventions | from the extension |
| `expiry_date` | TEXT | Expires on (e.g. a policy); gives a reminder | ISO date or NULL | manual |
| `added_on` | TEXT, required | Date added | ISO date | app |

### odometer_reading
Separate odometer reading, outside a service.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `date` | TEXT, required | Date of the reading | ISO date, not in the future | manual (default today) |
| `odometer_km` | INTEGER, required | Reading | ≥ 0; must fit the odometer history | manual |
| `note` | TEXT | Explanation | free or NULL | manual |

### fuel_entry
Fill-up (F2). Counts in the odometer history; `fuel.consumption` works out the consumption from full tank to full tank.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `date` | TEXT, required | Date of the fill-up | ISO date, not in the future | manual (default today) |
| `odometer_km` | INTEGER, required | Reading at the fill-up | ≥ 0; must fit the odometer history | manual |
| `liters` | REAL, required | Litres put in | > 0 and ≤ 1000 | manual |
| `cost` | REAL | Amount in euros | ≥ 0 or NULL | manual |
| `full_tank` | INTEGER, required | Filled up completely | 1 (default) or 0; with 0 the litres count at the next full tank | manual |
| `note` | TEXT | Note, e.g. the petrol station | free or NULL | manual |

### storage_period
Period in which the vehicle is stored (e.g. winter storage). No reminders are sent during storage.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `id` | INTEGER | Key | | automatic |
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `start_date` | TEXT, required | Start of the storage | ISO date, not in the future, not in an earlier period | manual |
| `end_date` | TEXT | End | ISO date ≥ `start_date`, or NULL = still in storage (at most one per vehicle) | manual |
| `start_km` | INTEGER | Reading when stored | ≥ 0 or NULL; must fit the odometer history | manual |
| `end_km` | INTEGER | Reading when picked up | ≥ 0 or NULL; must fit the odometer history | manual |

### spec
Specs per vehicle (oil, tyres, battery, ...). The default list per type is in `extra.SPECS_MOTORCYCLE` and `extra.SPECS_CAR`; own keys are allowed too.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `vehicle_id` | INTEGER, required | Vehicle | existing `vehicle.id` | app |
| `key` | TEXT, required | Part, e.g. "Motorolie" | free, max. 60 characters | default list or manual |
| `value` | TEXT, required | Value, e.g. "10W-40" | free, not empty, max. 200 characters | manual |
| `position` | INTEGER, required | Order on screen and in the export | ≥ 0 (default 0) | app |

Key: (`vehicle_id`, `key`).

### file_key
The key of every encrypted file (P1). Once the row is erased, every copy of the file is unreadable, also an old version in OneDrive.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `path` | TEXT, key | File path, see Conventions | valid file path | app |
| `key` | BLOB, required | Key of the file (AES-256), encrypted with the master key | `crypto` format (`GRGE` + version + nonce + content) | app |

No foreign key: a file without a database row (orphan) has a key as well.

### setting
Key-value pairs for the whole app.

| Column | Type | Meaning | Allowed values | Origin |
|---|---|---|---|---|
| `key` | TEXT | Name of the setting, see below | | app |
| `value` | TEXT | Value as text | | app or user |

| Key | Meaning | Values | Default |
|---|---|---|---|
| `theme` | Theme | `light`, `dark`, `system` | `system` |
| `private_terms` | Own redaction terms (name, address), one per line; terms shorter than 3 characters do not count | free text, encrypted (`grge:`) | empty |
| `master_key_backup` | The master key, encrypted with the backup password (P2); goes into every backup and brings back the key when `garage.key` can no longer be opened | JSON from `crypto.wrap_with_password` (salt, scrypt settings, key, fingerprint) | empty until a password is set |
| `encrypted_notice` | One-off notice after encrypting existing files (old versions in OneDrive) | `on` or empty | empty |
| `master_key_fingerprint` | Fingerprint of the master key (`crypto.fingerprint`), not the key itself. If `garage.key` is missing while this is set, Garage does not make a new key | 16 hex characters | empty until the first start with encryption |
| `reminders` | Windows notifications at sign-in | `on`, `off` | `on` |
| `last_notification` | Last notification shown, to limit repeats | JSON `{"date", "time", "key"}` | empty |
| `sketchfab_autoload` | Load the 3D model straight away instead of after a click | `on`, `off` | `off` |
| `backup_auto` | Automatic backup (every 7 days) | `on`, `off` | `on` |
| `backup_folder` | Chosen backup folder | full path or empty (= default folder) | empty |
| `backup_last` | Time of the last backup | ISO date and time | empty |
| `backup_error` | Last error of the automatic backup | text (max. 300 characters) or empty | empty |
| `secure_delete_vacuumed` | The one-off `VACUUM` after switching on `secure_delete` is done | `yes` or empty | empty |

`backup_folder`, `backup_auto` and `backup_last` belong to this pc: when a backup is restored, the current values stay.

## rdw_json
Fields in `vehicle.rdw_json`. Only these keys are stored, with simple values (`main.RDW_FIELDS`).

| Key | Meaning | Value | RDW source |
|---|---|---|---|
| `apk_expiry_date` | Expiry date of the APK (periodic roadworthiness test) | ISO date or null | `vervaldatum_apk` |
| `open_recall` | Open recall | true/false | `openstaande_terugroepactie_indicator` |
| `odometer_judgement` | RDW judgement of the odometer reading | text, e.g. "Logisch" | `tellerstandoordeel` |
| `odometer_year` | Year of the last registered odometer reading | year or null | `jaar_laatste_registratie_tellerstand` |
| `list_price` | List price in euros | number or null | `catalogusprijs` |
| `first_registration_nl_date` | First registration in the Netherlands (import) | ISO date or null | `datum_eerste_tenaamstelling_in_nederland` |
| `curb_weight_kg` | Curb weight in kg | number or null | `massa_rijklaar` |
| `fuel` | Fuel(s), separated by " + " | text or null | fuel dataset, `brandstof_omschrijving` |
| `power_kw` | Highest net power in kW | number or null | fuel dataset |

Sources: RDW Open Data, datasets *Gekentekende voertuigen* (`m9d7-ebf2`) and *Brandstof* (`8ys7-d773`).

## Schema versions
| Version | Migration | What |
|---|---|---|
| 1 | `db._migration_1` | Baseline: the schema of Garage 1.3.0 (Dutch names). Also works on older databases without a version number (adds missing tables and columns). |
| 2 | `db._migration_2` | English names for tables, columns, setting keys and internal values (`RENAMES_2` and related). Values the user sees stay Dutch. |
| 3 | `db._migration_3` | Table `file_key` for encryption (P1). The existing files and fields are encrypted by `db.encrypt_existing()` at startup, not by the migration. |
| 4 | `db._migration_4` | Table `fuel_entry` for fill-ups and fuel consumption (F2). |

A new migration goes at the end of `db.MIGRATIONS` and gets a row here. Data of a newer version than the app knows is not opened and not restored.
