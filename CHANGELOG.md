# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0]
### Changed (breaking)
- **The integration is now English** and was renamed: folder and domain `ovapi_bustijden` -> `ovapi_bus_departures`, name *OVapi bus departures*. Home Assistant treats a new domain as a new integration and cannot migrate config entries, entities or history across domains: remove the old integration and add your stops again.
- The Dutch name stays *OVapi bussen en halteinformatie* (translation title). The action is `ovapi_bus_departures.refresh`; the empty state is `No departures` (`Geen bussen` in Dutch).
- Dutch is now provided through translations (`translations/nl.json`): entity names, states (`No departures`/`Geen bussen`, `Refresh data`/`Data vernieuwen`), explanations and the stop selection texts follow the Home Assistant language.
- Entity names are translated instead of forced Dutch, so entity ids follow the language at creation (for example `sensor.<stop>_departures` in English).
- All attribute names are English: `departures`, `time`, `planned`, `minutes_until`, `delay_min`, `line`, `destination`, `destinations`, `line_name`, `direction`, `updated`, `data_age_min`, `messages`, `sources`, `line_filter`, `partial`, `missing_codes`, `failed_attempts`, `last_error`, `lines`, `count`, `persons`, `person_entities`, `nearest_m`, `upcoming`, `fetched`, `passes_in_response`, `cancelled`, `sources_ok`, `sources_total`, `explanation`, and so on.
- The map images are `stop_marker.png` and `stop_unknown.png`; the device name uses `→` for the direction (`Voorbeeldstad, Markt → Station`) and `Stop <code>` as the fallback name.
- The old `refresh_stops` action alias was removed; use `ovapi_bus_departures.refresh`.
- README is English.

## [0.9.2]
### Added
- Setting *Update minutes-until-departure every minute* (default on). Off: entities only update on a refresh or when a bus leaves (fewer state writes).
- When the last bus has left, the stop refreshes once (daytime 06:00-22:00 only, once per transition, honouring backoff and pause).
- Attribute `ontbrekende_codes` on *Vertrektijden* when OVapi returns nothing for a TPC code.

### Changed
- The stop list in the config flow shows a bold warning (⚠️) at the top when it was cut off at 25 directions; the list is still shown.
- Dynamic texts of the stop selection (departure headers, counts, distance from home, "unknown") now come from the translations (NL/EN) instead of being hard-coded Dutch.
- Attribute names harmonised to Dutch: *Afstand tot persoon* uses `persoon`, `in_zone`, `naderend`; *Opeenvolgende fouten* uses `gepauzeerd`, `max_fouten`, `laatste_fout`, `api_opeenvolgende_fouten`, `api_gepauzeerd_s`; *Gepauzeerd* uses `opeenvolgende_fouten`, `max_fouten`, `api_gepauzeerd_s`. Update templates that used the old English names.
- Automatic refreshes of the same stop are at least 60 s apart (manual refreshes always work).
- The first refresh at startup now honours pacing and the circuit breaker; reloading resets the pacing.
- Checking TPC codes in the config flow is bundled into combined requests (max 40 codes) and single-code requests run at most 5 at a time.
- Person updates that only change attributes (battery, accuracy) or move less than 5 m no longer re-evaluate the stops; untracked persons are ignored and stale reference data is cleaned up.
- The departure list is cached per minute and departed buses are pruned from memory every minute; the number of fetched departures stays in `opgehaald`.
- The raw OVapi response is no longer kept in memory; diagnostics fetch it when downloaded.
- Error texts and log lines are English.

## [0.9.1]
### Added
- Per stop (edit stop) you can move the marker on the HA map with a map picker. Tick *Reset marker* and submit to reset it. Only the marker moves; distances and zones keep using the real stop position.

### Changed
- *Vertrektijden* and the per-line sensors now always show all fetched departures. The *Departures in the attribute* setting (default 6) is removed, and the per-line `volgende` attribute lists every further departure with planned time, delay and status (was 3).
- README reorganised: separate sections for entities, settings, refreshing and error protection, plus a table with the two stop markers (normal and *Data vernieuwen*).

## [0.9.0]
All improvements from the EnBW Laadpalen integration in one release.

### Added
- **Error protection** (section in Settings): per-stop exponential backoff (60-900 s); pause after 5 consecutive errors with a repair issue and a *Gepauzeerd* diagnostic entity; API circuit breaker (10 errors or HTTP 429 with `Retry-After`, 60-1800 s, `api_paused` issue); minimum 3 s spacing between automatic calls; startup limited to 5 concurrent stops. Manual refresh always works.
- **Smarter zone visits:** one refresh on entry (at least 60 s after the previous), then the chosen interval; stop after 10/30/60/120 min or unlimited; cool-down (default 15 min); *Alleen vernieuwen bij naderen* switch (25 m closer); *Afstand tot persoon* sensor.
- **Defaults** for new stops (one screen on first install, later in Settings) with *Apply to existing stops* (off by default).
- Action `ovapi_bustijden.refresh` with a target picker and an *Alle haltes vernieuwen* button; `refresh_stops` stays as an alias.
- Diagnostics (also per device), *Opeenvolgende fouten* diagnostic entity, repair issues cleaned up on unload.
- `async_remove_config_entry_device`: removing a stop device also removes it from the configuration.
- Tests for translation placeholders, manifest/hacs.json, services and banned calls; mypy in CI.

### Changed
- Minimum Home Assistant 2026.2.0 (`hacs.json`); `import_executor` enabled; no deprecated calls; mypy clean.
- Informational attributes are excluded from the recorder (`MATCH_ALL`); stale data becomes *unknown* and is updated every minute.
- *Halte op kaart* is a sensor with `latitude`/`longitude` and `entity_picture`, without `location_name`. The *map logo* option stays.
- `services.yaml` uses only an entity filter (hassfest rejects device filters).
- Code, comments and docstrings are English; user-facing text stays Dutch.

### Migration
- The zone settings (interval and stop time) changed from numbers to selects; old values are converted to the nearest option. entity_ids and unique_ids are unchanged.
- Any leftover `device_tracker` of *Halte op kaart* (a sensor since 0.8) is removed.

### Not tested
Real map rendering (including the side-menu Map panel), real OVapi/network behaviour, and HACS/GitHub.

## [0.8.3]
- The OVapi certificate notice (`v0.ovapi.nl`, hostname mismatch) is logged at *info*; verification for that host is still disabled automatically, with a retry after 24 h.
- Repository moved to `GruttePier013/OVapi-stops-and-timetables`; README shortened for Home Assistant users.
- All examples are generic (no real places, streets or TPC codes).
- *Plaatsnaam* is now an address search (PDOK): a place centres the map (2 km), street + place puts the pin on the street (500 m), an address or postcode on that point (250 m). The *Address* and *Coordinates* fields were removed.
- Distance in the stop list is the distance from home in km (`0,7 km lopen van huis`); the *Afstand tot huis* sensor is calculated live.
- *Direction in device name* is on by default.

## [0.8.2]
- Added: *Plaatsnaam* field above the map, a separate *Extra opties* step, recorder-exclude hint in the README.
- *Halte op kaart* is named after the device and is a diagnostic; *Opgehaalde vertrektijden* counts departures still in the future.
- Line sensors on by default; *Vernieuwen als in zone* (off by default for stops in the home zone); new logo and stop markers.

## [0.8.1]
- Added: optional per-line sensors, tram/metro/ferry support, destination filter, configurable zone interval, reconfigure step, diagnostics, `icons.json`, repair issue for an NDOV file older than 90 days.
- Robustness: last data stays visible after a failed refresh, batched OVapi requests, retries, non-blocking first refresh, one minute timer per stop, SSL verification with fallback.
- Fixed: loading failed on newer Home Assistant versions (`async_extract_referenced_entity_ids` removed from `helpers.service`).

## [0.8.0]
- Renamed to **OVapi bussen en halteinformatie** (domain unchanged).
- Added: action `refresh_stops`, *stop after* setting, configurable stale-data timeout (default 12 h), *Data vernieuwen* state and unknown-stop marker, new logos.
- **Breaking:** *Halte op kaart* became a sensor (was a device_tracker); the old tracker is removed automatically. Update dashboards.

## [0.7.0]
- Zone refresh repeats every 2 minutes while a person is in the zone; optional direction in the device name; one checkbox per stop in the results step.
- *Personen nabij* shows `Geen` or names; the on/off *Persoon nabij* entity was removed.

## [0.6.x]
- 0.6.2: the re-download tick shows the NDOV file age; dynamic text is always Dutch.
- 0.6.1: a place name `unknown` from OVapi is ignored in default stop names.
- 0.6.0: *Lijnen* and *Personen nabij* sensors, map logo, NDOV file age tracking, new result list, codeowner @GruttePier013.

## [0.5.0]
- Stop source is now the NDOV Loket stop file (CHB); a quay (one direction) becomes one device and TPC codes are verified live.
- Three-phase config flow with a percentage progress ring; checkbox list right after choosing a zone.

## [0.4.0]
- Draggable search circle on the map picker; stops added per direction; OpenStreetMap tiles only.

## [0.3.0]
- GitHub workflows (HACS, hassfest, ruff, pytest), `hacs.json` with `country: NL`, sorted `manifest.json`, repository tests.

## [0.2.0]
- Correct OVapi endpoint, timezone fix, multiple TPC codes per stop with line filter, refresh schedule per stop, proximity, map in the config flow, NL/EN translations.
