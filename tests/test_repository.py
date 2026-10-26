"""Bewaakt de HACS-/hassfest-vereisten van de repository (zonder Home Assistant te starten)."""
import json
import re
import struct
from pathlib import Path

import pytest

from custom_components.ovapi_bustijden.const import DOMAIN, USER_AGENT

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / "custom_components"
INTEGRATION = COMPONENTS / DOMAIN

# Sleutels die hacs.json volgens de HACS-documentatie mag bevatten
HACS_KEYS = {
    "name", "content_in_root", "zip_release", "filename", "hide_default_branch",
    "country", "homeassistant", "hacs", "persistent_directory",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_exactly_one_integration_in_repo():
    dirs = [p for p in COMPONENTS.iterdir() if p.is_dir() and p.name != "__pycache__"]
    assert [p.name for p in dirs] == [DOMAIN]


def test_manifest_required_keys():
    manifest = _load(INTEGRATION / "manifest.json")
    for key in ("domain", "documentation", "issue_tracker", "codeowners", "name", "version"):
        assert manifest.get(key), f"manifest.json mist '{key}'"
    assert manifest["domain"] == DOMAIN
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["codeowners"] and all(c.startswith("@") for c in manifest["codeowners"])
    assert re.fullmatch(r"https://github\.com/[^/]+/[^/]+", manifest["documentation"])
    assert re.fullmatch(r"https://github\.com/[^/]+/[^/]+/issues", manifest["issue_tracker"])
    assert "REPLACE_ME" not in json.dumps(manifest)


def test_manifest_keys_sorted_like_hassfest():
    keys = list(_load(INTEGRATION / "manifest.json"))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_brand_icon_present_and_square():
    icon = INTEGRATION / "brand" / "icon.png"
    data = icon.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert width == height


def test_hacs_json():
    hacs = _load(ROOT / "hacs.json")
    assert hacs.get("name")
    assert set(hacs) <= HACS_KEYS, f"onbekende sleutels: {set(hacs) - HACS_KEYS}"
    # de dienst is Nederlands: HACS vraagt dan om 'country'
    assert hacs.get("country") == "NL"


@pytest.mark.parametrize("name", ["README.md", "LICENSE", "hacs.json"])
def test_root_files_exist(name):
    assert (ROOT / name).is_file()


def test_no_cache_or_build_artifacts_in_integration():
    for bad in (".ruff_cache", ".pytest_cache", ".mypy_cache"):
        assert not (INTEGRATION / bad).exists()


def test_user_agent_matches_manifest_version():
    version = _load(INTEGRATION / "manifest.json")["version"]
    assert f"/{version} " in USER_AGENT


def _keys(data: dict, prefix: str = ""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _keys(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}"


def test_translations_in_sync():
    en = set(_keys(_load(INTEGRATION / "translations" / "en.json")))
    nl = set(_keys(_load(INTEGRATION / "translations" / "nl.json")))
    assert en == nl


def test_unknown_stop_marker_present():
    from PIL import Image  # noqa: PLC0415
    im = Image.open(INTEGRATION / "static" / "halte_onbekend.png")
    assert im.size == (256, 256)


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{[a-z_0-9]+\}", text))


def _leaves(data: dict, prefix: str = ""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _leaves(value, f"{prefix}{key}.")
        elif isinstance(value, str):
            yield f"{prefix}{key}", value


def test_translation_placeholders_match():
    en = dict(_leaves(_load(INTEGRATION / "translations" / "en.json")))
    nl = dict(_leaves(_load(INTEGRATION / "translations" / "nl.json")))
    for key, text in en.items():
        assert _placeholders(text) == _placeholders(nl[key]), key


def test_manifest_import_executor_and_hacs_minimum():
    assert _load(INTEGRATION / "manifest.json").get("import_executor") is True
    assert _load(ROOT / "hacs.json")["homeassistant"] == "2026.2.0"


def test_services_use_entity_target_only():
    text = (INTEGRATION / "services.yaml").read_text(encoding="utf-8")
    assert "refresh:" in text
    assert "device:" not in text  # hassfest rejects device filters on targets


def test_no_deprecated_calls_or_removed_features():
    sources = "\n".join(p.read_text(encoding="utf-8") for p in INTEGRATION.glob("*.py"))
    for banned in (
        "helpers.service import async_extract_referenced_entity_ids",
        "remove_config_entry_id=",
        "DEVICE_TRACKER",
        "location_name",
    ):
        assert banned not in sources, banned


def test_no_personal_data_in_repo():
    needles = ("52.0907", "5.1214")
    for path in [*INTEGRATION.glob("*.py"), ROOT / "README.md", ROOT / "CHANGELOG.md"]:
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name}: {needle}"
