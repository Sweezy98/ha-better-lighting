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
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .const import (
    COLOR_PRESET_SPECS,
    CONDITION_SPECS,
    CONF_GROUP_GROUPS,
    CONF_ROOM_ID,
    CONF_RULE_STATES,
    CONF_SCENE_ORDER,
    CONF_ZONE_DETACHED_SCENE,
    CONTROLLER_SPECS,
    EFFECT_SPECS,
    EFFECT_STEP_SPECS,
    HUB_SPECS,
    LIGHT_GROUP_SPECS,
    LIGHT_PROFILE_SPECS,
    MODE_SPECS,
    ROOM_SCENE_SPECS,
    ROOM_SPECS,
    ROOM_ZONE_SPECS,
    TRIGGER_SPECS,
    FieldSpec,
    Section,
    mode_rule_specs,
)

_LOGGER = logging.getLogger(__name__)

# Anything before the first letter or digit of a label: the emoji the flow
# menus lead with, and the space after it.
_LEADING_SYMBOLS = re.compile(r"^[^\w]+", re.UNICODE)

# The order the panel shows a room's sections in: what it is, what it does by
# itself, then what it does about people and windows. Same grouping as the
# menu, because somebody who learned one should not have to learn the other.
ROOM_SECTIONS: tuple[Section, ...] = (
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
    Section.ADVANCED,
    # Last, because a second button is the last thing anybody configures and
    # most switches have none.
    Section.DOWN,
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
    """One field, as the panel needs it.

    Carries the selector twice over. ``selector`` is the config verbatim, in
    the shape Home Assistant's own ha-selector takes, so the panel can hand a
    field straight to the component that renders it everywhere else -- chips
    for a multi-select, its entity picker, its sliders. The flattened keys
    beside it drive the plain controls that stand in when that component
    cannot be had.
    """
    config = dict(getattr(spec.selector, "config", {}) or {})
    selector_type = getattr(type(spec.selector), "selector_type", None)
    field: dict[str, Any] = {
        "key": spec.key,
        "kind": _selector_kind(spec.selector),
        "default": spec.default,
        "required": spec.required,
        "section": spec.section.value,
    }
    if selector_type:
        field["selector"] = {selector_type: config}
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


def _plain(label: str) -> str:
    """A label with its decoration taken off.

    The flow menus lead with an emoji, which reads well in a list of buttons
    and badly in a breadcrumb trail -- and the panel draws its own icons
    beside these words anyway, so a second one in the text is just noise.
    """
    return _LEADING_SYMBOLS.sub("", label).strip()


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
    # A form's own section headers name several of these, and are the only
    # translated words for the ones no menu lists -- a switch's lower half
    # was showing "Down" in both languages because nothing read them.
    for step in _walk_steps(translations):
        if isinstance(step, dict):
            for name, block in (step.get("sections") or {}).items():
                if isinstance(block, dict) and (title := block.get("name")):
                    sections[name] = title
    # The menus already name each of these in the user's language; reuse those
    # rather than inventing a second set of words for the same things. Every
    # menu entry counts, not only the ones named after a Section -- scenes,
    # switches, calibration, rules and presets are screens without a Section
    # of their own, and filtering on the enum is what left them in English.
    for step in _walk_steps(translations):
        if isinstance(step, dict) and (menu := step.get("menu_options")):
            sections.update(menu)
    return {
        "data": data,
        "descriptions": described,
        "options": options,
        "sections": {name: _plain(title) for name, title in sections.items()},
    }


@lru_cache(maxsize=8)
def ui_strings(language: str) -> dict[str, str]:
    """The panel's own chrome, in the requested language.

    Kept apart from strings.json because these are not a config flow's
    strings and hassfest validates that file against a schema they do not
    fit -- an unknown key there is an error, not an extension point.
    """
    base = Path(__file__).parent / "panel_strings"
    english = json.loads((base / "en.json").read_text(encoding="utf-8"))
    for candidate in (language, language.split("-")[0]):
        path = base / f"{candidate}.json"
        if path.is_file():
            try:
                # English underneath, so a half-finished translation shows
                # the odd English word rather than a missing one.
                return {**english, **json.loads(path.read_text(encoding="utf-8"))}
            except ValueError:  # pragma: no cover - a corrupt file
                _LOGGER.warning("Could not read %s; falling back", path)
    return english


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


def _localise_options(
    forms: dict[str, Any], option_labels: dict[str, dict[str, str]]
) -> None:
    """Put our own words into the selectors we hand to Home Assistant.

    Its select renders a bare value unless the options carry labels, and it
    has no way to reach a custom integration's translations on its own -- so
    they are written in here, where they are already loaded.
    """
    for form in forms.values():
        for group in form:
            for field in group["fields"]:
                config = (field.get("selector") or {}).get("select")
                if not config:
                    continue
                labels = option_labels.get(config.get("translation_key") or "")
                if not labels:
                    continue
                config["options"] = [
                    {"value": value, "label": labels.get(value, value)}
                    for value in config.get("options") or ()
                ]


def schema(language: str = "en") -> dict[str, Any]:
    """Every form the panel can draw, plus the words to draw it with."""
    words = labels(language)
    forms = {
        "hub": _table(HUB_SPECS, HUB_SECTIONS),
        "zone": _table(ROOM_SPECS, ROOM_SECTIONS),
        "mode": _table(MODE_SPECS),
        # The room a switch is in is implicit now that it lives inside one,
        # and its running order is edited as a list rather than a field --
        # the same two omissions the settings screen makes.
        "switch": _table(
            tuple(
                spec
                for spec in CONTROLLER_SPECS
                if spec.key not in (CONF_ROOM_ID, CONF_SCENE_ORDER)
            ),
            SWITCH_SECTIONS,
        ),
        "calibration": _table(LIGHT_PROFILE_SPECS),
        # Not "zone": that is the room's own form, still keyed by the word
        # rooms were stored under before they were called rooms. A zone -- the
        # part of a room -- is "room_zone" here and nothing on disk.
        "room_zone": _runtime_choices(
            _table(ROOM_ZONE_SPECS),
            {CONF_ZONE_DETACHED_SCENE: "scenes"},
        ),
        # Which other groups a group may hold depends on the groups the room
        # has, so the picker is filled at runtime -- and filled without the
        # group being edited, which is the cheapest way to make the obvious
        # one-step loop unsayable.
        "light_group": _runtime_choices(
            _table(LIGHT_GROUP_SPECS),
            {CONF_GROUP_GROUPS: "light_groups"},
        ),
        "scene": _table(ROOM_SCENE_SPECS),
        "preset": _table(COLOR_PRESET_SPECS),
        "effect": _table(EFFECT_SPECS),
        # Both are edited inside the room or zone they belong to rather than
        # on a screen of their own, so these are the fields of one row.
        "condition": _table(CONDITION_SPECS),
        "trigger": _table(TRIGGER_SPECS),
        "effect_step": _table(EFFECT_STEP_SPECS),
        # Rules are built per mode, since their state picker depends on the
        # states that mode defines, and their scene picker on the room the
        # rule names. Both are marked as runtime choices so the panel fills
        # them from the mode and room in hand.
        "rule": _runtime_choices(
            _table(mode_rule_specs([])),
            {CONF_RULE_STATES: "mode_states"},
        ),
    }
    _localise_options(forms, words["options"])
    return {"ui": ui_strings(language), "labels": words, "forms": forms}
