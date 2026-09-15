"""Constants and the field-spec tables for Better Lighting.

Every configuration form in this integration is generated from the ``FieldSpec``
tables below, so a new option is added in exactly one place and the flow, the
defaults, the validation and the docs all follow.  This generalises Adaptive
Lighting's ``VALIDATION_TUPLES`` + side-car ``EXTRA_VALIDATION`` pair into a
single table that also carries the selector and the UI section.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homeassistant.components.light import VALID_TRANSITION
from homeassistant.helpers import selector

DOMAIN = "better_lighting"

# Sentinel for "unset" in a form. Options flows round-trip through JSON, which
# cannot carry `None` through a voluptuous schema cleanly, so Adaptive Lighting's
# string sentinel is reused.
NONE_STR = "None"

# Sentinel for a zone-level value that should fall back to the hub default.
INHERIT = "__inherit__"

PLATFORMS: list[str] = ["light"]


class SubentryType(StrEnum):
    """The five kinds of configuration object held by the hub entry."""

    ZONE = "zone"
    SCENE = "scene"
    CONTROLLER = "controller"
    LIGHT_PROFILE = "light_profile"
    MODE = "mode"


class Section(StrEnum):
    """UI grouping. Everything outside BASIC renders in a collapsed section."""

    BASIC = "basic"
    ADAPTIVE = "adaptive"
    NIGHT = "night"
    POWER = "power"
    PRESENCE = "presence"
    INSECT = "insect"
    GROUP = "group"
    ADVANCED = "advanced"


class BrightnessMode(StrEnum):
    """Which curve maps the sun's position onto a brightness percentage."""

    # Adaptive Lighting's original: flat at max all day, ramping only at night.
    SUN = "sun"
    # Smooth S-curve anchored on the nearest sunrise/sunset. Starts fading
    # before sunset, which is what most people actually want, so it is default.
    TANH = "tanh"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One configurable field: its default, its widget, and its validation.

    ``selector`` drives the form. ``validator`` runs *after* the form, for
    validation voluptuous cannot express in a selector (or that produces a
    non-JSON-serializable value, such as ``cv.time_period``); ``coerce`` turns
    such a value back into something storable.
    """

    key: str
    default: Any
    selector: Any
    validator: Any | None = None
    coerce: Callable[[Any], Any] | None = None
    section: Section = Section.BASIC
    required: bool = False
    # Only offered when another field holds one of these values.
    depends_on: tuple[str, tuple[Any, ...]] | None = None

    def as_default(self, values: dict[str, Any]) -> Any:
        """The value to pre-fill the form with."""
        return values.get(self.key, self.default)


def _pct(minimum: int = 1, maximum: int = 100) -> Any:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="%",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _kelvin() -> Any:
    return selector.ColorTempSelector(
        selector.ColorTempSelectorConfig(
            unit=selector.ColorTempSelectorUnit.KELVIN, min=1000, max=10000
        )
    )


def _seconds(minimum: float, maximum: float, step: float = 1) -> Any:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _boolean() -> Any:
    return selector.BooleanSelector()


def _select(options: list[str], key: str, *, multiple: bool = False) -> Any:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=key,
            mode=selector.SelectSelectorMode.DROPDOWN,
            multiple=multiple,
            sort=False,
        )
    )


# --------------------------------------------------------------------------
# Hub options: the global adaptive defaults every zone inherits.
# --------------------------------------------------------------------------

CONF_INTERVAL = "interval"
CONF_TRANSITION = "transition"
CONF_INITIAL_TRANSITION = "initial_transition"
CONF_SCENE_TRANSITION = "scene_transition"
CONF_MIN_BRIGHTNESS_PCT = "min_brightness_pct"
CONF_MAX_BRIGHTNESS_PCT = "max_brightness_pct"
CONF_MIN_COLOR_TEMP_K = "min_color_temp_k"
CONF_MAX_COLOR_TEMP_K = "max_color_temp_k"
CONF_BRIGHTNESS_MODE = "brightness_mode"
CONF_TIME_DARK = "brightness_mode_time_dark"
CONF_TIME_LIGHT = "brightness_mode_time_light"
CONF_SUNRISE_OFFSET = "sunrise_offset"
CONF_SUNSET_OFFSET = "sunset_offset"
CONF_NIGHT_BRIGHTNESS_PCT = "night_brightness_pct"
CONF_NIGHT_COLOR_TEMP_K = "night_color_temp_k"
CONF_PREFER_RGB_COLOR = "prefer_rgb_color"
CONF_TAKE_OVER_CONTROL = "take_over_control"
CONF_AUTORESET_MANUAL_S = "autoreset_manual_seconds"
CONF_SEPARATE_TURN_ON = "separate_turn_on_commands"
CONF_SEND_SPLIT_DELAY_MS = "send_split_delay_ms"
CONF_INTERCEPT_MEMBER_CALLS = "intercept_member_calls"

HUB_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(CONF_INTERVAL, 90, _seconds(10, 3600), section=Section.BASIC),
    FieldSpec(CONF_TRANSITION, 45, _seconds(0, 300, 0.5), validator=VALID_TRANSITION),
    FieldSpec(CONF_MIN_BRIGHTNESS_PCT, 1, _pct()),
    FieldSpec(CONF_MAX_BRIGHTNESS_PCT, 100, _pct()),
    FieldSpec(CONF_MIN_COLOR_TEMP_K, 2000, _kelvin()),
    FieldSpec(CONF_MAX_COLOR_TEMP_K, 5500, _kelvin()),
    FieldSpec(
        CONF_BRIGHTNESS_MODE,
        BrightnessMode.TANH.value,
        _select([m.value for m in BrightnessMode], "brightness_mode"),
    ),
    # --- advanced ---
    FieldSpec(
        CONF_INITIAL_TRANSITION,
        1,
        _seconds(0, 30, 0.5),
        validator=VALID_TRANSITION,
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_SCENE_TRANSITION,
        1.5,
        _seconds(0, 30, 0.5),
        validator=VALID_TRANSITION,
        section=Section.ADVANCED,
    ),
    FieldSpec(CONF_TIME_DARK, 5400, _seconds(0, 14400), section=Section.ADVANCED),
    FieldSpec(CONF_TIME_LIGHT, 2700, _seconds(0, 14400), section=Section.ADVANCED),
    FieldSpec(CONF_SUNRISE_OFFSET, 0, _seconds(-7200, 7200), section=Section.ADVANCED),
    FieldSpec(CONF_SUNSET_OFFSET, 0, _seconds(-7200, 7200), section=Section.ADVANCED),
    FieldSpec(CONF_NIGHT_BRIGHTNESS_PCT, 1, _pct(), section=Section.ADVANCED),
    FieldSpec(CONF_NIGHT_COLOR_TEMP_K, 1800, _kelvin(), section=Section.ADVANCED),
    FieldSpec(CONF_PREFER_RGB_COLOR, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_TAKE_OVER_CONTROL, True, _boolean(), section=Section.ADVANCED),
    FieldSpec(
        CONF_AUTORESET_MANUAL_S, 5400, _seconds(0, 86400), section=Section.ADVANCED
    ),
    FieldSpec(CONF_SEPARATE_TURN_ON, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(
        CONF_SEND_SPLIT_DELAY_MS,
        0,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=10000,
                step=10,
                unit_of_measurement="ms",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        section=Section.ADVANCED,
    ),
    FieldSpec(CONF_INTERCEPT_MEMBER_CALLS, False, _boolean(), section=Section.ADVANCED),
)


# --------------------------------------------------------------------------
# Zone subentry.
# --------------------------------------------------------------------------

CONF_NAME = "name"
CONF_LIGHTS = "lights"
CONF_ICON = "icon"
CONF_AREA_ID = "area_id"

# Group aggregation (ported from Relative Light Group).
CONF_ALL = "all"
CONF_HIDE_MEMBERS = "hide_members"
CONF_REMEMBER_ON_STATE = "remember_on_state"
CONF_BRIGHTNESS_STRATEGY = "brightness_strategy"
CONF_EXPAND_LIGHT_GROUPS = "expand_light_groups"

BRIGHTNESS_STRATEGIES = ["average", "median", "max", "min"]

ZONE_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_NAME,
        None,
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
    FieldSpec(
        CONF_LIGHTS,
        [],
        selector.EntitySelector(
            selector.EntitySelectorConfig(domain="light", multiple=True, reorder=True)
        ),
        required=True,
    ),
    FieldSpec(CONF_ICON, "mdi:lightbulb-group", selector.IconSelector()),
    FieldSpec(CONF_AREA_ID, None, selector.AreaSelector()),
    # --- group behaviour ---
    FieldSpec(CONF_ALL, False, _boolean(), section=Section.GROUP),
    FieldSpec(CONF_HIDE_MEMBERS, False, _boolean(), section=Section.GROUP),
    FieldSpec(CONF_REMEMBER_ON_STATE, True, _boolean(), section=Section.GROUP),
    FieldSpec(
        CONF_BRIGHTNESS_STRATEGY,
        "average",
        _select(BRIGHTNESS_STRATEGIES, "brightness_strategy"),
        section=Section.GROUP,
    ),
    FieldSpec(CONF_EXPAND_LIGHT_GROUPS, True, _boolean(), section=Section.GROUP),
)


SPECS_BY_SUBENTRY: dict[str, tuple[FieldSpec, ...]] = {
    SubentryType.ZONE.value: ZONE_SPECS,
}


def defaults_for(specs: tuple[FieldSpec, ...]) -> dict[str, Any]:
    """Every spec's default, for seeding a fresh config object."""
    return {spec.key: spec.default for spec in specs if spec.default is not None}
