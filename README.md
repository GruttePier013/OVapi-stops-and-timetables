<p align="center"><img src="custom_components/ovapi_bus_departures/brand/logo@2x.png" width="220" alt="OVapi bus times"></p>

# OVapi busstops departures timetable

[![HACS Validate](https://github.com/GruttePier013/OVapi-stops-and-timetables/actions/workflows/validate.yml/badge.svg)](https://github.com/GruttePier013/OVapi-stops-and-timetables/actions/workflows/validate.yml)
[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GruttePier013&repository=OVapi-stops-and-timetables&category=integration)

Live departure times of buses (and optionally trams, metros and ferries) in the Netherlands in Home Assistant, via [OVapi](https://v0.ovapi.nl). You search for stops on a map, pick a stop per direction of travel, and get sensors per stop, a map marker and settings for when to refresh. The interface is English, with a complete Dutch translation (Nederlandse vertaling).

> **Upgrading from 0.9.x (`ovapi_bustijden`)?** The integration was renamed to `ovapi_bus_departures`. Home Assistant treats it as a new integration: remove the old one, install this version and add your stops again. Entity ids, attribute names and states are now English.

## Install

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/GruttePier013/OVapi-stops-and-timetables` as **Integration**.
2. Download **OVapi bus times** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *OVapi bus times*.

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
    - sensor.example_town_market_departures
```

Without a `target` all stops are refreshed.

**Zone visit:** one refresh on entry (at least 60 s after the previous one), then the chosen interval until the stop time. A new visit only starts after the cool-down (default 15 min with nobody in the zone). Several persons share one visit. Which persons count is set under Configure → Settings (empty = all).

**Defaults:** on first install they are on one screen, later under Configure → Settings. *Apply to existing stops* (off by default) also sets them on existing stops.

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
  - sensor.example_town_market
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
