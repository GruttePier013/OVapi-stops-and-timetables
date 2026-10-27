"""Constants for OVapi bussen en halteinformatie."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ovapi_bustijden"
NAME = "OVapi bussen en halteinformatie"
PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
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
CONF_MARKER = "marker"  # map location override (selector)
CONF_MARKER_RESET = "marker_reset"
CONF_PERSONS = "persons"
CONF_MAP_LOGO = "map_logo"
CONF_DIRECTION_IN_NAME = "direction_in_name"
CONF_STALE_HOURS = "stale_hours"
CONF_MODES = "modes"
CONF_PLACE = "place"
PLACE_RADIUS = 2000  # m: search radius after looking up a place name (the map zooms out wide)
STREET_RADIUS = 500  # m: straat + plaats
ADDRESS_RADIUS = 250  # m: huisadres of postcode
CONF_LINE_SENSORS = "line_sensors"
DEFAULT_LINE_SENSORS = True
CONF_DESTINATIONS = "destinations"
MODES = ("bus", "tram", "metro", "boat")  # transport modes with realtime data at OVapi
DEFAULT_MODES = ["bus"]
DEFAULT_STALE_HOURS = 12
SERVICE_REFRESH = "refresh"
SERVICE_REFRESH_STOPS = "refresh_stops"  # old name of the action, kept as an alias
CONF_REFRESH_NDOV = "refresh_ndov"

DEFAULT_RADIUS = 250
GRACE_SECONDS = 30  # bus blijft 30 s na vertrek zichtbaar

RETRY_DELAYS = (1, 3)  # seconds to wait before attempts 2 and 3 on transient OVapi errors
BATCH_CHUNK = 40  # TPC-codes per gecombineerd OVapi-verzoek
FIRST_REFRESH_TIMEOUT = 10  # seconds startup waits at most for the first refresh
NDOV_OUTDATED_DAYS = 90  # daarna een reparatiemelding: haltebestand opnieuw downloaden
INSECURE_RETRY_AFTER = 24 * 3600  # after an SSL error at OVapi, only retry with verification after 24 hours
REQUEST_TIMEOUT = 30  # grote stations geven grote antwoorden
# Stop file from NDOV Loket (CHB): the index is refreshed on use after 14 days
CHB_INDEX_MAX_AGE = 14 * 24 * 3600
PREVIEW_MAX_STOPS = 25  # maximum number of stops (directions) in the config flow list
CONF_COORDINATES = "coordinates"
CONF_EXTRA_LINES = "extra_lines"
# Entity names are always Dutch (the service is Dutch), regardless of the Home Assistant language
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
    "zone_refresh": "Vernieuwingsfrequentie in zone",
    "approach_only": "Alleen vernieuwen bij naderen",
    "distance_to_person": "Afstand tot persoon",
    "error_count": "Opeenvolgende fouten",
    "paused": "Gepauzeerd",
}
REFRESH_ALL_NAME = "Alle haltes vernieuwen"
USER_AGENT = "HomeAssistant-ovapi_bustijden/0.9.1 (custom integration; OVapi KV78Turbo client)"
STATIC_URL = "/ovapi_bustijden_static"  # public path for the map logo (entity_picture)
PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
# v0.ovapi.nl has a mismatching certificate (the YAML sensors also use verify_ssl: false)
OVAPI_BASE = "https://v0.ovapi.nl"

DEFAULT_PROXIMITY_RADIUS = 500  # meters
MIN_RADIUS, MAX_RADIUS = 50, 5000
DEFAULT_ZONE_COOLDOWN = 15  # minutes the zone must be empty before a new visit starts
MIN_APPROACH_REFRESH_GAP = 60  # seconds between two entry refreshes

# Defaults for new stops (Configure -> Settings) and error protection
CONF_DEFAULT_SCHEDULE = "default_schedule"
CONF_DEFAULT_RADIUS = "default_radius"
CONF_DEFAULT_REFRESH_ON_ZONE = "default_refresh_on_approach"
CONF_DEFAULT_ZONE_INTERVAL = "default_zone_interval"
CONF_DEFAULT_ZONE_STOP_AFTER = "default_zone_stop_after"
CONF_DEFAULT_APPROACH_ONLY = "default_approach_only"
CONF_APPLY_EXISTING = "apply_existing"
CONF_SECTION_DEFAULTS = "defaults"
CONF_SECTION_PROTECTION = "protection"
CONF_MAX_ERRORS = "max_errors"  # consecutive errors before a stop pauses automatic refreshes
CONF_API_MAX_ERRORS = "api_max_errors"  # consecutive API errors before all automatic refreshes pause
CONF_API_SPACING = "api_spacing"  # seconds between automatic API calls
CONF_ZONE_COOLDOWN = "zone_cooldown"  # minutes the zone must be empty before a new visit starts

DEFAULT_MAX_ERRORS = 5  # per stop
DEFAULT_API_MAX_ERRORS = 10  # across the whole API
DEFAULT_API_SPACING = 3.0  # seconds between automatic API calls
STOP_BACKOFF_BASE = 60  # seconds; doubles per consecutive error, capped below
STOP_BACKOFF_MAX = 900
API_BACKOFF_BASE = 60
API_BACKOFF_MAX = 1800
RATE_LIMIT_DEFAULT_WAIT = 300  # seconds to wait after HTTP 429 without Retry-After

# Approach detection
APPROACH_MIN_DELTA_M = 25  # the distance must shrink by this much to count as approaching
APPROACH_HOLD_MIN = 5  # minutes a person keeps counting as approaching after the last decrease
