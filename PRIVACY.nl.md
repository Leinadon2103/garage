# Privacy

*English: [PRIVACY.md](PRIVACY.md)*

Garage is een programma voor op je eigen computer. Er is geen account en geen server van de maker: **de maker van Garage krijgt geen gegevens van je binnen**. Je beheert je gegevens zelf.

## Welke gegevens Garage bewaart
| Gegevens | Waar | Gevoeligheid |
|---|---|---|
| Voertuiggegevens (kenteken, merk, model, RDW-gegevens, specificaties) | database `onderhoud.db` | laag |
| Onderhoud, km-standen, kosten, stalling | database | laag |
| Originele facturen (met je naam, adres, telefoon e.d.) | map `facturen` | **hoog** |
| Gelakte facturen | map `facturen` | laag |
| Documenten (kentekenbewijs, polis, aankoopfactuur) | map `documenten` | **hoog** |
| Je eigen lak-termen (naam, adres) | database | **hoog** |
| Voertuigfoto (zonder EXIF/GPS) | map `beelden` | laag |
| Foutmeldingen | `app.log` (max. ca. 3 MB) | laag |
| Tekst van herinneringen | meldingencentrum van Windows | laag |

Het geïnstalleerde programma bewaart dit in **Documenten\Garage**. Staat je map Documenten in OneDrive, dan staan je gegevens daar dus ook.

**Versleuteld:** facturen, documenten en foto's, en in de database je lak-termen en de oorspronkelijke bestandsnamen. De sleutel hoort bij je Windows-account (DPAPI): iemand die alleen bij je OneDrive of je schijf kan, kan ze niet lezen. Iemand die als jou is ingelogd wel, net als Garage zelf. Hoe het werkt: [ontwerp versleuteling](docs/encryption.nl.md).

Het volledige overzicht, met doel en bewaartermijn per gegeven, staat in het [gegevensregister](docs/data-register.nl.md).

## Wat er naar buiten gaat
Alleen als je de functie gebruikt:

| Functie | Wat | Naar |
|---|---|---|
| Voertuig opzoeken, RDW-gegevens vernieuwen | kenteken, IP-adres | RDW Open Data (Nederland) |
| Beeld zoeken en kiezen | merk en model of je zoekterm, IP-adres | Sketchfab en Wikimedia Commons (VS) |
| 3D-model bekijken | IP-adres | Sketchfab (VS). Standaard pas na een klik, in te stellen bij *Instellingen > Privacy*. |
| Back-up naar een cloudmap | alles, ook de niet-gelakte originelen, maar versleuteld; alleen te openen op deze pc of met je back-upwachtwoord | de map die je kiest (bijv. OneDrive) |
| Herinneringen | niets, de melding is lokaal | - |

Tekstherkenning (OCR) gebeurt offline met de ingebouwde Windows-OCR.

## Delen met een koper
De verkoop-PDF bevat alleen facturen die je hebt gecontroleerd en gelakt. Een factuur die nog niet gecontroleerd is, gaat nooit mee, en ook de bestandsnaam ervan niet. Documenten (kentekenbewijs, polis) gaan nooit mee. Kies je "kosten niet tonen", dan worden de bedragen ook op de facturen weggelakt.

## Bewaren en verwijderen
- **Voertuig verwijderen** wist het voertuig met alle onderhoud, facturen en documenten uit de app. Verwijderde gegevens worden in de database echt overschreven, niet alleen als vrij gemarkeerd. Van elk bestand wordt eerst de sleutel gewist: ook een oude versie in OneDrive is daarna onleesbaar.
- **Back-ups** bevatten een kopie: de laatste 10 automatische back-ups en 3 veiligheidskopieën blijven bewaard. Wat je verwijdert, staat dus nog in oudere back-ups tot die worden opgeruimd. Verwijder die zelf als het direct weg moet.
- **Herinneringen** blijven in het meldingencentrum van Windows staan tot je ze daar wist.
- **Garage verwijderen** (Instellingen > Apps) vraagt of je ook je gegevens wilt wissen.
- **Al je gegevens meenemen** kan met een back-up: dat is één zip met alles. Op een andere pc open je die met je back-upwachtwoord. Ben je dat wachtwoord kwijt én werkt je Windows-account niet meer, dan zijn je gegevens niet meer te openen; daar is geen achterdeur voor.
