<p align="center"><img src="custom_components/ovapi_bus_departures/brand/logo@2x.png" width="260" alt="OVapi bus departures"></p>

# OVapi bus departures

[![HACS Validate](https://github.com/GruttePier013/OVapi-stops-and-timetables/actions/workflows/validate.yml/badge.svg)](https://github.com/GruttePier013/OVapi-stops-and-timetables/actions/workflows/validate.yml)
[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GruttePier013&repository=OVapi-stops-and-timetables&category=integration)

Live departure times of buses (and optionally trams, metros and ferries) in the Netherlands in Home Assistant, via [OVapi](https://v0.ovapi.nl). You search for stops on a map, pick a stop per direction of travel, and get sensors per stop, a map marker and settings for when to refresh. The interface is English, with a complete Dutch translation (Nederlandse vertaling).

> **Upgrading from 0.9.x (`ovapi_bustijden`)?** The integration was renamed to `ovapi_bus_departures`. Home Assistant treats it as a new integration: remove the old one, install this version and add your stops again. Entity ids, attribute names and states are now English.

## Install

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/GruttePier013/OVapi-stops-and-timetables` as **Integration**.
2. Download **OVapi bus departures** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *OVapi bus departures*.

Manual install: copy `custom_components/ovapi_bus_departures` to `config/custom_components/` and restart.

## Adding stops

1. Choose **Search stops** (or *Enter TPC codes* if you already know the OVapi code).
2. Type a **place** (`Voorbeeldstad`) or **street + place** (`Voorbeeldlaan Voorbeeldstad`) and press Enter. Move the pin and resize the circle by dragging its edge (50-5000 m). You can also tick tram, metro or ferry.
3. Tick the stops. Every direction of travel is its own stop, with its distance from home and the current departures below it. The first time, the stop file from [NDOV Loket](https://data.ndovloket.nl/haltes/) is downloaded (about 13 MB, with progress in %). If more than 25 directions are found, a bold warning at the top says the list was cut off; reduce the radius to see the rest.
4. *Extra options*: direction in the device name (on by default), after how many hours data counts as stale (default 12), and re-downloading the stop file.
5. Per stop: name, the lines you want to see (empty = all) and optionally only certain destinations (for example `Station, Noord`).

Change anything later under **Configure** (search stops, edit, remove, settings).

## What you get per stop

**Main entities**

| Entity | Content |
|---|---|
| Departures | `14:05 (Line 74)`, `No departures` or `Refresh data`; the `departures` attribute holds time, line, destination, delay and minutes for **every** upcoming departure |
| Next departure | timestamp of the next bus |
| Lines | `34, 73, 350` (or `None`) |
| Line 34, Line 73, … | next departure per line (optional); `upcoming` lists all further departures |
| Stop on map | the map marker, with the next bus as its state |
| Persons nearby | the followed persons inside the stop's zone |
| Live departure | on while at least one upcoming bus is really driving (`DRIVING`/`ARRIVED`) |
| Departure events | event entity: `cancelled`, `delayed` and `live` (see [Events](#events)) |
| Refresh (button) | refresh right now |

**Settings per stop** (switches and selects on the device)

| Setting | Meaning |
|---|---|
| Refresh frequency | *Never* (default), daily, hourly, every 30 or 5 minutes, optionally only at rush hour |
| Refresh when in zone | refreshes as soon as a followed person enters the zone (off if the stop is inside your home zone) |
| Refresh only when approaching | after the first refresh, only refresh when someone got at least 25 m closer |
| Zone radius | radius of the zone around the stop (default 500 m) |
| Refresh frequency in zone | on entry, or every 2 / 5 / 10 / 15 min (default 5) |
| Stop refreshing in zone after | 10 / 30 / 60 / 120 min or unlimited (default 30) |

**Diagnostics:** Paused, Consecutive errors, Distance, Distance to person, Updated, Fetched departures, TPC code and stop code. The **OVapi** device has the **Refresh all stops** button.

The minutes until departure keep counting down every minute without a new request (can be switched off in the settings); departed buses disappear by themselves. When the last bus is gone, the stop refreshes once during the day (06:00-22:00).

## When does it refresh?

By default nothing is polled. You refresh with the button, with a refresh frequency, automatically when you enter a stop's zone, or with an action:

```yaml
action: ovapi_bus_departures.refresh
target:
  entity_id:
    - sensor.ovapi_example_town_market_departures   # a device or area also works
```

Without a `target` all stops are refreshed; `entity_id: all` does the same explicitly. A stop ignores a second manual refresh within 10 seconds, so an automation that accidentally calls it in a loop cannot hammer OVapi.

Every departure has a `live` flag: `true` when OVapi reports the bus as driving (`DRIVING`/`ARRIVED`), `false` when it is only a timetable time. With *Only live departures count as next departure* (Configure → Settings) the states ignore non-live departures.

**Zone visit:** one refresh on entry (at least 60 s after the previous one), then the chosen interval until the stop time. A new visit only starts after the cool-down (default 15 min with nobody in the zone). Several persons share one visit. Which persons count is set under Configure → Settings (empty = all).

**Defaults:** on first install they are on one screen, later under Configure → Settings. *Apply to existing stops* (off by default) also sets them on existing stops.

## Action: get_departures

`ovapi_bus_departures.get_departures` answers with the upcoming departures of one or more stops, for a specific stop (device or entity as target) or for all stops inside a zone. It does **not** call OVapi: it uses the data the stops already have, so refresh first if you need fresh data. A stop whose data is stale returns an empty list (`stale: true`).

| Field | Meaning |
|---|---|
| target | a stop (device or entity), an area, or `entity_id: all` |
| `zone` | a zone entity: every stop inside the zone (by its marker position and the zone radius) is added |
| `line` | only these lines (`"74, 34"` or a list) |
| `limit` | at most this many departures (1-100, default 10) |
| `live_only` | only departures with a driving bus |

```yaml
action: ovapi_bus_departures.get_departures
target:
  device_id: 0123456789abcdef
data:
  zone: zone.home
  line: "74, 34"
  limit: 5
response_variable: bus
```

The response has `departures` (all selected stops together, sorted by time) and `stops` (per stop: `stop`, `stop_key`, `fetched`, `stale`, `departures`). A departure has `stop`, `line`, `destination`, `time`, `planned`, `expected` (ISO timestamp), `minutes_until`, `delay_min`, `status` and `live`.

```yaml
- action: ovapi_bus_departures.get_departures
  data:
    zone: zone.home
    limit: 3
  response_variable: bus
- action: notify.mobile_app_phone
  data:
    message: >
      {% for d in bus.departures %}{{ d.time }} line {{ d.line }} to {{ d.destination }}{{ ", " if not loop.last }}{% endfor %}
```

## Events

Every stop has an event entity, `event.ovapi_<stop>_departure_events`. After each refresh the journeys are compared with the previous refresh; the first refresh and new journeys only set the baseline. Event types:

| Type | When |
|---|---|
| `cancelled` | OVapi now reports the journey as cancelled |
| `delayed` | the expected time moved at least *Delay that fires a 'delayed' event* (Configure → Settings, default 5 minutes) compared with the last reported time |
| `live` | the journey got a driving bus (`DRIVING`/`ARRIVED`) |

Attributes: `line`, `destination`, `planned`, `time`, `delay_min`, `minutes_until`, `status`. Events only follow refreshes, so they are as timely as your refresh settings.

```yaml
trigger:
  - trigger: state
    entity_id: event.ovapi_example_town_market_departure_events
condition:
  - "{{ trigger.to_state.attributes.event_type == 'cancelled' }}"
action:
  - action: notify.mobile_app_phone
    data:
      message: "Line {{ trigger.to_state.attributes.line }} of {{ trigger.to_state.attributes.planned }} is cancelled"
```

## Dashboard and automation examples

**Departures card** (Markdown card, shows the next five departures of the stop):

```yaml
type: markdown
title: Bus
content: >
  {% set d = state_attr('sensor.ovapi_example_town_market_departures', 'departures') or [] %}
  {% for x in d[:5] %}
  **{{ x.time }}**{% if x.delay_min > 0 %} (+{{ x.delay_min }}){% endif %} line {{ x.line }} to {{ x.destination }}{{ "" if x.live else " ⏲" }}
  {% else %}
  No departures
  {% endfor %}
```

(⏲ marks a timetable time without a driving bus.)

**Blueprint "bus leaves soon":** a notification once the next departure is at most N minutes away.
[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FGruttePier013%2FOVapi-stops-and-timetables%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fovapi_bus_departures%2Fbus_leaves_soon.yaml)
If the button does not work (other branch name), copy `blueprints/automation/ovapi_bus_departures/bus_leaves_soon.yaml` to `config/blueprints/automation/ovapi_bus_departures/`. Choose the *Next departure* sensor, the number of minutes and what to do; `{{ line }}`, `{{ destination }}` and `{{ minutes }}` are available in the action. Because a stop only knows the current time when it refreshes, combine it with a refresh frequency or a zone visit.

## Stops that stop working

When OVapi has returned no departure at all for a stop for 7 days (refreshes between 06:00 and 22:00), a repair issue says the stop code may no longer exist. It disappears by itself as soon as departures return, or when you remove the stop. If you change the TPC codes of a stop under Configure, its device and entities stay the same.

## Error protection

To avoid overloading OVapi:

- After each error a stop waits longer (60 s to 15 min). After 5 consecutive errors the stop pauses with a repair issue; the *Refresh* button always works and resumes the stop.
- After 10 errors in total, or on HTTP 429, all automatic calls pause temporarily (60 s to 30 min).
- Automatic calls keep at least 3 s apart, and the same stop never refreshes automatically more than once per 60 s.

Thresholds are set under Configure → Settings → *Error protection*. After a failed refresh the last data stays visible; the `failed_attempts` and `last_error` attributes tell you why.

## Stop on the map

The *Stop on map* sensor has `latitude`, `longitude` and a logo, so it shows as a marker on the map. The marker changes appearance:

| Marker | When |
|---|---|
| <img src="custom_components/ovapi_bus_departures/static/stop_marker.png" width="56" alt="Stop marker"> | normal: current bus data available |
| <img src="custom_components/ovapi_bus_departures/static/stop_unknown.png" width="56" alt="Stop with question mark"> | *Refresh data*: data is stale or no bus left; press *Refresh* |

Add the marker to a map card in YAML (the visual editor only lists persons and zones):

```yaml
type: map
entities:
  - sensor.ovapi_example_town_market
default_zoom: 16
```

**Moving the marker:** Configure → stops → edit, then drag the pin. Tick *Reset marker* and submit to reset it. Only the marker moves; distances and zones keep using the real stop position. Switch the logo off with *Use the logo on the HA map*.

**Note:** the **Map** panel in the sidebar only shows persons, zones and devices with a tracker; the sensor may not appear there. Use a map card on a dashboard instead. This has not been tested in a real Home Assistant.

## Sources and limitations

- Departures: [OVapi](https://v0.ovapi.nl) · stops: [NDOV Loket](https://data.ndovloket.nl/haltes/) · addresses: [PDOK](https://www.pdok.nl).
- Train stations have no realtime data at OVapi and are not supported.
- Realtime departures and informational attributes are not stored in the recorder database.
- Problems? Download the **diagnostics** (on the integration page; locations are redacted) and open an [issue](https://github.com/GruttePier013/OVapi-stops-and-timetables/issues).

Changes: see [CHANGELOG.md](CHANGELOG.md). License: MIT.
