"""Constants for OVapi bussen en halteinformatie."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ovapi_bustijden"
NAME = "OVapi bussen en halteinformatie"
PLATFORMS = [
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

CONF_ADDRESS = "address"
CONF_LOCATION = "location"
CONF_STOPS = "stops"
CONF_SELECTED_STOPS = "selected_stops"
CONF_TPC_CODES = "tpc_codes"
CONF_LINES = "lines"
CONF_MAX_DEPARTURES = "max_departures"
CONF_PERSONS = "persons"
CONF_MAP_LOGO = "map_logo"
CONF_DIRECTION_IN_NAME = "direction_in_name"
CONF_STALE_HOURS = "stale_hours"
CONF_MODES = "modes"
CONF_PLACE = "place"
PLACE_RADIUS = 2000  # m: zoekstraal na het opzoeken van een plaatsnaam (de kaart zoomt daarmee ruim uit)
STREET_RADIUS = 500  # m: straat + plaats
ADDRESS_RADIUS = 250  # m: huisadres of postcode
CONF_LINE_SENSORS = "line_sensors"
DEFAULT_LINE_SENSORS = True
CONF_DESTINATIONS = "destinations"
MODES = ("bus", "tram", "metro", "boat")  # vervoerstypen met realtime gegevens bij OVapi
DEFAULT_MODES = ["bus"]
DEFAULT_STALE_HOURS = 12
SERVICE_REFRESH_STOPS = "refresh_stops"
CONF_REFRESH_NDOV = "refresh_ndov"

DEFAULT_RADIUS = 250
DEFAULT_MAX_DEPARTURES = 6
GRACE_SECONDS = 30  # bus blijft 30 s na vertrek zichtbaar

RETRY_DELAYS = (1, 3)  # seconden wachttijd voor poging 2 en 3 bij tijdelijke OVapi-fouten
BATCH_CHUNK = 40  # TPC-codes per gecombineerd OVapi-verzoek
FIRST_REFRESH_TIMEOUT = 10  # seconden die het opstarten maximaal op de eerste verversing wacht
NDOV_OUTDATED_DAYS = 90  # daarna een reparatiemelding: haltebestand opnieuw downloaden
INSECURE_RETRY_AFTER = 24 * 3600  # na een SSL-fout bij OVapi pas na 24 uur weer met verificatie proberen
REQUEST_TIMEOUT = 30  # grote stations geven grote antwoorden
# Haltebestand van NDOV Loket (CHB): de index wordt bij gebruik na 14 dagen ververst
CHB_INDEX_MAX_AGE = 14 * 24 * 3600
PREVIEW_MAX_STOPS = 25  # maximaal aantal haltes (richtingen) in de lijst van de configflow
CONF_COORDINATES = "coordinates"
CONF_EXTRA_LINES = "extra_lines"
# Entiteitsnamen altijd in het Nederlands (de dienst is Nederlands), los van de Home Assistant-taal
NAMES_NL = {
    "next_departure": "Volgende vertrek",
    "departures": "Vertrektijden",
    "fetched_departures": "Opgehaalde vertrektijden",
    "last_update": "Bijgewerkt",
    "distance_m": "Afstand tot huis",
    "stop_code": "NDOV haltecode (quay)",
    "tpc_code": "OVapi timing-pointcode",
    "refresh": "Vernieuwen",
    "refresh_schedule": "Vernieuwingsfrequentie",
    "location": "Halte op kaart",
    "nearby_count": "Personen nabij",
    "lines": "Lijnen",
    "proximity_radius": "Zone radius",
    "refresh_on_approach": "Vernieuwen als in zone",
    "zone_stop_after": "Vernieuwen in zone stoppen na",
    "zone_interval": "Vernieuwen in zone elke",
}
USER_AGENT = "HomeAssistant-ovapi_bustijden/0.8.3 (custom integration; OVapi KV78Turbo client)"
STATIC_URL = "/ovapi_bustijden_static"  # publiek pad voor het kaartlogo (entity_picture)
PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
# v0.ovapi.nl heeft een afwijkend certificaat (de yaml-sensoren gebruiken ook verify_ssl: false)
OVAPI_BASE = "https://v0.ovapi.nl"

DEFAULT_PROXIMITY_RADIUS = 500  # meter
DEFAULT_ZONE_STOP_AFTER = 30  # minuten: daarna stopt 'vernieuwen in zone' tot iemand de zone opnieuw binnenkomt
ZONE_REFRESH_INTERVAL = 120  # seconden (standaard 2 min) tussen verversingen zolang iemand in de zone is
DEFAULT_ZONE_INTERVAL = 2  # minuten, instelbaar per halte
MIN_APPROACH_REFRESH_GAP = 60  # seconden tussen twee nabijheids-verversingen
