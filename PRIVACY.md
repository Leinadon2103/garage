# Privacy

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

Het geïnstalleerde programma bewaart dit in **Documenten\Garage**. Staat je map Documenten in OneDrive, dan staan je gegevens daar dus ook.

## Wat er naar buiten gaat
Alleen als je de functie gebruikt:

| Functie | Wat | Naar |
|---|---|---|
| Voertuig opzoeken, RDW-gegevens vernieuwen | kenteken, IP-adres | RDW Open Data (Nederland) |
| Beeld zoeken en kiezen | merk en model of je zoekterm, IP-adres | Sketchfab en Wikimedia Commons (VS) |
| 3D-model bekijken | IP-adres | Sketchfab (VS). Standaard pas na een klik, in te stellen bij *Instellingen > Privacy*. |
| Back-up naar een cloudmap | alles, ook de niet-gelakte originelen | de map die je kiest (bijv. OneDrive) |
| Herinneringen | niets, de melding is lokaal | - |

Tekstherkenning (OCR) gebeurt offline met de ingebouwde Windows-OCR.

## Delen met een koper
De verkoop-PDF bevat alleen facturen die je hebt gecontroleerd en gelakt. Een factuur die nog niet gecontroleerd is, gaat nooit mee. Documenten (kentekenbewijs, polis) gaan nooit mee. Kies je "kosten niet tonen", dan worden de bedragen ook op de facturen weggelakt.

## Bewaren en verwijderen
- **Voertuig verwijderen** wist het voertuig met alle onderhoud, facturen en documenten uit de app.
- **Back-ups** bevatten een kopie: de laatste 10 automatische back-ups en 3 veiligheidskopieën blijven bewaard. Wat je verwijdert, staat dus nog in oudere back-ups tot die worden opgeruimd. Verwijder die zelf als het direct weg moet.
- **Garage verwijderen** (Instellingen > Apps) vraagt of je ook je gegevens wilt wissen.
- **Al je gegevens meenemen** kan met een back-up: dat is één zip met alles.
