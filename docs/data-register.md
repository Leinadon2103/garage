# Data register

*Nederlands: [data-register.nl.md](data-register.nl.md)*

Which data Garage processes, where it is stored and for how long. This is the record of processing behind [PRIVACY.md](../PRIVACY.md) and the basis for encryption (P1), retention periods (P7) and erasure (P3, P4). The meaning per table and column is in the [data dictionary](data-dictionary.md).

Status: after version 1.3.0, schema version 3 (encryption P1, build step 3). Update this when a piece of data, a storage location or a retention period is added.

## Storage locations
| Code | Location | Explanation |
|---|---|---|
| **db** | `onderhoud.db` in the data folder | SQLite. Installed: `Documenten\Garage` (Documents), so in OneDrive if Documents has been moved there. Readable, except the encrypted fields (original file names, redaction terms) and the file keys. |
| **key** | `garage.key` in the data folder | Master key, protected with DPAPI: only this Windows account on this pc can open it. Not included in the backup. |
| **inv** | `facturen\<vehicle-id>\` | Original `<uuid>.<ext>`, redacted version `<uuid>-gelakt.<ext>`; encrypted |
| **doc** | `documenten\<vehicle-id>\` | `<uuid>.<ext>`; encrypted |
| **img** | `beelden\` | `<uuid>.jpg`, without EXIF; encrypted |
| **log** | `app.log` (+ `.1`, `.2`) in the data folder | Error messages, max. 1 MB per part |
| **bak** | `Garage back-ups\` | Zip with db + all folders. By default in OneDrive, otherwise in the user folder, or a folder of the user's choice. Encrypted (format 2): the db with the master key, the files as they are; with the master key under the backup password, if one is set. |
| **pdf** | Wherever the user saves the sale PDF | `Downloads` by default; meant to be shared with a buyer |
| **tmp** | `%TEMP%` and temporary folders in the data folder | See [Temporary copies](#temporary-copies) |
| **win** | Windows notification centre | Reminders (toasts) stay there until the user clears them |

Everywhere: what is in **db**, **inv**, **doc** or **img** is also in **bak**.

## Register
Sensitivity: **high** = directly traceable to a person (name, address, contact details, documents); **medium** = indirectly traceable, or free text that may contain personal data; **low** = data about the vehicle.

| Data | Source | Purpose | Sensitivity | Location | Retention |
|---|---|---|---|---|---|
| License plate | RDW lookup or manual | Identify the vehicle, fetch RDW data | medium (indirectly traceable to the owner) | db, bak, pdf | until the vehicle is deleted |
| Vehicle data (type, make, model, color, displacement, first registration date) | RDW or manual | Overview, planning, export | low | db, bak, pdf | idem |
| Nickname of the vehicle | manual | Recognizability in the app and notifications | low (free text) | db, bak, win | idem |
| Extra RDW data (APK date, recall, odometer judgement, list price, NL registration date, weight, fuel, power) | RDW Open Data | Planning (APK), information for the buyer | low | db (`rdw_json`), bak, pdf | idem; overwritten on refresh |
| Specs (oil, tyres, battery, ...) | manual | Reference during maintenance, export | low | db, bak, pdf | idem |
| Service: date, odometer reading, maintenance types | manual or read from an invoice | History and planning | low | db, bak, pdf | until the service or the vehicle is deleted |
| Service: performed by | manual or read from an invoice | History, buyer's trust | medium (can be a person's name, e.g. a self-employed mechanic) | db, bak, pdf | idem |
| Service: cost | manual or read from an invoice | Cost overview, export (optional) | low | db, bak, pdf (unless "hide costs") | idem |
| Service: note | manual | Explanation | medium (free text, appears in the sale PDF, see P6) | db, bak, pdf | idem |
| Separate odometer readings and storage periods (with a note) | manual | Odometer history, planning | low; note medium | db, bak, pdf (odometer history, without the note) | until deleted |
| Invoice, original | upload | Proof of maintenance; source of the redacted version | **high** (name, address, phone, customer number of the owner; name of the mechanic) | inv, bak | until the invoice, service or vehicle is deleted |
| Invoice, redacted version | made by Garage | Sharing with a buyer | low to medium (as good as the redaction) | inv, bak, pdf (only when reviewed) | idem |
| Invoice: original file name | upload | Recognition in the app | medium (can contain a name, e.g. `factuur Jansen.pdf`) | db, bak | idem |
| Redaction boxes (positions) | user | Redacting again builds on them | low | db, bak | idem |
| Documents (registration, policy, purchase invoice, ...) and their expiry date | upload | Reference, reminder on expiry | **high** | doc, db, bak | until the document or vehicle is deleted |
| Vehicle image: own photo | upload | Recognition in the app | medium (can show a house, person or surroundings; EXIF/GPS is removed) | img, bak | until replaced or deleted |
| Vehicle image: Sketchfab or Wikimedia, with title and source link | chosen search result | Recognition, attribution | low | img, db, bak | idem |
| Own redaction terms (name, address, ...) | user | Finding personal data on invoices | **high** | db (`setting`), bak | until the user changes them |
| Settings (theme, reminders, Sketchfab, backup folder and status) | user and app | Operation of the app | low (the backup folder contains the Windows user name) | db, bak | ongoing |
| Notification bookkeeping (`last_notification`) | app | At most one notification per day | low | db, bak | overwritten |
| Text of reminders (vehicle name + what needs doing) | app | Reminder | low | win | until the user clears the notification |
| Error messages | app | Solving problems | low to medium (paths with the user name, error texts) | log | about 3 MB, then overwritten |
| OCR result of an invoice | Windows OCR | Reading and redaction suggestions | **high** | only in memory (cache of the last images) | until the app closes |

## What goes outside
Only when the feature is used, without an account or telemetry (see also PRIVACY.md):

| Feature | Data | Recipient |
|---|---|---|
| Look up a vehicle, refresh RDW data | license plate, IP address | RDW Open Data |
| Search and choose an image | make and model or search term, IP address | Sketchfab, Wikimedia Commons |
| View a 3D model | IP address | Sketchfab |
| Backup to OneDrive or another cloud folder | everything, including originals and redaction terms, encrypted (older backups from before P1/P2 still readable) | the chosen folder (through the sync client) |
| Data folder in OneDrive (Documents moved) | everything; files and sensitive fields encrypted, the rest of the database readable | OneDrive |
| Sale PDF | see register, location pdf | whoever the user gives it to |

## Temporary copies
| What | Location | Cleaned up |
|---|---|---|
| ~~Image of an invoice for OCR~~ | none any more: since P8 it goes to PowerShell through stdin, in memory only | - |
| Half-written data file | `*.deel` next to the file in `facturen\`, `documenten\`, `beelden\` | straight away on an error; after a crash at the next start; never included in a backup |
| Database copy for a backup | temporary folder in `%TEMP%` | straight after zipping |
| Backup restored through "choose file" | temporary folder in `%TEMP%` | straight after restoring |
| Unpacked backup, copy of the current database | `.herstel-tmp\` in the data folder | straight after restoring; after a crash at the next start (first the old state is put back if the restore was not finished) |
| Current folders during a restore | `.oud-facturen\` etc. in the data folder | after restoring; after a crash at the next start |
| Partial backup | `*.zip.deel` in the backup folder | on an error straight away; after a crash at the next start |

The program window (WebView2) runs in private mode by pywebview's default, without a lasting cache or cookies. This is an estimate based on the pywebview default, not yet checked on disk.

## Findings while writing this
Points that were not yet on the roadmap or that sharpen an existing point:

1. ~~**Deleted is not straight away gone from the database.**~~ Fixed by P9: `PRAGMA secure_delete = ON` on every connection, plus a one-off `VACUUM` for what was deleted before. Note: old backups still contain the deleted data (P3/P7).
2. ~~**Original file name in the sale PDF.**~~ Fixed by P10: error pages only name the attachment number, the date and the maintenance.
3. ~~**Leftovers after a crash.**~~ Fixed by G9: `backup.clean_up()` at startup; `.deel` files at the automatic backup check.
4. **The Windows notification centre** keeps the text of reminders. Low risk, but it belongs in the privacy text.
5. ~~**Redaction terms are readable in every backup.**~~ Solved by P1 and P2: encrypted in the database, and the database in a backup is encrypted as a whole. Old backups from before P1 still have them readable (cleaning up: P7).
