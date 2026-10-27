"""Translated runtime texts (states and explanations) with English fallbacks; no Home Assistant imports."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import DOMAIN

DEFAULTS: dict[str, str] = {
    "no_buses": "No departures",
    "refresh_data": "Refresh data",
    "none": "None",
    "line_short": "Line {line}",
    "line_at": "Line {line} at {time}",
    "explain_refresh_on_approach": (
        "On: as soon as a followed person enters the zone radius of this stop, the departures are refreshed "
        "right away (then at the refresh frequency in zone). If the stop is inside the Home Assistant home zone "
        "this is off by default. Followed persons are set under Configure."
    ),
    "explain_approach_only": (
        "On: refreshes in the zone (after the first one on entry) only happen when someone got at least "
        "{meters} m closer since the previous refresh."
    ),
}


class Texts:
    """Look up ``component.<domain>.exceptions.<key>.message`` in the loaded translations."""

    def __init__(self, translations: Mapping[str, str] | None = None) -> None:
        self._translations = translations or {}

    def __call__(self, key: str, **values: Any) -> str:
        template = self._translations.get(f"component.{DOMAIN}.exceptions.{key}.message") or DEFAULTS.get(key, key)
        return template.format(**values)
