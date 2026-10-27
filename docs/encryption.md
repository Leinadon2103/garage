# Design: encryption (P1) and password-protected backup (P2)

*Nederlands: [encryption.nl.md](encryption.nl.md)*

Status: proposal (K6), not built yet. Daniel's decision (October 2026): DPAPI for the data on this pc, a password on the backup. This document records how, and why.

## Why
- Invoices and documents are stored readable in the data folder, which is often in OneDrive (Documents). Backups also go to OneDrive readable by default, including the redaction terms ([data register](data-register.md), finding 5).
- Deleting is not really deleting: OneDrive keeps old versions and a recycle bin. With a key per file, a file is unreadable once that key is thrown away, wherever its copies are (crypto-shredding).
- A backup must be restorable on another pc. DPAPI cannot do that, a password can.

## Threats
| Threat | After P1+P2 |
|---|---|
| Someone with access to your OneDrive (account taken over, shared folder, Microsoft itself) | Sees only encrypted files and backups |
| Another Windows user on the same pc, a loose disk, a stolen laptop without BitLocker | Cannot read the files: the key belongs to your Windows account |
| Old versions and recycle bin in OneDrive after deleting | Unreadable once the file key is erased from the database (P9 makes sure it is really gone) |
| Malware or someone logged in as you | **Not covered.** They can ask DPAPI for the key, just like Garage. Only a password at every start helps there, and that was deliberately not chosen. |
| Old backups after a sale or wipe | **Not covered by P1/P2:** a backup contains the keys. P3/P7 solve that (cleaning up old backups). |

## What gets encrypted
| Data | How |
|---|---|
| All files in `facturen\`, `documenten\` and `beelden\` (originals, redacted versions, photos) | AES-256-GCM per file with its own key |
| Redaction terms (`setting.private_terms`) and original file names (`invoice.original_filename`, `document.original_filename`) | Field encryption with the master key |
| The database inside a backup | The whole file, with the master key |
| The database on this pc | **No.** License plate, services, amounts and notes stay readable. See "Rejected". |

Redacted versions and images are encrypted too. That is simpler (one rule for all files), and an own photo can show a house or a person.

## Keys
```
Windows account --DPAPI--> master key (32 bytes, random)
backup password --scrypt--> password key --> master key (second copy)
master key --> file key (per file, in the database) --> file
master key --> redaction terms, file names, backup database
```
- **Master key:** one per data folder, random. On disk only in two wrapped forms:
  1. `garage.key` in the data folder, protected with DPAPI (`CryptProtectData`, current user, via `ctypes`, no extra package). Not included in the backup: it is worthless on another pc.
  2. In the database (`setting.master_key_backup`), encrypted with the password key. Included in the backup.
- **Password key:** `hashlib.scrypt` (standard library) with n=2^17, r=8, p=1 and a random salt. The password itself is never stored. At least 12 characters, no other rules.
- **File keys:** new table `file_key (path TEXT PRIMARY KEY, key BLOB NOT NULL)`, a random key per file, encrypted with the master key. Deleting a file deletes the row too.
- **Package:** AES-GCM comes from `cryptography` (new; ships abi3 wheels, so it should work on 3.15, an estimate to be checked when building).

Because the master key is also in the database under the password, the password only has to be typed when setting it and when recovering. Automatic backups keep working.

## File format
`GRGE` (4 bytes) + version (1 byte) + nonce (12 bytes) + ciphertext with tag. The relative path is used as *associated data*, so a file cannot be moved to another place unnoticed. Files are encrypted and decrypted in memory (an upload is at most 200 MB) and written atomically (temporary file, then `os.replace`).

## What changes in the code
- New module `app/crypto.py`: DPAPI, AES-GCM, the file format, key management.
- Reading and writing only through `db.read_file(rel) -> bytes` and `db.write_file(rel, data)`. Every place that opens a path now changes: `FileResponse` becomes `Response`, and `pymupdf.open(path)`, `Image.open(path)` and the sale PDF get bytes.
- OCR now writes a readable copy to `%TEMP%`. With encryption that becomes the biggest leak, so **P8 comes along** in this step.
- The exe's `--self-test` gets an encryption round trip, so a missing `cryptography` in the exe is noticed.

## Migrating existing data
Migration 3 creates the `file_key` table. After that a Python step at startup encrypts:
1. No `garage.key`? Create a master key and store it with DPAPI.
2. For each file without a `GRGE` header: create and store a key (commit), then encrypt the file and replace it. A crash halfway is fine: the next start picks up the rest, with the key already stored.
3. Encrypt the redaction terms and file names.

The same step runs after restoring an old, unencrypted backup. Files without a database row (orphans) are encrypted as well.

Points of attention:
- Before migrating, Garage makes a normal backup, as now. It is still readable, like all existing backups. Cleaning up can come later with P7.
- OneDrive keeps the old, readable versions for a while (about 30 days for a personal account, an estimate). Garage cannot erase those; the app says so after the migration.
- After the migration version 1.3 can no longer read the files. That was already true after migration 2.

## Backup (P2)
- Contents: `onderhoud.db` encrypted with the master key, the files as they are (already encrypted, so fast), and `garage-backup.json` with `"format": 2` and the password-wrapped master key (plus salt and scrypt settings).
- The K3 check decrypts the database in memory for `PRAGMA integrity_check`.
- **Setting the password:** at the first start after the update, and changeable later under Backup. Changing it only re-wraps the second copy of the master key. Old backups keep their old password.
- **No password set yet:** backups are still made, but can then only be restored on this pc and account. The app shows a warning until the password is set. A limited backup is better than none.

## Restoring
1. Backup with `format` 2 and the same master key as here (compared through a key fingerprint): no password needed.
2. Different master key (other pc, or after a reinstall): ask for the password, decrypt the master key and store it here with DPAPI. The safety copy of the current state is still made with the current key first.
3. Old backup without `format`: restore as now, then the migration runs.
4. Wrong password: nothing changed, try again.

## Lost key
- `garage.key` cannot be opened (other pc, Windows reinstalled, password reset by an administrator): Garage starts with a screen "Enter your backup password". With the password the app takes the master key from its own database and stores it again with DPAPI. No backup is even needed.
- Whether resetting a Microsoft account keeps the DPAPI keys, I do not know for sure; that is why this route exists.
- **Windows account and password both lost: the data is gone.** That is shown clearly when setting the password. There will be no back door.

## Rejected
- **SQLCipher (encrypting the whole database):** an extra compiled package (`sqlcipher3`) instead of the standard `sqlite3` module, with no certainty about wheels for 3.15, and all database code has to move to it. The sensitive fields in the database are covered by field encryption; the rest (license plate, services) is low to medium and is in the sale PDF anyway.
- **Only a password, no DPAPI:** safer against someone on your account, but a password at every start, and everything lost when it is forgotten. Not chosen.
- **One key for all files:** simpler, but then crypto-shredding per file does not work.
- **Windows CNG via `ctypes` instead of `cryptography`:** no new package, but calling AES-GCM yourself is error-prone. You do not build encryption yourself.

## Build order
Each step its own PR with tests:
1. `app/crypto.py` with tests (DPAPI, format, keys), `cryptography` in the lock files, self-test.
2. `read_file`/`write_file` and every caller moved to it; P8.
3. Migration 3 and the encryption step; redaction terms and file names.
4. Backup format 2, password screen, restoring with a password, the screen for a missing key.
5. Docs: data register, data dictionary, `PRIVACY`, glossary.
