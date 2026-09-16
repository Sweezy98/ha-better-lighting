"""Describe the ``FieldSpec`` tables to the panel, so it can render them.

Every form in the integration is already a table of :class:`FieldSpec`. The
config flow turns those into voluptuous schemas; this turns the same tables
into JSON the panel renders as controls. One description of each field, two
places it appears -- which is the only reason putting every setting on the
page is a day's work rather than a month's, and the only reason the two can
never disagree about what a field is.

Labels come from the integration's own translation files rather than from the
frontend's store: they are ours, they are on disk, and reading them here means
the panel is translated by the same files that translate the forms.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from .const import (
    COLOR_PRESET_SPECS,
    CONF_RULE_STATES,
    CONF_SCENE_ORDER,
    CONF_ZONE_ID,
    CONTROLLER_SPECS,
    HUB_SPECS,
    LIGHT_PROFILE_SPECS,
    MODE_SPECS,
    ZONE_SCENE_SPECS,
    ZONE_SPECS,
    FieldSpec,
    Section,
    mode_rule_specs,
)

_LOGGER = logging.getLogger(__name__)

# The order the panel shows a room's sections in: what it is, what it does by
# itself, then what it does about people and windows. Same grouping as the
# menu, because somebody who learned one should not have to learn the other.
ZONE_SECTIONS: tuple[Section, ...] = (
    Section.BASIC,
    Section.GROUP,
    Section.ADAPTIVE,
    Section.NIGHT,
    Section.POWER,
    Section.PRESENCE,
    Section.INSECT,
)
HUB_SECTIONS: tuple[Section, ...] = (Section.BASIC, Section.NIGHT, Section.ADVANCED)
SWITCH_SECTIONS: tuple[Section, ...] = (
    Section.BASIC,
    Section.DOWN,
    Section.ADVANCED,
)


def _selector_kind(widget: Any) -> str:
    """The control the panel should draw, as a plain word."""
    return (
        type(widget)
        .__name__.removesuffix("Selector")
        .replace("ColorRGB", "color")
        .lower()
    )


def describe(spec: FieldSpec) -> dict[str, Any]:
    """One field, as the panel needs it."""
    config = dict(getattr(spec.selector, "config", {}) or {})
    field: dict[str, Any] = {
        "key": spec.key,
        "kind": _selector_kind(spec.selector),
        "default": spec.default,
        "required": spec.required,
        "section": spec.section.value,
    }
    if spec.options_key:
        # Filled in by the panel from the room or hub it is editing.
        field["options_key"] = spec.options_key
    if spec.depends_on:
        field["depends_on"] = {
            "key": spec.depends_on[0],
            "values": list(spec.depends_on[1]),
        }

    for name in ("min", "max", "step", "unit_of_measurement", "mode", "multiple"):
        if name in config:
            field[name] = config[name]
    if options := config.get("options"):
        field["options"] = [
            option if isinstance(option, str) else option.get("value")
            for option in options
        ]
        field["translation_key"] = config.get("translation_key")
    if domains := config.get("domain"):
        field["domains"] = domains if isinstance(domains, list) else [domains]
    if config.get("custom_value"):
        field["custom_value"] = True
    return field


def _table(specs: tuple[FieldSpec, ...], sections: tuple[Section, ...] | None = None):
    wanted = sections or tuple(dict.fromkeys(spec.section for spec in specs))
    return [
        {
            "section": section.value,
            "fields": [describe(spec) for spec in specs if spec.section is section],
        }
        for section in wanted
        if any(spec.section is section for spec in specs)
    ]


@lru_cache(maxsize=8)
def _translations(language: str) -> dict[str, Any]:
    """This integration's own translations, or English when it has none."""
    base = Path(__file__).parent / "translations"
    for candidate in (language, language.split("-")[0], "en"):
        path = base / f"{candidate}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:  # pragma: no cover - a corrupt file, not a flow
                _LOGGER.warning("Could not read %s; falling back", path)
    return {}


def _walk_steps(block: Any):
    """Every step dict inside a translations block."""
    if not isinstance(block, dict):
        return
    for key, value in block.items():
        if key == "step" and isinstance(value, dict):
            yield from value.values()
        elif isinstance(value, dict):
            yield from _walk_steps(value)


def labels(language: str) -> dict[str, Any]:
    """Field labels, descriptions and option names, flattened by key.

    Flattened deliberately: a key means the same thing wherever it appears --
    ``night_behavior`` is the room's night behaviour on every screen that shows
    it -- so one lookup table serves every form and a field moved between
    screens keeps its label.
    """
    translations = _translations(language)
    data: dict[str, str] = {}
    described: dict[str, str] = {}

    def _absorb(block: Any) -> None:
        """Take one block's labels, then those of any sections inside it.

        Many keys live under ``sections.<name>.data`` rather than at the top
        of a step -- the whole of Advanced, most of a switch -- and reading
        only the top level left a third of the fields showing raw slugs.
        """
        if not isinstance(block, dict):
            return
        for key, label in (block.get("data") or {}).items():
            data.setdefault(key, label)
        for key, text in (block.get("data_description") or {}).items():
            described.setdefault(key, text)
        for section in (block.get("sections") or {}).values():
            _absorb(section)

    for step in _walk_steps(translations):
        _absorb(step)

    options = {
        name: dict(block.get("options") or {})
        for name, block in (translations.get("selector") or {}).items()
    }
    sections = {
        section.value: section.value.replace("_", " ").title() for section in Section
    }
    # The menu already names each section in the user's language; reuse those
    # rather than inventing a second set of words for the same things.
    for step in _walk_steps(translations):
        if isinstance(step, dict) and (menu := step.get("menu_options")):
            for key, label in menu.items():
                if key in sections:
                    sections[key] = label
    return {
        "data": data,
        "descriptions": described,
        "options": options,
        "sections": sections,
    }


def _runtime_choices(
    table: list[dict[str, Any]], keys: dict[str, str]
) -> list[dict[str, Any]]:
    """Mark fields whose choices only exist once something is selected."""
    for group in table:
        for field in group["fields"]:
            if field["key"] in keys:
                field["options_key"] = keys[field["key"]]
                field.pop("options", None)
    return table


def schema(language: str = "en") -> dict[str, Any]:
    """Every form the panel can draw, plus the words to draw it with."""
    return {
        "labels": labels(language),
        "forms": {
            "hub": _table(HUB_SPECS, HUB_SECTIONS),
            "zone": _table(ZONE_SPECS, ZONE_SECTIONS),
            "mode": _table(MODE_SPECS),
            # The room a switch is in is implicit now that it lives inside
            # one, and its running order is edited as a list rather than a
            # field -- same two omissions the settings screen makes.
            "switch": _table(
                tuple(
                    spec
                    for spec in CONTROLLER_SPECS
                    if spec.key not in (CONF_ZONE_ID, CONF_SCENE_ORDER)
                ),
                SWITCH_SECTIONS,
            ),
            "calibration": _table(LIGHT_PROFILE_SPECS),
            "scene": _table(ZONE_SCENE_SPECS),
            "preset": _table(COLOR_PRESET_SPECS),
            # Rules are built per mode, since their state picker depends on
            # the states that mode defines, and their scene picker on the room
            # the rule names. Both are marked as runtime choices so the panel
            # fills them from the mode and room in hand.
            "rule": _runtime_choices(
                _table(mode_rule_specs([])),
                {CONF_RULE_STATES: "mode_states"},
            ),
        },
    }
