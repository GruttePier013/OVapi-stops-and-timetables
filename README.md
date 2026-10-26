<p align="center"><img src="custom_components/ovapi_bustijden/brand/logo@2x.png" width="220" alt="OVapi bussen en halteinformatie"></p>

# OVapi bussen en halteinformatie

[![HACS Validate](https://github.com/GruttePier013/OVapi-stops-and-timetables/actions/workflows/validate.yml/badge.svg)](https://github.com/GruttePier013/OVapi-stops-and-timetables/actions/workflows/validate.yml)
[![Open je Home Assistant en open deze repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GruttePier013&repository=OVapi-stops-and-timetables&category=integration)

Live vertrektijden van bussen (en optioneel tram, metro en veerboot) in Nederland in Home Assistant, via [OVapi](https://v0.ovapi.nl). Je zoekt haltes op een kaart, kiest per rijrichting een halte en krijgt per halte sensoren, een kaartmarker en instellingen voor wanneer er ververst wordt.

## Installeren

1. HACS → ⋮ (rechtsboven) → **Aangepaste repositories** → voeg `https://github.com/GruttePier013/OVapi-stops-and-timetables` toe met type **Integratie**.
2. Download **OVapi bussen en halteinformatie** en **herstart Home Assistant**.
3. Instellingen → Apparaten & services → **Integratie toevoegen** → *OVapi bussen en halteinformatie*.

*Handmatig:* kopieer `custom_components/ovapi_bustijden` naar `config/custom_components/` en herstart.

## Haltes toevoegen

1. Kies **Haltes zoeken** (of *TPC-codes invoeren* als je de OVapi-code al kent).
2. Typ boven de kaart een **plaats** (`Voorbeeldstad`: kaart naar het centrum, 2 km) of **straat + plaats** (`Voorbeeldlaan Voorbeeldstad`: pin op de straat) en druk op Enter. Verplaats de pin en maak de cirkel groter of kleiner door aan de rand te slepen (50–5000 m). Kies eventueel ook tram, metro of veerboot.
3. Vink de haltes aan. Elke rijrichting is een eigen halte, met de afstand tot huis en de huidige vertrektijden eronder. Bij de eerste keer wordt het haltebestand van [NDOV Loket](https://data.ndovloket.nl/haltes/) gedownload (±13 MB, met voortgang in %).
4. *Extra opties*: richting in de apparaatnaam (standaard aan, bijv. `Voorbeeldstad, Markt - ri. Station`), na hoeveel uur data als verouderd geldt (standaard 12) en eventueel het haltebestand opnieuw downloaden als een nieuwe halte ontbreekt.
5. Per halte: naam, lijnen die je wilt zien (leeg = alle) en eventueel *Alleen bestemmingen met…* (bijv. `Station, Noord`).

Later wijzig je alles via **Configureren** (haltes zoeken, bewerken, verwijderen, instellingen) of via ⋮ → **Opnieuw configureren**.

## Entiteiten per halte

| Entiteit | Inhoud |
|---|---|
| **Vertrektijden** | `14:05 (Lijn 74)`, `Geen bussen` of `Data vernieuwen`; attribuut `vertrekken` (lijst met tijd, lijn, bestemming, vertraging, minuten) |
| **Volgende vertrek** | tijdstip van de eerstvolgende bus |
| **Lijnen** | `34, 73, 350` (of `Geen`) |
| **Lijn 34, Lijn 73, …** | volgende vertrek per lijn (aan te zetten/uit te zetten in de instellingen); nieuwe lijnen krijgen vanzelf een sensor |
| **Halte op kaart** (diagnose) | `Lijn 74 om 16:01 (+2 min)`; staat als marker op de kaart |
| **Personen nabij** | `Geen` of de namen van de gevolgde personen in de zone |
| **Vernieuwen** (knop) | direct verversen |
| **Vernieuwingsfrequentie** | *Nooit* (standaard), dagelijks, ieder uur, elke 30 of 5 minuten, ook alleen in de spits |
| **Vernieuwen als in zone** | aan: ververst zodra een gevolgd persoon in de zone komt (uit als de halte in je thuiszone ligt) |
| **Zone radius** · **Vernieuwen in zone elke** · **Vernieuwen in zone stoppen na** | 500 m · 2 min · 30 min |
| Diagnose | Opgehaalde vertrektijden (aantal nog komende vertrekken), Bijgewerkt, TPC-code, haltecode, Afstand tot huis |

De minuten tot vertrek lopen elke minuut door zonder nieuwe aanvraag; vertrokken bussen verdwijnen vanzelf.

## Ververst wanneer jij het wilt

Standaard wordt er niet gepolld. Ververs met de knop, met een **vernieuwingsfrequentie**, automatisch **als je in de zone van de halte komt**, of met de actie:

```yaml
# alle haltes
action: ovapi_bustijden.refresh_stops
# alleen specifieke haltes
action: ovapi_bustijden.refresh_stops
target:
  entity_id:
    - sensor.voorbeeldstad_markt_vertrektijden
```

Welke personen meetellen stel je in via Configureren → Instellingen (leeg = alle personen).

## Geen gegevens of verouderd

Staat een halte op **Data vernieuwen** (grijze marker met vraagteken)? Dan zijn de gegevens ouder dan de ingestelde tijd of is er geen enkele bus meer op de halte (`Vertrektijden` en `Lijnen` tonen dan `Geen bussen` / `Geen`). Druk op *Vernieuwen*. Na een mislukte vernieuwing blijft de laatste data zichtbaar; de attributen `mislukte_pogingen` en `laatste_fout` op *Vertrektijden* vertellen waarom.

## Halte op de kaart

De sensor *Halte op kaart* heeft `latitude`/`longitude` en verschijnt daardoor op het kaartpaneel. In een kaartkaart voeg je hem toe in YAML (de visuele editor toont alleen personen en zones):

```yaml
type: map
entities:
  - sensor.voorbeeldstad_markt
default_zoom: 16
```

Wil je geen geschiedenis van deze sensor, voeg hem dan toe aan `recorder: exclude: entities:` in `configuration.yaml`.

## Bronnen en beperkingen

- Vertrektijden: [OVapi](https://v0.ovapi.nl) · haltes: [NDOV Loket](https://data.ndovloket.nl/haltes/) · adressen: [PDOK](https://www.pdok.nl).
- Treinstations hebben geen realtime gegevens bij OVapi en worden niet ondersteund.
- Realtime vertrektijden en positie-attributen worden niet in de recorder-database bewaard.
- Problemen? Download de **diagnostische gegevens** (op de integratiepagina; locaties zijn afgeschermd) en maak een [issue](https://github.com/GruttePier013/OVapi-stops-and-timetables/issues).

Zie [CHANGELOG.md](CHANGELOG.md) voor wijzigingen. Licentie: MIT.
