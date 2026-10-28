# Ontwerp: versleuteling (P1) en back-up met wachtwoord (P2)

*English: [encryption.md](encryption.md)*

Status: gebouwd (bouwstap 1 t/m 4, zie "Volgorde van bouwen"). Beslissing van Daniel (oktober 2026): DPAPI voor de gegevens op deze pc, een wachtwoord op de back-up. Dit document legt vast hoe, en waarom zo.

## Waarom
- Facturen en documenten staan nu leesbaar in de gegevensmap, en die staat vaak in OneDrive (Documenten). Back-ups gaan standaard ook leesbaar naar OneDrive, met de lak-termen erbij ([gegevensregister](data-register.nl.md), bevinding 5).
- Wissen is nu niet echt wissen: OneDrive bewaart oude versies en de prullenbak. Met een eigen sleutel per bestand is een bestand na het weggooien van die sleutel onleesbaar, waar de kopieën ook staan (crypto-shredding).
- Een back-up moet ook op een andere pc terug te zetten zijn. DPAPI kan dat niet, een wachtwoord wel.

## Dreigingen
| Dreiging | Na P1+P2 |
|---|---|
| Iemand met toegang tot je OneDrive (account overgenomen, gedeelde map, Microsoft zelf) | Ziet alleen versleutelde bestanden en back-ups |
| Andere Windows-gebruiker op dezelfde pc, losse schijf, gestolen laptop zonder BitLocker | Kan bestanden niet lezen: de sleutel hoort bij jouw Windows-account |
| Andere Windows-gebruiker op dezelfde pc, terwijl Garage bij jou openstaat | Kan de server op `127.0.0.1` wel bereiken, maar krijgt niets: zonder sessiecookie weigert de server alles (S4). Het token is bij elke start nieuw (32 willekeurige bytes) en alleen het venster van Garage krijgt het, via de start-URL; het cookie is `HttpOnly` en `SameSite=Strict` en verdwijnt bij het sluiten. |
| Oude versies en prullenbak in OneDrive na verwijderen | Onleesbaar zodra de bestandssleutel uit de database is gewist (P9 zorgt dat die echt weg is) |
| Malware of iemand die als jou is ingelogd | **Niet afgedekt.** Die kan de sleutel via DPAPI opvragen, net als Garage zelf. Daar helpt alleen een wachtwoord bij elke start, en dat is bewust niet gekozen. |
| Oude back-ups na verkoop of wissen | **Niet afgedekt door P1/P2:** een back-up bevat de sleutels. Dat lost P3/P7 op (oude back-ups opruimen). |

## Wat wordt versleuteld
| Gegeven | Hoe |
|---|---|
| Alle bestanden in `facturen\`, `documenten\` en `beelden\` (originelen, gelakte versies, foto's) | Per bestand AES-256-GCM met een eigen sleutel |
| Lak-termen (`setting.private_terms`) en oorspronkelijke bestandsnamen (`invoice.original_filename`, `document.original_filename`) | Veldversleuteling met de hoofdsleutel |
| De database in een back-up | Het hele bestand, met de hoofdsleutel |
| De database op deze pc | **Niet.** Kenteken, beurten, bedragen en opmerkingen blijven leesbaar. Zie "Afgewezen". |

Ook de gelakte versies en beelden worden versleuteld. Dat is eenvoudiger (één regel voor alle bestanden) en een eigen foto kan een huis of persoon tonen.

## Sleutels
```
Windows-account --DPAPI--> hoofdsleutel (32 bytes, willekeurig)
back-upwachtwoord --scrypt--> wachtwoordsleutel --> hoofdsleutel (tweede kopie)
hoofdsleutel --> bestandssleutel (per bestand, in de database) --> bestand
hoofdsleutel --> lak-termen, bestandsnamen, back-updatabase
```
- **Hoofdsleutel:** één per gegevensmap, willekeurig. Op schijf alleen in twee verpakte vormen:
  1. `garage.key` in de gegevensmap, beschermd met DPAPI (`CryptProtectData`, huidige gebruiker, via `ctypes`, geen extra pakket). Gaat niet mee in de back-up: op een andere pc is hij waardeloos.
  2. In de database (`setting.master_key_backup`), versleuteld met de wachtwoordsleutel. Gaat wel mee in de back-up.
- **Wachtwoordsleutel:** `hashlib.scrypt` (standaardbibliotheek) met n=2^17, r=8, p=1 en een willekeurig zout. Het wachtwoord zelf wordt nergens opgeslagen. Minimaal 12 tekens, verder geen eisen.
- **Bestandssleutels:** nieuwe tabel `file_key (path TEXT PRIMARY KEY, key BLOB NOT NULL)`, met per bestand een willekeurige sleutel, versleuteld met de hoofdsleutel. Verwijder je een bestand, dan gaat ook de rij weg.
- **Pakket:** AES-GCM komt uit `cryptography` (nieuw; heeft abi3-wheels, zou op 3.15 moeten werken, inschatting die bij het bouwen gecontroleerd wordt).

Doordat de hoofdsleutel ook met het wachtwoord in de database staat, hoeft het wachtwoord alleen bij het instellen en bij herstel te worden ingetypt. Automatische back-ups blijven gewoon werken.

## Bestandsformaat
`GRGE` (4 bytes) + versie (1 byte) + nonce (12 bytes) + versleutelde inhoud met tag. Het relatieve pad gaat mee als *associated data*, zodat een bestand niet ongemerkt op een andere plek kan worden gezet. Bestanden worden in het geheugen versleuteld en ontsleuteld (een upload is hooguit 200 MB) en atomair geschreven (tijdelijk bestand, dan `os.replace`).

## Wat er in de code verandert
- Nieuwe module `app/crypto.py`: DPAPI, AES-GCM, het bestandsformaat, sleutelbeheer.
- Lezen en schrijven gaat alleen nog via `db.read_file(rel) -> bytes` en `db.write_file(rel, data)`. Alle plekken die nu een pad openen gaan om: `FileResponse` wordt `Response`, en `pymupdf.open(path)`, `Image.open(path)` en de verkoop-PDF krijgen bytes.
- OCR schrijft nu een leesbare kopie naar `%TEMP%`. Met versleuteling wordt dat het grootste lek, dus **P8 gaat mee** in deze stap.
- `--self-test` van de exe krijgt een versleutelronde, zodat een ontbrekend `cryptography` in de exe opvalt.

## Migratie van bestaande gegevens
Migratie 3 maakt de tabel `file_key`. Daarna versleutelt een stap in Python bij het starten:
1. Geen `garage.key`? Maak een hoofdsleutel en bewaar hem met DPAPI.
2. Per bestand zonder `GRGE`-kop: sleutel maken en opslaan (commit), dan het bestand versleutelen en vervangen. Een crash halverwege is geen probleem: de volgende start pakt de rest op, met de al opgeslagen sleutel.
3. Lak-termen en bestandsnamen versleutelen.

Dezelfde stap draait na het terugzetten van een oude, onversleutelde back-up. Ook bestanden zonder databaserij (wezen) worden versleuteld.

Aandachtspunten:
- Vóór de migratie maakt Garage een gewone back-up, zoals nu. Die is nog leesbaar, net als alle bestaande back-ups. Opruimen kan later met P7.
- OneDrive houdt de oude, leesbare versies nog een tijd vast (bij een persoonlijk account ca. 30 dagen, inschatting). Garage kan dat niet wissen; de app meldt het na de migratie.
- Na de migratie kan versie 1.3 de bestanden niet meer lezen. Dat gold al na migratie 2.

## Back-up (P2)
- Inhoud: `onderhoud.db` versleuteld met de hoofdsleutel, de bestanden zoals ze al zijn (al versleuteld, dus snel), en `garage-backup.json` met `"format": 2` en de met het wachtwoord versleutelde hoofdsleutel (plus zout en scrypt-instellingen).
- De controle van K3 ontsleutelt de database in het geheugen voor `PRAGMA integrity_check`.
- **Wachtwoord instellen:** bij de eerste start na de update, en later te wijzigen bij Back-up. Bij wijzigen wordt alleen de tweede kopie van de hoofdsleutel opnieuw verpakt. Oude back-ups houden hun oude wachtwoord.
- **Nog geen wachtwoord ingesteld:** back-ups worden toch gemaakt, maar zijn dan alleen op deze pc en dit account terug te zetten. De app toont een waarschuwing tot het wachtwoord er is. Liever een beperkte back-up dan geen.

## Terugzetten
1. Back-up met `format` 2 en dezelfde hoofdsleutel als hier (vergelijken via een vingerafdruk van de sleutel): geen wachtwoord nodig.
2. Andere hoofdsleutel (andere pc, of na een nieuwe installatie): vraag het wachtwoord, ontsleutel de hoofdsleutel en bewaar hem hier met DPAPI. De veiligheidskopie van de huidige stand wordt eerst nog met de huidige sleutel gemaakt.
3. Oude back-up zonder `format`: terugzetten zoals nu, daarna draait de migratie.
4. Fout wachtwoord: niets veranderd, opnieuw proberen.

## Sleutel kwijt
- `garage.key` niet te openen (andere pc, Windows opnieuw geïnstalleerd, wachtwoord gereset door een beheerder): Garage start met een scherm "Voer je back-upwachtwoord in". Met het wachtwoord haalt de app de hoofdsleutel uit de eigen database en bewaart hem opnieuw met DPAPI. Er is dan zelfs geen back-up nodig.
- Of een reset van een Microsoft-account de DPAPI-sleutels bewaart, weet ik niet zeker; daarom bestaat deze route.
- **Windows-account én wachtwoord kwijt: dan zijn de gegevens weg.** Dat staat duidelijk in beeld bij het instellen van het wachtwoord. Er komt geen achterdeur.

## Afgewezen
- **SQLCipher (hele database versleutelen):** extra gecompileerd pakket (`sqlcipher3`) in plaats van de standaardmodule `sqlite3`, zonder zekerheid over wheels voor 3.15, en alle databasecode moet erop over. De gevoelige velden in de database zijn met veldversleuteling afgedekt; de rest (kenteken, beurten) is laag tot middel en staat ook in de verkoop-PDF.
- **Alleen een wachtwoord, geen DPAPI:** veiliger tegen iemand op jouw account, maar een wachtwoord bij elke start en bij vergeten alles kwijt. Niet gekozen.
- **Eén sleutel voor alle bestanden:** eenvoudiger, maar dan werkt crypto-shredding per bestand niet.
- **Windows CNG via `ctypes` in plaats van `cryptography`:** geen nieuw pakket, maar zelf AES-GCM aanroepen is foutgevoelig. Versleuteling bouw je niet zelf.

## Volgorde van bouwen
Elke stap een eigen PR met tests:
1. `app/crypto.py` met tests (DPAPI, formaat, sleutels), `cryptography` in de lockbestanden, zelftest.
2. `read_file`/`write_file` en alle aanroepen erop; P8.
3. Migratie 3 en de versleutelstap; lak-termen en bestandsnamen.
4. Back-up format 2, wachtwoordscherm, terugzetten met wachtwoord, het scherm bij een ontbrekende sleutel.
5. Docs: gegevensregister, datadictionary, `PRIVACY`, begrippenlijst.
