"""Packaging consistency.

Most of these guard the same class of mistake: something declared in one place
and forgotten in another. Two of them have already caught real bugs -- a
missing entity translation silently collapses an entity's name to its device's,
so two entities in one room end up as `switch.kitchen` and `switch.kitchen_2`
and every automation referring to them breaks.
"""

from __future__ import annotations

import json
import pathlib
import re

import yaml

COMPONENT = pathlib.Path(__file__).parents[2] / "custom_components" / "better_lighting"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
SERVICES_YAML = yaml.safe_load((COMPONENT / "services.yaml").read_text())
MANIFEST = json.loads((COMPONENT / "manifest.json").read_text())


def test_translations_match_strings() -> None:
    """Custom integrations load translations/, not strings.json."""
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert english == STRINGS


def test_every_service_is_declared_everywhere() -> None:
    source = (COMPONENT / "services.py").read_text()
    registered = set(re.findall(r'^SERVICE_\w+ = "(\w+)"', source, re.M))
    assert registered
    assert registered == set(SERVICES_YAML), "services.yaml is out of step"
    assert registered == set(STRINGS["services"]), "strings.json is out of step"


def test_every_service_field_is_described() -> None:
    for name, schema in SERVICES_YAML.items():
        described = set(STRINGS["services"][name].get("fields", {}))
        declared = set(schema.get("fields") or {})
        assert declared <= described, f"{name} has undescribed fields"


def test_every_subentry_type_has_strings() -> None:
    source = (COMPONENT / "config_flow.py").read_text()
    used = set(re.findall(r"SubentryType\.(\w+)\.value: \w+SubentryFlow", source))
    declared = {name.upper() for name in STRINGS["config_subentries"]}
    assert used == declared


def test_every_entity_translation_key_has_a_name() -> None:
    """A missing one collapses the entity's name onto its device's."""
    declared = {
        f"{platform}.{key}"
        for platform, entries in STRINGS["entity"].items()
        for key in entries
    }
    for path in COMPONENT.glob("*.py"):
        source = path.read_text()
        for key in re.findall(r'_attr_translation_key = "(\w+)"', source):
            platform = path.stem
            assert f"{platform}.{key}" in declared, (
                f"{path.name} uses translation key {key!r} with no name in strings.json"
            )


def test_every_translated_selector_has_options() -> None:
    """A select whose options are not translated shows raw slugs.

    Free-text selects are exempt: their values are the device's own vocabulary
    rather than ours, so they deliberately carry no translation key.
    """
    source = (COMPONENT / "const.py").read_text()
    used = {
        match.group(2)
        for match in re.finditer(r"_select\(\s*([^,]+),\s*\"(\w+)\"([^)]*)\)", source)
        if "custom=True" not in match.group(3)
    }
    declared = set(STRINGS["selector"])
    assert used <= declared, f"untranslated selectors: {sorted(used - declared)}"


def test_manifest_is_complete() -> None:
    for key in (
        "domain",
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
        "config_flow",
        "iot_class",
        "integration_type",
    ):
        assert MANIFEST.get(key), f"manifest is missing {key}"
    assert MANIFEST["domain"] == "better_lighting"
    # One hub entry owns everything, so Home Assistant should hide "add entry".
    assert MANIFEST["single_config_entry"] is True


def test_hacs_declares_the_supported_floor() -> None:
    hacs = json.loads((COMPONENT.parents[1] / "hacs.json").read_text())
    assert hacs["homeassistant"] == "2026.1.0"


def test_repair_issues_are_described() -> None:
    source = (COMPONENT / "repairs.py").read_text()
    used = set(re.findall(r'^ISSUE_\w+ = "(\w+)"', source, re.M))
    assert used == set(STRINGS["issues"])
