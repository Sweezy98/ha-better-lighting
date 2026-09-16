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


def test_german_covers_exactly_the_same_strings() -> None:
    """A translation with holes in it is worse than none at all.

    Home Assistant falls back to English per missing key, so a partial file
    produces a form that is half one language and half the other.
    """
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    german = json.loads((COMPONENT / "translations" / "de.json").read_text())

    def flatten(node, prefix=""):
        if isinstance(node, dict):
            merged = {}
            for key, value in node.items():
                merged |= flatten(value, f"{prefix}.{key}")
            return merged
        return {prefix: node}

    flat_en, flat_de = flatten(english), flatten(german)
    assert set(flat_en) == set(flat_de)

    # Placeholders are substituted by Home Assistant, so losing one in
    # translation produces a message with a hole in it.
    placeholder = re.compile(r"\{[a-z_]+\}")
    for key, value in flat_en.items():
        assert set(placeholder.findall(value)) == set(
            placeholder.findall(flat_de[key])
        ), f"{key} lost or gained a placeholder in translation"


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


def test_every_selector_wraps_its_values_in_options() -> None:
    """hassfest rejects a selector block that is not {"options": {...}}.

    Checking the key existed was not enough: a selector whose values sat at the
    top level passed that and failed in CI, which is a poor division of labour.
    """
    for name, block in STRINGS["selector"].items():
        assert set(block) == {"options"}, (
            f"selector.{name} must hold exactly an 'options' mapping, got {sorted(block)}"
        )
        # Empty is legitimate for a select whose choices only exist at
        # runtime -- which scenes are defined, which rooms -- since there is
        # nothing fixed to label.
        assert isinstance(block["options"], dict), (
            f"selector.{name}.options must be a mapping"
        )
        assert all(isinstance(value, str) for value in block["options"].values()), (
            f"selector.{name}.options must map each value to a label"
        )


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


def test_the_panel_javascript_ships_with_the_integration() -> None:
    """HACS copies files and runs no build, so the panel must be plain JS."""
    panel = COMPONENT / "www" / "better_lighting_panel.js"
    assert panel.is_file(), "panel script missing from the integration"
    source = panel.read_text()
    assert 'customElements.define("better-lighting-panel"' in source
    # Nothing that would need bundling or fetching at runtime.
    assert "import " not in source.split("*/")[-1], "panel must have no imports"


def test_every_menu_option_has_a_step_behind_it() -> None:
    """A menu entry naming a step that does not exist is a dead end."""
    source = (COMPONENT / "config_flow.py").read_text()
    defined = set(re.findall(r"async def async_step_(\w+)\(", source))
    # These are Home Assistant's own, not ours to define.
    defined |= {"finish", "done"}

    for area in ("config_subentries", "options"):
        block = STRINGS.get(area, {})
        groups = block.values() if area == "config_subentries" else [block]
        for group in groups:
            for step_id, step in (group.get("step") or {}).items():
                for option in step.get("menu_options") or {}:
                    assert option in defined, (
                        f"{area}.{step_id} offers {option!r}, which has no step"
                    )


def test_every_panel_field_has_a_label() -> None:
    """A field the panel cannot name shows a raw slug like `down_press_action`."""
    from custom_components.better_lighting import panel_schema

    schema = panel_schema.schema("en")
    labels = schema["labels"]["data"]
    unlabelled = sorted(
        {
            field["key"]
            for form in schema["forms"].values()
            for group in form
            for field in group["fields"]
            if field["key"] not in labels
        }
    )
    assert not unlabelled, f"panel fields with no label: {unlabelled}"


def test_the_panel_translates_as_completely_as_the_forms() -> None:
    """German is generated from the same files, so it must be as complete."""
    from custom_components.better_lighting import panel_schema

    english = panel_schema.schema("en")["labels"]
    german = panel_schema.schema("de")["labels"]
    assert set(german["data"]) == set(english["data"])
    assert set(german["options"]) == set(english["options"])


def test_the_panel_only_calls_commands_that_exist() -> None:
    """A typo in either half is a button that silently does nothing."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    backend = (COMPONENT / "panel.py").read_text()

    called = set(re.findall(r'_call\("([a-z_]+)"', panel_js))
    registered = set(re.findall(r'f"\{DOMAIN\}/([a-z_]+)"', backend))
    assert called <= registered, (
        f"panel calls unknown commands: {sorted(called - registered)}"
    )
