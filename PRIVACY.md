# Privacy

*Nederlands: [PRIVACY.nl.md](PRIVACY.nl.md)*

Garage is a program for your own computer. There is no account and no server of the maker: **the maker of Garage receives none of your data**. You manage your data yourself.

## What data Garage stores
| Data | Where | Sensitivity |
|---|---|---|
| Vehicle data (license plate, make, model, RDW data, specs) | database `onderhoud.db` | low |
| Maintenance, odometer readings, costs, storage | database | low |
| Original invoices (with your name, address, phone etc.) | folder `facturen` | **high** |
| Redacted invoices | folder `facturen` | low |
| Documents (registration certificate, insurance policy, purchase invoice) | folder `documenten` | **high** |
| Your own redaction terms (name, address) | database | **high** |
| Vehicle photo (without EXIF/GPS) | folder `beelden` | low |
| Error messages | `app.log` (max. about 3 MB) | low |
| Text of reminders | Windows notification centre | low |

The installed program stores this in **Documents\Garage**. If your Documents folder is in OneDrive, your data is there as well.

The full overview, with purpose and retention per piece of data, is in the [data register](docs/data-register.md).

## What goes outside
Only when you use the feature:

| Feature | What | To |
|---|---|---|
| Look up a vehicle, refresh RDW data | license plate, IP address | RDW Open Data (Netherlands) |
| Search and choose an image | make and model or your search term, IP address | Sketchfab and Wikimedia Commons (US) |
| View a 3D model | IP address | Sketchfab (US). By default only after a click, adjustable under *Settings > Privacy*. |
| Backup to a cloud folder | everything, including the unredacted originals | the folder you choose (e.g. OneDrive) |
| Reminders | nothing, the notification is local | - |

Text recognition (OCR) happens offline with the built-in Windows OCR.

## Sharing with a buyer
The sale PDF only contains invoices you have reviewed and redacted. An invoice that has not been reviewed never goes along, and neither does its file name. Documents (registration, policy) never go along. If you choose "hide costs", the amounts are redacted on the invoices too.

## Keeping and deleting
- **Deleting a vehicle** erases the vehicle with all maintenance, invoices and documents from the app. Deleted data is really overwritten in the database, not just marked as free.
- **Backups** contain a copy: the last 10 automatic backups and 3 safety copies are kept. What you delete is therefore still in older backups until they are cleaned up. Delete those yourself if it must be gone straight away.
- **Reminders** stay in the Windows notification centre until you clear them there.
- **Uninstalling Garage** (Settings > Apps) asks whether you also want to erase your data.
- **Taking all your data with you** is possible with a backup: one zip with everything.
