# Datadictionary

Betekenis van elke tabel en kolom in `onderhoud.db`, met toegestane waarden en herkomst. Waar een gegeven staat en hoe gevoelig het is, staat in het [gegevensregister](gegevens.md).

Stand: schemaversie 1 (`PRAGMA user_version`). De test `tests/test_datadictionary.py` faalt als een kolom in de database hier ontbreekt; voeg bij een nieuwe migratie dus ook de kolommen hier toe.

## Afspraken
- **Datums** zijn tekst in ISO-formaat `JJJJ-MM-DD`. Bij km-standen en stalling worden datums in de toekomst geweigerd.
- **Bedragen** zijn euro's als REAL, afgerond op 2 decimalen. Invoer mag Nederlands (`1.250,50`) of met punt.
- **Km-standen** zijn hele kilometers. Invoer mag met punten (`12.000`).
- **Leeg**: de meeste vrije tekstvelden worden `NULL` als ze leeg zijn. Uitzondering: `merk`, `model`, `kleur` en `datum_eerste_toelating` van `voertuig` worden een lege tekst.
- **Bestandspaden** zijn relatief aan de gegevensmap, met `/`, en moeten voldoen aan `db.GELDIG_PAD` (`facturen/`, `beelden/` of `documenten/` + naam). Paden daarbuiten worden geweigerd, ook uit een back-up.
- **Mime-types**: alleen `application/pdf`, `image/jpeg` en `image/png`. Bij het tonen telt de extensie, nooit de databasewaarde.
- **Verwijderen**: alle kindtabellen hebben `ON DELETE CASCADE` naar hun voertuig of beurt; de bijbehorende bestanden ruimt de app zelf op.

## Tabellen

### voertuig
Eén rij per voertuig.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `kenteken` | TEXT, verplicht | Kenteken zonder streepjes, hoofdletters (`MD12AB`); de app toont het met streepjes | `[A-Z0-9]+` | RDW of handmatig, genormaliseerd door `rdw.normaliseer` |
| `voertuigsoort` | TEXT, verplicht | Soort voertuig; bepaalt welke onderhoudstypes en specificaties horen | `Motorfiets`, `Bromfiets`, `Personenauto`, `Bedrijfsauto`, `Aanhangwagen`, `Overig`, of een andere RDW-soort | RDW of keuzelijst |
| `merk` | TEXT | Merk | vrij | RDW (in Titelcase) of handmatig |
| `model` | TEXT | Handelsbenaming | vrij | RDW of handmatig |
| `datum_eerste_toelating` | TEXT | Eerste toelating op de weg | ISO-datum of leeg | RDW of handmatig |
| `kleur` | TEXT | Eerste kleur | vrij | RDW of handmatig |
| `cilinderinhoud` | INTEGER | Cilinderinhoud in cc | ≥ 0 of NULL | RDW of handmatig |
| `bijnaam` | TEXT | Eigen naam voor het voertuig | vrij of NULL | handmatig |
| `beeld_type` | TEXT | Soort voertuigbeeld | `3d`, `foto`, `eigen` of NULL | keuze in het beeldscherm |
| `beeld_ref` | TEXT | Sketchfab-uid bij een 3D-model | 32 hex-tekens of NULL | Sketchfab |
| `beeld_titel` | TEXT | Titel van het beeld (naamsvermelding) | vrij of NULL | Sketchfab/Wikimedia, of `Eigen foto` |
| `beeld_bron` | TEXT | Link naar de bron (naamsvermelding) | `https://sketchfab.com/...`, `https://commons.wikimedia.org/...` of NULL | app |
| `beeld_pad` | TEXT | Lokale afbeelding; bij 3D de offline-terugval | `beelden/<uuid>.jpg` of NULL | app |
| `schema_bron` | TEXT | Waar de eigen onderhoudsintervallen vandaan komen | vrij of NULL (bijv. "instructieboekje") | handmatig |
| `rdw_json` | TEXT | Extra RDW-gegevens als JSON, zie [rdw_json](#rdw_json) | JSON-object of NULL | RDW, gefilterd door `main._rdw_extra` |
| `rdw_bijgewerkt` | TEXT | Datum van de laatste RDW-opzoeking | ISO-datum of NULL | app |

### onderhoudstype
Soorten onderhoud, met een standaardinterval (de "vuistregel"). Bij een lege tabel vult `db.init()` de standaardlijst `STANDAARD_TYPES`.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `naam` | TEXT, verplicht | Naam, bijv. "Olie vervangen". `Overig` staat altijd onderaan. | vrij | standaardlijst of instellingen |
| `voertuigsoorten` | TEXT, verplicht | Voor welke soorten dit type geldt | `*` (alle) of soorten gescheiden door komma's, bijv. `Motorfiets,Bromfiets` | standaardlijst of instellingen |
| `interval_km` | INTEGER | Elke zoveel km | ≥ 0 of NULL (geen km-interval) | idem |
| `interval_maanden` | INTEGER | Elke zoveel maanden | ≥ 0 of NULL (geen tijdsinterval) | idem |

Het eerste interval dat verloopt (km of tijd) telt.

### voertuig_interval
Eigen interval per voertuig dat afwijkt van de vuistregel, bijv. uit het instructieboekje. Geen rij = vuistregel van `onderhoudstype`.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `voertuig_id` | INTEGER, verplicht | Voertuig | bestaande `voertuig.id` | app |
| `type_id` | INTEGER, verplicht | Onderhoudstype | bestaande `onderhoudstype.id` | app |
| `interval_km` | INTEGER | Eigen km-interval | ≥ 0 of NULL | handmatig |
| `interval_maanden` | INTEGER | Eigen tijdsinterval | ≥ 0 of NULL | handmatig |

Sleutel: (`voertuig_id`, `type_id`).

### beurt
Eén onderhoudsmoment, met één of meer onderhoudstypes en nul of meer facturen.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `voertuig_id` | INTEGER, verplicht | Voertuig | bestaande `voertuig.id` | app |
| `datum` | TEXT, verplicht | Datum waarop het onderhoud is uitgevoerd | ISO-datum | handmatig of uit de factuur gelezen |
| `kilometerstand` | INTEGER | Km-stand bij het onderhoud | ≥ 0 of NULL; moet passen in het km-verloop (`extra.km_controle`) | idem |
| `uitgevoerd_door` | TEXT | Garage of persoon | vrij of NULL | idem |
| `kosten` | REAL | Totaalbedrag in euro | getal of NULL (negatief wordt niet geweigerd) | idem |
| `opmerking` | TEXT | Toelichting; staat in de verkoop-PDF | vrij of NULL | handmatig |

### beurt_type
Koppeling tussen beurt en onderhoudstype (minstens één per beurt).

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `beurt_id` | INTEGER, verplicht | Beurt | bestaande `beurt.id` | app |
| `type_id` | INTEGER, verplicht | Onderhoudstype | bestaande `onderhoudstype.id` | keuze in het formulier |

Sleutel: (`beurt_id`, `type_id`).

### factuur
Bijlage bij een beurt. Na het lakken zijn er twee bestanden: het origineel en de gelakte versie.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `beurt_id` | INTEGER, verplicht | Beurt | bestaande `beurt.id` | app |
| `pad` | TEXT, verplicht | Het bestand dat de app toont en exporteert: de gelakte versie als die er is, anders het origineel | `facturen/<voertuig-id>/<uuid>.<ext>` of `.../<uuid>-gelakt.<ext>` | app |
| `originele_naam` | TEXT, verplicht | Bestandsnaam bij het uploaden | vrij | upload |
| `mime_type` | TEXT, verplicht | Bestandstype | zie Afspraken | uit de extensie, gecontroleerd op de eerste bytes |
| `origineel_pad` | TEXT | Het niet-gelakte origineel | pad zoals hierboven, of NULL = nog niet gelakt (dan is `pad` het origineel) | app |
| `gecontroleerd` | INTEGER, verplicht | Gecontroleerd op persoonsgegevens; alleen dan gaat de factuur mee in de verkoop-PDF | `0` of `1` (standaard `0`) | lakscherm |
| `boxen` | TEXT | Toegepaste lakbalken als JSON-lijst `[{"pagina", "x0", "y0", "x1", "y1"}]`, posities als fractie 0-1 van de pagina; pagina telt vanaf 0 | JSON of NULL | lakscherm |

### document
Losse documenten bij een voertuig. Gaan nooit mee in de verkoop-PDF.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `voertuig_id` | INTEGER, verplicht | Voertuig | bestaande `voertuig.id` | app |
| `soort` | TEXT, verplicht | Soort document | `extra.DOCUMENTSOORTEN` (kentekenbewijs, polis, aankoopfactuur, garantiebewijs, instructieboekje, keuringsrapport, overig) | keuzelijst |
| `titel` | TEXT | Eigen omschrijving | vrij of NULL | handmatig |
| `pad` | TEXT, verplicht | Bestand | `documenten/<voertuig-id>/<uuid>.<ext>` | app |
| `originele_naam` | TEXT, verplicht | Bestandsnaam bij het uploaden | vrij | upload |
| `mime_type` | TEXT, verplicht | Bestandstype | zie Afspraken | uit de extensie |
| `vervaldatum` | TEXT | Verloopt op (bijv. polis); geeft een herinnering | ISO-datum of NULL | handmatig |
| `toegevoegd` | TEXT, verplicht | Datum van toevoegen | ISO-datum | app |

### km_stand
Losse km-registratie, buiten een beurt om.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `voertuig_id` | INTEGER, verplicht | Voertuig | bestaande `voertuig.id` | app |
| `datum` | TEXT, verplicht | Datum van de stand | ISO-datum, niet in de toekomst | handmatig (standaard vandaag) |
| `kilometerstand` | INTEGER, verplicht | Km-stand | ≥ 0; moet passen in het km-verloop | handmatig |
| `opmerking` | TEXT | Toelichting | vrij of NULL | handmatig |

### stalling
Periode waarin het voertuig stilstaat (bijv. winterstalling). Tijdens een stalling worden geen herinneringen gestuurd.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `id` | INTEGER | Sleutel | | automatisch |
| `voertuig_id` | INTEGER, verplicht | Voertuig | bestaande `voertuig.id` | app |
| `van` | TEXT, verplicht | Begin van de stalling | ISO-datum, niet in de toekomst, niet in een eerdere periode | handmatig |
| `tot` | TEXT | Einde | ISO-datum ≥ `van`, of NULL = staat nog in de stalling (hooguit één per voertuig) | handmatig |
| `km_van` | INTEGER | Km-stand bij wegzetten | ≥ 0 of NULL; moet passen in het km-verloop | handmatig |
| `km_tot` | INTEGER | Km-stand bij ophalen | ≥ 0 of NULL; moet passen in het km-verloop | handmatig |

### spec
Specificaties per voertuig (olie, banden, accu, ...). De standaardlijst per soort staat in `extra.SPECS_MOTOR` en `extra.SPECS_AUTO`; eigen sleutels mogen ook.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `voertuig_id` | INTEGER, verplicht | Voertuig | bestaande `voertuig.id` | app |
| `sleutel` | TEXT, verplicht | Onderdeel, bijv. "Motorolie" | vrij, max. 60 tekens | standaardlijst of handmatig |
| `waarde` | TEXT, verplicht | Waarde, bijv. "10W-40" | vrij, niet leeg, max. 200 tekens | handmatig |
| `volgorde` | INTEGER, verplicht | Volgorde in het scherm en de export | ≥ 0 (standaard 0) | app |

Sleutel: (`voertuig_id`, `sleutel`).

### instelling
Sleutel-waardeparen voor de hele app.

| Kolom | Type | Betekenis | Toegestane waarden | Herkomst |
|---|---|---|---|---|
| `sleutel` | TEXT | Naam van de instelling, zie hieronder | | app |
| `waarde` | TEXT | Waarde als tekst | | app of gebruiker |

| Sleutel | Betekenis | Waarden | Standaard |
|---|---|---|---|
| `thema` | Weergave | `licht`, `donker`, `systeem` | `systeem` |
| `prive_termen` | Eigen lak-termen (naam, adres), één per regel; termen korter dan 3 tekens tellen niet | vrije tekst | leeg |
| `herinneringen` | Windows-meldingen bij aanmelden | `aan`, `uit` | `aan` |
| `melding_laatst` | Laatst getoonde melding, om herhaling te beperken | JSON `{"datum", "tijd", "sleutel"}` | leeg |
| `sketchfab_auto` | 3D-model direct laden in plaats van na een klik | `aan`, `uit` | `uit` |
| `backup_auto` | Automatische back-up (elke 7 dagen) | `aan`, `uit` | `aan` |
| `backup_map` | Gekozen back-upmap | volledig pad of leeg (= standaardmap) | leeg |
| `backup_laatste` | Tijdstip van de laatste back-up | ISO-datum en -tijd | leeg |
| `backup_fout` | Laatste fout van de automatische back-up | tekst (max. 300 tekens) of leeg | leeg |
| `secure_delete_opgeschoond` | Eenmalige `VACUUM` na het aanzetten van `secure_delete` is gedaan | `ja` of leeg | leeg |

`backup_map`, `backup_auto` en `backup_laatste` horen bij deze pc: bij het terugzetten van een back-up blijven de huidige waarden staan.

## rdw_json
Velden in `voertuig.rdw_json`. Alleen deze sleutels worden opgeslagen, met eenvoudige waarden (`main.RDW_VELDEN`).

| Sleutel | Betekenis | Waarde | RDW-bron |
|---|---|---|---|
| `apk_vervaldatum` | Vervaldatum APK | ISO-datum of null | `vervaldatum_apk` |
| `terugroepactie` | Openstaande terugroepactie | true/false | `openstaande_terugroepactie_indicator` |
| `tellerstandoordeel` | Oordeel van de RDW over de km-stand | tekst, bijv. "Logisch" | `tellerstandoordeel` |
| `jaar_tellerstand` | Jaar van de laatste geregistreerde km-stand | jaartal of null | `jaar_laatste_registratie_tellerstand` |
| `catalogusprijs` | Catalogusprijs in euro | getal of null | `catalogusprijs` |
| `datum_tenaamstelling_nl` | Eerste tenaamstelling in Nederland (import) | ISO-datum of null | `datum_eerste_tenaamstelling_in_nederland` |
| `massa_rijklaar` | Massa rijklaar in kg | getal of null | `massa_rijklaar` |
| `brandstof` | Brandstof(fen), gescheiden door " + " | tekst of null | brandstofdataset, `brandstof_omschrijving` |
| `vermogen_kw` | Hoogste nettovermogen in kW | getal of null | brandstofdataset |

Bronnen: RDW Open Data, datasets *Gekentekende voertuigen* (`m9d7-ebf2`) en *Brandstof* (`8ys7-d773`).

## Schemaversies
| Versie | Migratie | Wat |
|---|---|---|
| 1 | `db._migratie_1` | Basis: het schema van Garage 1.3.0. Werkt ook op oudere databases zonder versienummer (vult ontbrekende tabellen en kolommen aan). |

Een nieuwe migratie komt achteraan in `db.MIGRATIES` en krijgt hier een regel. Gegevens van een nieuwere versie dan de app kent, worden niet geopend en niet teruggezet.
