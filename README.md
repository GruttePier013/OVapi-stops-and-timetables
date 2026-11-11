<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/ovapi_bus_departures/brand/dark_logo@2x.png">
    <img src="custom_components/ovapi_bus_departures/brand/logo@2x.png" width="280" alt="OVapi bus departures">
  </picture>
</p>

# OVapi bus departures

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GruttePier013&repository=OVapi-stops-and-timetables&category=integration)

Live departure times of Dutch buses (optionally trams, metros and ferries) in Home Assistant, via [OVapi](https://v0.ovapi.nl). The interface is English with a complete Dutch translation.

## Features

- **Stops on the map:** each stop is a marker (`Stop on map` sensor) with the next bus as its state; the marker changes when data is stale.
- **Search stops** by place or street on a map, one stop per direction of travel.
- **Per stop:** departures with delay and line, next departure, lines, optional sensor per line, live (driving) indicator.
- **Smart refreshing:** nothing is polled by default; refresh by button, schedule, or automatically when a person enters the stop's zone.
- **Events** for cancelled, delayed and live buses, plus the actions `refresh` and `get_departures`.
- **Error protection:** backs off and pauses by itself instead of hammering OVapi.

## Install

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/GruttePier013/OVapi-stops-and-timetables` as **Integration**.
2. Download **OVapi bus departures** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *OVapi bus departures*.

Manual: copy `custom_components/ovapi_bus_departures` to `config/custom_components/`.

> Upgrading from 0.9.x (`ovapi_bustijden`)? Remove the old integration and add your stops again; entity ids and attributes are now English.

## Adding stops

1. Choose **Search stops** (or *Enter TPC codes* if you know the OVapi code).
2. Type a place (`Voorbeeldstad`) or street + place and press Enter. Move the pin and resize the circle (50-5000 m). Optionally include tram, metro or ferry.
3. Tick the stops. The first time the stop file from [NDOV Loket](https://data.ndovloket.nl/haltes/) is downloaded (about 13 MB). At most 25 directions are listed; reduce the radius to see the rest.
4. Per stop: a name, the lines you want (empty = all) and optionally only certain destinations.

Change anything later under **Configure** (search, edit, remove, settings).

## Stop on the map

The *Stop on map* sensor has a position and a logo, so it shows as a marker:

| Marker | When |
|---|---|
| <img src="custom_components/ovapi_bus_departures/static/stop_marker.png" width="48" alt="Stop marker"> | current bus data available |
| <img src="custom_components/ovapi_bus_departures/static/stop_unknown.png" width="48" alt="Stop with question mark"> | *Refresh data*: stale or no bus left; press *Refresh* |

Add it to a map card in YAML (the visual editor only lists persons and zones):

```yaml
type: map
entities:
  - sensor.ovapi_example_town_market
default_zoom: 16
```

To move the marker: Configure → stops → edit, drag the pin (tick *Reset marker* to undo). Only the marker moves; distances and zones keep using the real position. The sidebar **Map** panel only shows persons, zones and trackers, so use a map card.

## Entities per stop

| Entity | Content |
|---|---|
| Departures | `14:05 (Line 74)`, `No departures` or `Refresh data`; attribute `departures` lists every upcoming bus |
| Next departure | timestamp of the next bus (attributes: line, destination, delay, `live`) |
| Lines / Line 34, … | lines in the data; next departure per line (optional) |
| Live departure | on while a bus is really driving (`DRIVING`/`ARRIVED`) |
| Departure events | event entity: `cancelled`, `delayed`, `live` |
| Refresh | button |

Per-stop settings (switches and selects on the device): refresh frequency (default *Never*), refresh when in zone, only when approaching, zone radius (default 500 m), refresh interval in zone (default 5 min) and stop after (default 30 min). Diagnostics: Paused, Consecutive errors, Distance, Updated, TPC code and more. The **OVapi** device has *Refresh all stops*.

Minutes until departure count down by themselves; departed buses disappear. *Only live departures count as next departure* (Configure → Settings) ignores timetable-only buses.

## Refreshing

```yaml
action: ovapi_bus_departures.refresh
target:
  entity_id: all      # or specific stops, devices or areas
```

A stop ignores a second manual refresh within 10 seconds. **Zone visit:** one refresh on entry, then the chosen interval until the stop time; persons sharing a visit count once, and which persons count is set under Configure → Settings.

## Action: get_departures

Returns upcoming departures from the stops' current data (no OVapi call; refresh first if needed) for a stop (device or entity), an area, `entity_id: all`, or all stops inside a `zone`.

```yaml
action: ovapi_bus_departures.get_departures
target:
  device_id: 0123456789abcdef
data:
  zone: zone.home       # optional
  line: "74, 34"        # optional
  limit: 5              # 1-100, default 10
  live_only: false      # optional
response_variable: bus
```

The response has `departures` (sorted by time) and `stops`; a departure has `stop`, `line`, `destination`, `time`, `planned`, `expected`, `minutes_until`, `delay_min`, `status` and `live`.

## Events

`event.ovapi_<stop>_departure_events` fires after a refresh shows a change:

| Type | When |
|---|---|
| `cancelled` | the journey is now cancelled |
| `delayed` | the delay grew by at least the set minutes (Configure → Settings, default 5) |
| `live` | the journey got a driving bus |

Attributes: `line`, `destination`, `planned`, `time`, `delay_min`, `minutes_until`, `status`.

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

## Examples

**Markdown card** with the next five departures:

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

**Blueprint "bus leaves soon"** (notification when the next bus is within N minutes):
[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FGruttePier013%2FOVapi-stops-and-timetables%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fovapi_bus_departures%2Fbus_leaves_soon.yaml)
Or copy `blueprints/automation/ovapi_bus_departures/bus_leaves_soon.yaml` to `config/blueprints/automation/ovapi_bus_departures/`. Combine it with a refresh frequency or zone visit, because a stop only updates when it refreshes.

## Good to know

- After 5 consecutive errors a stop pauses (repair issue; *Refresh* resumes it). After 10 errors or HTTP 429 all automatic calls pause temporarily. Thresholds: Configure → Settings → *Error protection*. Failed refreshes keep the last data and set `last_error`.
- A stop with no departures at all for 7 days gets a repair issue (the code may no longer exist).
- Train stations have no realtime data at OVapi and are not supported.
- Realtime departures are not stored in the recorder database.
- Problems? Download the **diagnostics** (locations are redacted) and open an [issue](https://github.com/GruttePier013/OVapi-stops-and-timetables/issues).

Sources: [OVapi](https://v0.ovapi.nl), [NDOV Loket](https://data.ndovloket.nl/haltes/), [PDOK](https://www.pdok.nl). Changes: [CHANGELOG.md](CHANGELOG.md). License: MIT.
