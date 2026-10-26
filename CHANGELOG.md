# Changelog

Formaat gebaseerd op [Keep a Changelog](https://keepachangelog.com/nl/1.1.0/).

## [0.9.1]
### Toegevoegd
- Per halte (Halte bewerken) kun je de marker op de HA-kaart verschuiven met een kaartkiezer. Vink *Marker terugzetten* aan en verstuur om te resetten. Alleen de marker verschuift; afstanden en zones gebruiken de echte haltepositie.

### Gewijzigd
- README opgeruimd en overzichtelijker: aparte kopjes voor entiteiten, instellingen, verversen en foutbeveiliging, en een tabel met de twee haltemarkers (normaal en *Data vernieuwen*).

## [0.9.0]
Alle verbeteringen uit de EnBW-Laadpalen-integratie in één release.

### Toegevoegd
- **Foutbeveiliging** (sectie in Instellingen): exponentiële wachttijd per halte (60–900 s), pauze na 5 opeenvolgende fouten met een reparatiemelding en de diagnose-entiteit *Gepauzeerd*; API-stroomonderbreker (10 fouten of HTTP 429 met `Retry-After`, 60–1800 s, melding `api_paused`); minimale tussenruimte van 3 s tussen automatische aanroepen; opstart met maximaal 5 gelijktijdige haltes. Handmatig vernieuwen werkt altijd.
- **Slimmere zonebezoeken:** één vernieuwing bij binnenkomst (minstens 60 s na de vorige), daarna op de gekozen frequentie; stoppen na 10/30/60/120 min of onbeperkt; afkoeltijd (standaard 15 min); schakelaar *Alleen vernieuwen bij naderen* (25 m dichterbij); sensor *Afstand tot persoon*.
- **Standaardwaarden** voor nieuwe haltes (op de eerste installatie op één scherm, later in Instellingen) met *Toepassen op bestaande haltes* (standaard uit).
- Actie `ovapi_bustijden.refresh` met doelkiezer en de knop *Alle haltes vernieuwen*; `refresh_stops` blijft als alias.
- Diagnostics (ook per apparaat), diagnose-entiteit *Opeenvolgende fouten*, reparatiemeldingen worden opgeruimd bij uitladen.
- `async_remove_config_entry_device`: een halte verwijderen via het apparaat verwijdert hem ook uit de configuratie.
- Tests voor vertaal-placeholders, manifest/hacs.json, services en verboden aanroepen; mypy in CI.

### Gewijzigd
- Minimale Home Assistant-versie 2026.2.0 (`hacs.json`); `import_executor` aan; geen verouderde aanroepen meer; mypy schoon.
- Informatieve attributen worden niet meer in de recorder bewaard (`MATCH_ALL`); verouderde data wordt *onbekend* en minutelijks bijgewerkt.
- *Halte op kaart* is een sensor met `latitude`/`longitude` en `entity_picture`; zonder `location_name`. De optie *Logo op de HA-kaart* blijft.
- `services.yaml` gebruikt alleen een entiteitsfilter (hassfest weigert apparaatfilters).
- Code, commentaar en docstrings zijn Engels; teksten voor gebruikers blijven Nederlands.

### Migratie
- De zone-instellingen (frequentie en stoptijd) zijn van getallen naar keuzelijsten gegaan; de oude waarden worden automatisch omgezet naar de dichtstbijzijnde keuze. entity_id's en unique_id's blijven gelijk.
- De oude `device_tracker` van *Halte op kaart* is al sinds 0.8 een sensor; resterende exemplaren worden opgeruimd.

### Niet getest
Werkelijke kaartweergave (ook het Kaart-paneel in het zijmenu), echte OVapi-/netwerkgedrag en HACS/GitHub.

## [0.8.3]
### Gewijzigd
- De melding dat het certificaat van OVapi (`v0.ovapi.nl`, hostnaam komt niet overeen) niet geverifieerd kan worden staat nu op *info* in plaats van *waarschuwing*; de integratie schakelt de verificatie voor die host zelf uit, zoals eerder.
- Repository verhuisd naar `GruttePier013/OVapi-stops-and-timetables`; manifest, README en licentie verwijzen daarheen. README is ingekort en richt zich alleen op Home Assistant-gebruikers.
- Alle voorbeelden in README, vertalingen, documentatie en tests zijn generiek gemaakt (geen echte plaatsen, straten of TPC-codes meer).
- Het veld *Plaatsnaam* is nu een adreszoeker (PDOK): alleen een plaats zet de kaart op het centrum van de bebouwde kom (2 km); straat + plaats zet de pin op die straat (500 m); een adres of postcode op dat punt (250 m).
- De invoervelden *Adres* en *Coördinaten* onderaan de zoekstap zijn vervallen (de adreszoeker en de kaart vervangen ze).
- De afstand in de haltelijst is nu de afstand tot huis (thuislocatie van Home Assistant) in km met één decimaal, met *lopen* alleen onder 2 km: `0,7 km lopen van huis`, `50,6 km van huis`. De diagnosesensor heet *Afstand tot huis* en rekent live vanaf de thuislocatie.
- *Richting in de apparaatnaam opnemen* staat nu standaard aan (`Voorbeeldstad, Markt - ri. Station`).

## [0.8.2]
### Toegevoegd
- Veld *Plaatsnaam* boven de kaart in *Haltes zoeken*: de kaart springt naar het centrum van de plaats (zoekstraal 2 km).
- Aparte stap *Extra opties* in de configflow met richting in de naam, timeout voor verouderde data en het opnieuw downloaden van het NDOV-bestand.
- Instructie in de README om de geschiedenis van *Halte op kaart* uit te zetten (recorder-exclude).

### Gewijzigd
- *Halte op kaart* heet nu als het apparaat (de gekozen haltenaam; entity_id `sensor.<haltenaam>`) en staat onder *Diagnose*.
- *Opgehaalde vertrektijden* toont het aantal vertrekken dat nog in de toekomst ligt (loopt elke minuut terug; 0 = geen data meer en *Halte op kaart* gaat naar *Data vernieuwen*). Het aantal uit de laatste aanvraag staat in het attribuut `opgehaald`.
- Sensoren per lijn staan standaard aan.
- *Vernieuwingsfrequentie in zone* heet nu **Vernieuwen als in zone** (Engels: *Refresh when in zone*) en staat standaard uit als de halte in de thuiszone van Home Assistant ligt.
- Nieuw integratielogo (afgeronde hoeken) en nieuwe haltemarkers: blauw met klok en grijs met vraagteken.

## [0.8.1]
### Toegevoegd
- Sensoren per lijn (optioneel), die nieuwe lijnen vanzelf oppikken.
- Ondersteuning voor tram, metro en veerboot (kies bij *Haltes zoeken*; het NDOV-haltebestand wordt daarvoor eenmalig opnieuw opgebouwd) en een bestemmingsfilter per halte.
- *Vernieuwen in zone elke* (instelbaar interval, standaard 2 min).
- Herconfiguratiestap (*Opnieuw configureren*), diagnostische gegevens, `icons.json` en een reparatiemelding bij een NDOV-haltebestand ouder dan 90 dagen.
- Robuustheid: laatste data blijft zichtbaar na een mislukte vernieuwing, gebundelde OVapi-aanvragen, nieuwe pogingen bij tijdelijke fouten, niet-blokkerende eerste vernieuwing, één minuuttimer per halte, SSL-verificatie met terugval, CI met nieuwste Home Assistant.
- *Halte op kaart* toont ook *Data vernieuwen* met de grijze marker als er geen enkele bus meer op de halte is.

### Opgelost
- Laden mislukte op nieuwere Home Assistant-versies (`async_extract_referenced_entity_ids` bestaat daar niet meer in `helpers.service`); de actie leest het doel nu zelf uit.

## [0.8.0]
### Toegevoegd
- Actie `ovapi_bustijden.refresh_stops`: zonder doel alle haltes, met doel alleen de opgegeven haltes vernieuwen.
- Instelling **Vernieuwen in zone stoppen na** (standaard 30 min).
- Instelbare timeout voor verouderde data (standaard 12 uur), bij de installatie en in Configureren → Instellingen.
- Status **Data vernieuwen** met doorgestreept icoon als de gegevens ouder zijn dan 12 uur (*Vertrektijden*, *Lijnen*, *Halte op kaart*), en een kaartmarker *halte met vraagteken* bij geen of verouderde busgegevens.
- Nieuwe logo's en haltemarker; logo bovenaan de README.

### Gewijzigd
- De integratie heet nu **OVapi bussen en halteinformatie** (domein `ovapi_bustijden` ongewijzigd).
- **Breaking:** *Halte op kaart* is nu een sensor (was device_tracker) met als status de eerstvolgende bus, bijv. `Lijn 74 om 16:01 (+2 min)`. De oude device_tracker wordt automatisch opgeruimd; pas dashboards aan naar de nieuwe sensor.
- Namen: *Vernieuwingsfrequentie* en *Vernieuwingsfrequentie in zone*; standaard zone-radius 500 m.

## [0.7.0]
### Toegevoegd
- **Vernieuwen in zone** ververst nu elke 2 minuten zolang een gevolgd persoon in de zone is (daarvoor: één keer bij binnenkomst) en heeft een uitleg in het attribuut `uitleg`; ook bij *Zone radius*.
- Optie in de configflow om de richting in de apparaatnaam op te nemen als `Voorbeeldstad, Markt - ri. Station` (standaard uit). Dubbele namen worden dan op windrichting onderscheiden: `Voorbeeldstad, Markt (NO)`.

### Gewijzigd
- Resultatenstap: één checkbox per halte met de vertrektijden eronder (helpertekst), in plaats van een apart overzicht met een nummerlijst.
- *Personen nabij* toont `Geen` of de namen (was: het aantal). De aan/uit-entiteit *Persoon nabij* is verwijderd en wordt bij het laden uit het entiteitenregister opgeruimd.
- Integratielogo's (`brand/`) zijn weer de oorspronkelijke transparante set, met licht afgeronde hoeken; het halte-logo op de kaart is ongewijzigd.

## [0.6.2]
### Gewijzigd
- Het vinkje om het haltebestand te verversen toont nu de ouderdom: *NDOV data 72 dagen oud (bestand van 2026-08-12) opnieuw downloaden*, met uitleg dat de configuratie dan opnieuw begint. De ouderdom volgt de datumstempel van het bestand.
- De dynamische tekst in de resultatenstap (vertrektijden, aantallen, voortgang) is altijd Nederlands, ook als de server-taal van Home Assistant Engels is.

## [0.6.1]
### Opgelost
- Een door OVapi geleverde plaatsnaam `unknown` komt niet meer in de standaardnaam van een halte (`unknown, Voorbeeldstad, Voorbeeldlaan` wordt `Voorbeeldstad, Voorbeeldlaan`).

## [0.6.0]
### Toegevoegd
- Sensor **Lijnen** (`34, 73, 350`, gesorteerd; lijst in het attribuut `lijnen`) en sensor **Personen nabij** (`Geen` of het aantal).
- Logo op de HA-kaart (`halte_marker.png`); in *Configureren* uit te zetten. Nieuw merklogo in `brand/`.
- NDOV-haltebestand: ouderdom wordt bijgehouden en getoond; in de resultatenlijst kun je opnieuw laten downloaden (terug naar stap 1, daarna dezelfde zone).

### Gewijzigd
- Resultatenlijst: per halte een blok (`naam · 📍 TPC · afstand lopen | windrichting` met de huidige vertrektijden per lijn, of `⚠️ Geen vertrektijden informatie`) en daaronder de checkboxes.
- Namen: *Standaard vernieuwingsfrequentie*, *Zone radius*, *Vernieuwen in zone*, *Persoon nabij*. Bestaande entity_id's blijven ongewijzigd.
- Integratienaam **OVapi-bushalte** (sinds 0.8.0: **OVapi bussen en halteinformatie**), fabrikant **OVapi en NDOV**, codeowner @GruttePier013.

## [0.5.0]
### Gewijzigd
- Haltebron is nu het **NDOV Loket-haltebestand (CHB)** (ExportCHB + halteverwijstabel) in plaats van de OVapi-haltegebiedenlijst en GTFS. Een quay (haltepaal) is één rijrichting en wordt één apparaat; TPC-codes worden live bij OVapi gecontroleerd.
- Configflow in drie fases met een **voortgangsring in procenten**: downloaden (±13 MB), uitpakken (±300 MB, gestreamd) en haltes zoeken.
- Na het kiezen van een zone volgt **direct een lijst met checkboxes** (per rijrichting) in plaats van een overzichtskaartje.
- Kaart bovenaan het zoekformulier, adres en coördinaten eronder.
- *Halte op kaart* gebruikte het gewone `mdi:bus`-icoon; de eigen marker-afbeelding en het statische pad werden tijdelijk verwijderd (terug in 0.6.0).
- `Pillow`, GTFS-, kaarttegel- en richtingsgroeperingscode zijn verwijderd; de `http`-afhankelijkheid is niet meer nodig.

## [0.4.0]
### Gewijzigd
- Configflow: de kaartkiezer heeft nu een **sleepbare zoekcirkel** rond de pin (de losse radius-invoer is vervallen).
- Haltes worden **per rijrichting** getoond en toegevoegd: elke gekozen richting wordt een eigen apparaat; bussen met een andere eindbestemming in dezelfde richting worden samengevoegd.
- Het overzichtskaartje gebruikt alleen OpenStreetMap-tegels: de CARTO-basemaps toonden een "API KEY REQUIRED"-watermerk. De keuze voor een kaartstijl is vervallen.
- Het *Terug*-vinkje is uit alle stappen verwijderd.

## [0.3.0]
### Toegevoegd
- GitHub-workflows voor HACS-validatie, hassfest, ruff en pytest.
- `CONFIG_SCHEMA` (alleen config entries), `hacs.json` met `country: NL`, tests die de HACS-/hassfest-vereisten bewaken.

### Gewijzigd
- `manifest.json`: sleutels gesorteerd volgens hassfest en `codeowners`, `documentation` en `issue_tracker` ingevuld (zie `scripts/set_github_user.sh`).
- README opgeschoond voor publicatie; niet-ondersteunde sleutel `render_readme` uit `hacs.json` verwijderd.

## [0.2.0]
- Juiste OVapi-endpoint (`/tpc/<code>`), tijdzonefix, meerdere TPC-codes per halte met lijnfilter, verversschema per halte, nabijheid, kaart in de configflow en NL/EN-vertalingen.
