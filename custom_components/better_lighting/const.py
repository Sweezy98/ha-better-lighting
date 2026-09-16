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

from .session import OptedOutOnExit, RestoreMode, ZoneAction

DOMAIN = "better_lighting"

# Sentinel for "unset" in a form. Options flows round-trip through JSON, which
# cannot carry `None` through a voluptuous schema cleanly, so Adaptive Lighting's
# string sentinel is reused.
NONE_STR = "None"

# Sentinel for a zone-level value that should fall back to the hub default.
INHERIT = "__inherit__"

PLATFORMS: list[str] = ["button", "event", "light", "select", "switch"]


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
    # The lower half of a two-button switch.
    DOWN = "down"
    ADVANCED = "advanced"


class BrightnessMode(StrEnum):
    """Which curve maps the sun's position onto a brightness percentage."""

    # Adaptive Lighting's original: flat at max all day, ramping only at night.
    SUN = "sun"
    # Smooth S-curve anchored on the nearest sunrise/sunset. Starts fading
    # before sunset, which is what most people actually want, so it is default.
    TANH = "tanh"


class SceneLightAction(StrEnum):
    """What a scene does to one named light."""

    APPLY = "apply"
    # Dark, while the rest of the room is lit.
    OFF = "off"
    # Not touched at all.
    LEAVE = "leave"


class CoverCondition(StrEnum):
    """When the covers allow presence to light a room."""

    ALL_CLOSED = "all_closed"
    ANY_CLOSED = "any_closed"
    IGNORE = "ignore"


class PresenceOnAction(StrEnum):
    """What presence does when somebody walks in."""

    # Honour the room's own power-cycle setting: adaptive, or the last scene.
    RESTORE = "restore"
    ADAPTIVE = "adaptive"
    SCENE = "scene"
    NONE = "none"


class PresenceOffAction(StrEnum):
    """What presence does once the room has emptied."""

    TURN_OFF = "turn_off"
    ADAPTIVE = "adaptive"
    NONE = "none"


class BindingType(StrEnum):
    """How a controller hears about a press."""

    # We watch a real entity's state ourselves: an event entity from a Zigbee
    # button, a binary_sensor, a text sensor, a switch, an input_button.
    ENTITY_STATE = "entity_state"
    # Only the better_lighting.press service drives it.
    SERVICE_ONLY = "service_only"
    # A bare light.turn_on on the zone's own light entity. This is the one that
    # works with a plain wall switch and no configuration, but it cannot tell
    # *which* switch pressed it -- so only one controller per zone may use it.
    ZONE_LIGHT = "zone_light"


class PressAction(StrEnum):
    """What a press of a given kind does."""

    NONE = "none"
    CYCLE_NEXT = "cycle_next"
    CYCLE_PREVIOUS = "cycle_previous"
    RESET_ADAPTIVE = "reset_adaptive"
    ZONE_OFF = "zone_off"
    TOGGLE_NIGHT = "toggle_night"
    # Relative dimming, for the hold on a rocker's up and down halves. A bias
    # rather than an absolute level, so the room keeps tracking the sun while
    # sitting a few points below (or above) where the curve would put it.
    BRIGHTEN = "brighten"
    DIM = "dim"


class RestoreOnPowerCycle(StrEnum):
    """What the first press after the room was switched off should do."""

    ADAPTIVE = "adaptive"
    LAST_SCENE = "last_scene"


class NightBehavior(StrEnum):
    """What night mode does to a zone once its source entity turns on."""

    # Clamp to the configured night brightness and a warm colour temperature.
    MIN_SETTINGS = "min_settings"
    # Apply a designated night scene instead.
    SCENE = "scene"
    # Switch the room off entirely -- but only once it is empty. Driven by the
    # source entity only: reaching for the zone's own night switch is a
    # deliberate act and dims the room rather than darkening it.
    TURN_OFF = "turn_off"
    # Night mode is configured but does nothing to this zone.
    OFF = "off"


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
    # Names a set of choices that only exist at runtime -- the scenes defined
    # so far, say. The flow supplies them when it builds the form.
    options_key: str | None = None

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
    """A colour temperature, typed rather than dragged.

    The gradient slider looks better and is useless for the job: these are
    limits, and hitting exactly 2700 K on a 9000-wide slider is luck. The
    picker belongs where a colour is being *chosen*, not where a bound is
    being set.
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1000,
            max=10000,
            step=50,
            unit_of_measurement="K",
            mode=selector.NumberSelectorMode.BOX,
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


def _select(
    options: list[str], key: str, *, multiple: bool = False, custom: bool = False
) -> Any:
    config: dict[str, Any] = {
        "options": options,
        "multiple": multiple,
        "sort": False,
    }
    if custom:
        # Button devices publish their own vocabularies; the defaults cover the
        # common ones but the user must be able to add theirs. A free-text
        # select carries no translation key, because its values are the
        # device's words rather than ours.
        config["custom_value"] = True
        config["mode"] = selector.SelectSelectorMode.LIST
    else:
        config["translation_key"] = key
        config["mode"] = selector.SelectSelectorMode.DROPDOWN
    return selector.SelectSelector(selector.SelectSelectorConfig(**config))


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
CONF_NIGHT_SOURCE = "night_source_entity"
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
    # One helper for the whole house: "everyone is asleep" is a fact about the
    # household, not about a room. What each room *does* about it stays per
    # zone, because a bedroom and a hallway should not react the same way.
    FieldSpec(
        CONF_NIGHT_SOURCE,
        None,
        selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor", "input_boolean", "schedule", "switch"]
            )
        ),
        section=Section.NIGHT,
    ),
    FieldSpec(CONF_NIGHT_BRIGHTNESS_PCT, 1, _pct(), section=Section.NIGHT),
    FieldSpec(CONF_NIGHT_COLOR_TEMP_K, 1800, _kelvin(), section=Section.NIGHT),
    FieldSpec(CONF_TIME_DARK, 5400, _seconds(0, 14400), section=Section.ADVANCED),
    FieldSpec(CONF_TIME_LIGHT, 2700, _seconds(0, 14400), section=Section.ADVANCED),
    FieldSpec(CONF_SUNRISE_OFFSET, 0, _seconds(-7200, 7200), section=Section.ADVANCED),
    FieldSpec(CONF_SUNSET_OFFSET, 0, _seconds(-7200, 7200), section=Section.ADVANCED),
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
CONF_COLOR_LIGHTS_DARK_MEMBERS = "color_lights_dark_members"

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
    # Off by default: setting a room's colour is an adjustment to the light
    # that is there, not a request to light the room.
    FieldSpec(CONF_COLOR_LIGHTS_DARK_MEMBERS, False, _boolean(), section=Section.GROUP),
)


# --- zone adaptive overrides -------------------------------------------------

CONF_ADAPTIVE_OVERRIDE = "adaptive_override_enabled"
CONF_ADAPTIVE_BRIGHTNESS_ON = "adaptive_brightness_default_on"
CONF_ADAPTIVE_COLOR_ON = "adaptive_color_default_on"

# --- zone night mode ---------------------------------------------------------

CONF_NIGHT_BEHAVIOR = "night_behavior"
CONF_NIGHT_SCENE = "night_scene_id"
CONF_NIGHT_IGNORE_PRESENCE = "night_ignore_presence"
CONF_NIGHT_TRANSITION = "night_transition"

CONF_RESTORE_ON_POWER_CYCLE = "restore_on_power_cycle"
CONF_RESUME_MAX_AGE_MIN = "resume_max_age_minutes"

ZONE_POWER_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_RESTORE_ON_POWER_CYCLE,
        RestoreOnPowerCycle.ADAPTIVE.value,
        _select([r.value for r in RestoreOnPowerCycle], "restore_on_power_cycle"),
        section=Section.POWER,
    ),
    FieldSpec(
        CONF_RESUME_MAX_AGE_MIN,
        480,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=10080,
                step=10,
                unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        section=Section.POWER,
        depends_on=(
            CONF_RESTORE_ON_POWER_CYCLE,
            (RestoreOnPowerCycle.LAST_SCENE.value,),
        ),
    ),
)

ZONE_ADAPTIVE_SPECS: tuple[FieldSpec, ...] = (
    # When False every value below is ignored and the hub defaults apply, so a
    # zone only carries its own curve when the user deliberately asked for one.
    FieldSpec(CONF_ADAPTIVE_OVERRIDE, False, _boolean(), section=Section.ADAPTIVE),
    FieldSpec(CONF_MIN_BRIGHTNESS_PCT, 1, _pct(), section=Section.ADAPTIVE),
    FieldSpec(CONF_MAX_BRIGHTNESS_PCT, 100, _pct(), section=Section.ADAPTIVE),
    FieldSpec(CONF_MIN_COLOR_TEMP_K, 2000, _kelvin(), section=Section.ADAPTIVE),
    FieldSpec(CONF_MAX_COLOR_TEMP_K, 5500, _kelvin(), section=Section.ADAPTIVE),
    FieldSpec(
        CONF_BRIGHTNESS_MODE,
        BrightnessMode.TANH.value,
        _select([m.value for m in BrightnessMode], "brightness_mode"),
        section=Section.ADAPTIVE,
    ),
    FieldSpec(
        CONF_TRANSITION,
        45,
        _seconds(0, 300, 0.5),
        validator=VALID_TRANSITION,
        section=Section.ADAPTIVE,
    ),
    FieldSpec(CONF_INTERVAL, 90, _seconds(10, 3600), section=Section.ADAPTIVE),
    FieldSpec(CONF_ADAPTIVE_BRIGHTNESS_ON, True, _boolean(), section=Section.ADAPTIVE),
    FieldSpec(CONF_ADAPTIVE_COLOR_ON, True, _boolean(), section=Section.ADAPTIVE),
)

ZONE_NIGHT_SPECS: tuple[FieldSpec, ...] = (
    # Night mode follows an existing helper rather than a schedule of its own,
    # so the user keeps one source of truth for "the house is asleep".
    FieldSpec(
        CONF_NIGHT_BEHAVIOR,
        NightBehavior.MIN_SETTINGS.value,
        _select([b.value for b in NightBehavior], "night_behavior"),
        section=Section.NIGHT,
    ),
    FieldSpec(
        CONF_NIGHT_SCENE,
        None,
        _select([], "night_scene"),
        section=Section.NIGHT,
        options_key="scenes",
        depends_on=(CONF_NIGHT_BEHAVIOR, (NightBehavior.SCENE.value,)),
    ),
    FieldSpec(
        CONF_NIGHT_BRIGHTNESS_PCT,
        1,
        _pct(),
        section=Section.NIGHT,
        depends_on=(CONF_NIGHT_BEHAVIOR, (NightBehavior.MIN_SETTINGS.value,)),
    ),
    FieldSpec(
        CONF_NIGHT_COLOR_TEMP_K,
        1800,
        _kelvin(),
        section=Section.NIGHT,
        depends_on=(CONF_NIGHT_BEHAVIOR, (NightBehavior.MIN_SETTINGS.value,)),
    ),
    FieldSpec(
        CONF_NIGHT_TRANSITION,
        2,
        _seconds(0, 300, 0.5),
        validator=VALID_TRANSITION,
        section=Section.NIGHT,
    ),
    FieldSpec(CONF_NIGHT_IGNORE_PRESENCE, False, _boolean(), section=Section.NIGHT),
)


# --------------------------------------------------------------------------
# Light profile subentry: per-light calibration (requirement 9).
# --------------------------------------------------------------------------

CONF_LIGHT_ENTITY = "light_entity"
CONF_ENABLED = "enabled"
CONF_BRIGHTNESS_OFFSET_PCT = "brightness_offset_pct"
CONF_BRIGHTNESS_MULTIPLIER = "brightness_multiplier"
CONF_COLOR_TEMP_OFFSET_K = "color_temp_offset_k"
CONF_CLAMP_TO_DEVICE = "clamp_to_device_limits"
CONF_ADAPT_BRIGHTNESS = "adapt_brightness"
CONF_ADAPT_COLOR = "adapt_color"

LIGHT_PROFILE_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_LIGHT_ENTITY,
        None,
        selector.EntitySelector(selector.EntitySelectorConfig(domain="light")),
        required=True,
    ),
    FieldSpec(CONF_ENABLED, True, _boolean()),
    # Offsets are the calibration: "this fixture reads dim". Percentage points
    # and Kelvin, because that is how the rest of the UI asks for them.
    FieldSpec(
        CONF_BRIGHTNESS_OFFSET_PCT,
        0,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-100,
                max=100,
                step=1,
                unit_of_measurement="%",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    ),
    FieldSpec(
        CONF_COLOR_TEMP_OFFSET_K,
        0,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-3000,
                max=3000,
                step=25,
                unit_of_measurement="K",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    ),
    # Limits are the operating range: "never below this, it flickers". Applied
    # after the offset, so a calibration can never breach them.
    FieldSpec(CONF_MIN_BRIGHTNESS_PCT, 1, _pct()),
    FieldSpec(CONF_MAX_BRIGHTNESS_PCT, 100, _pct()),
    FieldSpec(CONF_MIN_COLOR_TEMP_K, 1000, _kelvin(), section=Section.ADVANCED),
    FieldSpec(CONF_MAX_COLOR_TEMP_K, 10000, _kelvin(), section=Section.ADVANCED),
    FieldSpec(
        CONF_BRIGHTNESS_MULTIPLIER,
        1.0,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1, max=3.0, step=0.05, mode=selector.NumberSelectorMode.BOX
            )
        ),
        section=Section.ADVANCED,
    ),
    FieldSpec(CONF_CLAMP_TO_DEVICE, True, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_ADAPT_BRIGHTNESS, True, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_ADAPT_COLOR, True, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_PREFER_RGB_COLOR, False, _boolean(), section=Section.ADVANCED),
)


# --------------------------------------------------------------------------
# Scene subentry: a reusable recipe, not a per-room set of states.
# --------------------------------------------------------------------------

CONF_OVERRIDE_MODE = "override_mode"
CONF_BRIGHTNESS_PCT = "brightness_pct"
CONF_COLOR_FORMAT = "color_format"
CONF_COLOR_TEMP_KELVIN = "color_temp_kelvin"
CONF_RGB_COLOR = "rgb_color"
CONF_COLOR_NAME = "color_name"
CONF_ON_LIGHTS_ONLY = "on_lights_only"
CONF_IGNORE_PRESENCE = "ignore_presence"
CONF_OTHERS = "others"
CONF_ON_UNSUPPORTED_COLOR = "on_unsupported_color"
CONF_SCENE_ID = "scene_id"
CONF_SCENE_ZONES = "scene_zones"


# The data model supports all eight Home Assistant colour formats; the UI
# offers the three with a real picker. The rest stay reachable through
# services and imported configuration.
COLOR_FORMAT_NONE = "none"
COLOR_FORMATS = [
    CONF_COLOR_TEMP_KELVIN,
    CONF_RGB_COLOR,
    CONF_COLOR_NAME,
    COLOR_FORMAT_NONE,
]

OVERRIDE_MODES = ["both", "brightness", "color", "neither"]
OTHERS_POLICIES = ["adaptive", "off", "leave"]
UNSUPPORTED_COLOR_POLICIES = ["adaptive", "nearest_ct", "skip"]

SCENE_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_NAME,
        None,
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
    FieldSpec(CONF_ICON, "mdi:palette", selector.IconSelector()),
    # Which rooms this scene is *offered* in. Empty means every room, which is
    # right for a Night or Movie scene; a Reading scene usually belongs to one.
    # This never changes what applying a scene does -- a scene only ever
    # affects the room it is applied to.
    FieldSpec(
        CONF_SCENE_ZONES,
        [],
        _select([], "zone", multiple=True),
        options_key="zones",
    ),
    FieldSpec(
        CONF_OVERRIDE_MODE,
        "both",
        _select(OVERRIDE_MODES, "override_mode"),
    ),
    FieldSpec(CONF_BRIGHTNESS_PCT, 80, _pct()),
    FieldSpec(
        CONF_COLOR_FORMAT,
        CONF_COLOR_TEMP_KELVIN,
        _select(COLOR_FORMATS, "color_format"),
    ),
    FieldSpec(
        CONF_TRANSITION,
        1.5,
        _seconds(0, 300, 0.5),
        validator=VALID_TRANSITION,
    ),
    # --- behaviour ---
    FieldSpec(CONF_ON_LIGHTS_ONLY, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_IGNORE_PRESENCE, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(
        CONF_OTHERS,
        "adaptive",
        _select(OTHERS_POLICIES, "others"),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_ON_UNSUPPORTED_COLOR,
        "adaptive",
        _select(UNSUPPORTED_COLOR_POLICIES, "on_unsupported_color"),
        section=Section.ADVANCED,
    ),
)

# Step two of the scene flow: only the field for the chosen format is shown,
# because a colour carrying two formats is a contradiction we would rather not
# be able to express in the first place.
SCENE_COLOR_SPECS: dict[str, FieldSpec] = {
    CONF_COLOR_TEMP_KELVIN: FieldSpec(
        CONF_COLOR_TEMP_KELVIN, 3000, _kelvin(), required=True
    ),
    CONF_RGB_COLOR: FieldSpec(
        CONF_RGB_COLOR, [255, 180, 100], selector.ColorRGBSelector(), required=True
    ),
    CONF_COLOR_NAME: FieldSpec(
        CONF_COLOR_NAME,
        "warmwhite",
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
}

# --------------------------------------------------------------------------
# Scenes belonging to a room.
# --------------------------------------------------------------------------
# A room scene is a list of lights and what each should look like, in the
# spirit of Home Assistant's own scenes -- not a brightness-and-colour recipe
# applied wholesale. It therefore carries no brightness or colour of its own:
# those live on the individual lights, which is the only way "the strip behind
# the television at 7% orange, the ceiling off, the desk lamp at 20% keeping
# whatever colour the sun says" can be said at all. Which axes the scene takes
# over follows from what each light actually names.
CONF_ZONE_SCENES = "scenes"
# The light switches that drive this room.
CONF_ZONE_SWITCHES = "switches"
CONF_SWITCH_ID = "switch_id"
# Per-light calibration lives with the room whose lights it calibrates.
CONF_ZONE_PROFILES = "light_profiles"

ZONE_SCENE_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_NAME,
        None,
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
    FieldSpec(CONF_ICON, "mdi:palette", selector.IconSelector()),
    FieldSpec(
        CONF_TRANSITION,
        1.5,
        _seconds(0, 300, 0.5),
        validator=VALID_TRANSITION,
    ),
    FieldSpec(CONF_ON_LIGHTS_ONLY, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_IGNORE_PRESENCE, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(
        CONF_OTHERS,
        "adaptive",
        _select(OTHERS_POLICIES, "others"),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_ON_UNSUPPORTED_COLOR,
        "adaptive",
        _select(UNSUPPORTED_COLOR_POLICIES, "on_unsupported_color"),
        section=Section.ADVANCED,
    ),
)


# Per-light entries inside a scene.
CONF_SCENE_LIGHTS = "lights"
CONF_LIGHT_ACTION = "action"
CONF_WARM_WHITE = "warm_white"
CONF_COLD_WHITE = "cold_white"

# "inherit" takes the scene's own colour; "none" leaves this light's colour to
# the sun; "rgb_white" is RGB plus the two white channels an RGBWW fixture has.
COLOR_FORMAT_INHERIT = "inherit"
COLOR_FORMAT_RGB_WHITE = "rgb_white"
# A colour named once in the global config and picked by name here. Resolved to
# a real colour when it is chosen, not held as a live reference: a scene should
# not change under you because a preset was edited months later.
COLOR_FORMAT_PRESET = "preset"
SCENE_LIGHT_COLOR_FORMATS = [
    COLOR_FORMAT_INHERIT,
    COLOR_FORMAT_NONE,
    COLOR_FORMAT_PRESET,
    CONF_COLOR_TEMP_KELVIN,
    CONF_RGB_COLOR,
    COLOR_FORMAT_RGB_WHITE,
]

# --------------------------------------------------------------------------
# Colour presets, in the global config.
# --------------------------------------------------------------------------
# The house's named colours -- "TV orange", "candle" -- so a colour used in
# several scenes is described once and picked by name. They replace the
# house-wide scene recipes that scenes-in-rooms made redundant, and they are
# the practical answer to a config flow having no colour wheel.
CONF_COLOR_PRESETS = "color_presets"
CONF_PRESET_NAME = "preset_name"

COLOR_PRESET_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_NAME,
        None,
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
    FieldSpec(
        CONF_COLOR_FORMAT,
        CONF_RGB_COLOR,
        _select([CONF_RGB_COLOR, CONF_COLOR_TEMP_KELVIN], "color_format"),
    ),
)


def scene_light_specs() -> tuple[FieldSpec, ...]:
    """The form for one light inside a scene.

    Deliberately short. The colour value is asked for in a second step, so the
    user sees the one control they chose rather than every colour field at
    once.
    """
    return (
        FieldSpec(
            "light",
            None,
            _select([], "light", multiple=False),
            required=True,
            options_key="lights",
        ),
        FieldSpec(
            CONF_LIGHT_ACTION,
            SceneLightAction.APPLY.value,
            _select([a.value for a in SceneLightAction], "scene_light_action"),
        ),
        # Omit to use the scene's own brightness.
        FieldSpec(CONF_BRIGHTNESS_PCT, None, _pct()),
        FieldSpec(
            CONF_COLOR_FORMAT,
            COLOR_FORMAT_INHERIT,
            _select(SCENE_LIGHT_COLOR_FORMATS, "scene_light_color_format"),
        ),
    )


def _white_channel(key: str) -> FieldSpec:
    return FieldSpec(
        key,
        0,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=255, step=1, mode=selector.NumberSelectorMode.SLIDER
            )
        ),
    )


def scene_light_color_specs(color_format: str | None) -> tuple[FieldSpec, ...]:
    """The second step: just the control for the chosen colour format.

    These are Home Assistant's own colour picker and colour-temperature
    slider. There is no combined light-colour control available to config
    flows -- the wheel in the more-info dialog is a frontend component -- so
    picking the format first and showing one control is as close as it gets.
    """
    if color_format == CONF_COLOR_TEMP_KELVIN:
        return (FieldSpec(CONF_COLOR_TEMP_KELVIN, 2700, _kelvin(), required=True),)
    if color_format == CONF_RGB_COLOR:
        return (
            FieldSpec(
                CONF_RGB_COLOR,
                [255, 96, 16],
                selector.ColorRGBSelector(),
                required=True,
            ),
        )
    if color_format == COLOR_FORMAT_RGB_WHITE:
        # RGBWW strips have two white LEDs behind the colour ones. Home
        # Assistant has no five-channel picker, so the whites are sliders
        # alongside its ordinary colour picker.
        return (
            FieldSpec(
                CONF_RGB_COLOR,
                [255, 96, 16],
                selector.ColorRGBSelector(),
                required=True,
            ),
            _white_channel(CONF_WARM_WHITE),
            _white_channel(CONF_COLD_WHITE),
        )
    return ()


# --------------------------------------------------------------------------
# Controller subentry: one light switch, with its own ordered list.
# --------------------------------------------------------------------------

CONF_ZONE_ID = "zone_id"
CONF_BINDING_TYPE = "binding_type"
CONF_BINDING_ENTITY = "binding_entity"
CONF_PRESS_STATES = "press_states"
CONF_DOUBLE_PRESS_STATES = "double_press_states"
CONF_LONG_PRESS_STATES = "long_press_states"
CONF_PRESS_ATTRIBUTE = "press_attribute"
CONF_IS_DEFAULT = "is_default"
CONF_ADAPTIVE_POSITION = "adaptive_position"
CONF_OFF_AT_END = "off_at_end"
CONF_WRAP_AROUND = "wrap_around"
CONF_ON_FOREIGN = "on_foreign_state"
CONF_DOUBLE_PRESS_ACTION = "double_press_action"
CONF_LONG_PRESS_ACTION = "long_press_action"
CONF_MIN_PRESS_INTERVAL_MS = "min_press_interval_ms"
CONF_COALESCE_WINDOW_MS = "coalesce_window_ms"
CONF_SCENE_ORDER = "scene_order"

# A rocker publishes a second vocabulary for its lower half. Configured as its
# own set of words rather than a second controller, so one switch stays one
# object: up lights the room and cycles it, down switches it off, and holding
# either end takes the brightness with it.
CONF_DOWN_PRESS_STATES = "down_press_states"
CONF_DOWN_DOUBLE_PRESS_STATES = "down_double_press_states"
CONF_DOWN_LONG_PRESS_STATES = "down_long_press_states"
CONF_DOWN_PRESS_ACTION = "down_press_action"
CONF_DOWN_DOUBLE_PRESS_ACTION = "down_double_press_action"
CONF_DOWN_LONG_PRESS_ACTION = "down_long_press_action"
CONF_DIM_STEP_PCT = "dim_step_pct"

DEFAULT_DOWN_PRESS_STATES = ["off", "down", "down_single", "down_press"]
DEFAULT_DOWN_DOUBLE_PRESS_STATES = ["down_double", "down_double_press"]
DEFAULT_DOWN_LONG_PRESS_STATES = ["down_hold", "down_long_press"]

ADAPTIVE_POSITIONS = ["first", "last", "none"]
FOREIGN_POLICIES = ["restart", "remember_position"]
PRESS_ACTIONS = [a.value for a in PressAction]

# Defaults cover the vocabularies Zigbee and Z-Wave buttons actually publish,
# so most devices work without touching these.
DEFAULT_PRESS_STATES = ["on", "single", "press", "short_release", "initial_press"]
DEFAULT_DOUBLE_PRESS_STATES = ["double", "double_press"]
DEFAULT_LONG_PRESS_STATES = ["hold", "long_press"]

CONTROLLER_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_NAME,
        None,
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
    FieldSpec(
        CONF_ZONE_ID,
        None,
        _select([], "zone"),
        required=True,
        options_key="zones",
    ),
    FieldSpec(
        CONF_BINDING_TYPE,
        BindingType.ENTITY_STATE.value,
        _select([b.value for b in BindingType], "binding_type"),
    ),
    FieldSpec(
        CONF_BINDING_ENTITY,
        None,
        # Deliberately unfiltered: button devices surface as event, sensor,
        # binary_sensor or input_button depending on the integration.
        selector.EntitySelector(selector.EntitySelectorConfig()),
        depends_on=(CONF_BINDING_TYPE, (BindingType.ENTITY_STATE.value,)),
    ),
    FieldSpec(CONF_IS_DEFAULT, False, _boolean()),
    # --- cycle shape ---
    FieldSpec(
        CONF_ADAPTIVE_POSITION,
        "first",
        _select(ADAPTIVE_POSITIONS, "adaptive_position"),
        section=Section.ADVANCED,
    ),
    FieldSpec(CONF_OFF_AT_END, False, _boolean(), section=Section.ADVANCED),
    FieldSpec(CONF_WRAP_AROUND, True, _boolean(), section=Section.ADVANCED),
    FieldSpec(
        CONF_ON_FOREIGN,
        "restart",
        _select(FOREIGN_POLICIES, "on_foreign_state"),
        section=Section.ADVANCED,
    ),
    # --- multi-press ---
    FieldSpec(
        CONF_DOUBLE_PRESS_ACTION,
        PressAction.CYCLE_PREVIOUS.value,
        _select(PRESS_ACTIONS, "press_action"),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_LONG_PRESS_ACTION,
        PressAction.BRIGHTEN.value,
        _select(PRESS_ACTIONS, "press_action"),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_PRESS_STATES,
        DEFAULT_PRESS_STATES,
        _select(DEFAULT_PRESS_STATES, "press_states", multiple=True, custom=True),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_DOUBLE_PRESS_STATES,
        DEFAULT_DOUBLE_PRESS_STATES,
        _select(
            DEFAULT_DOUBLE_PRESS_STATES, "press_states", multiple=True, custom=True
        ),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_LONG_PRESS_STATES,
        DEFAULT_LONG_PRESS_STATES,
        _select(DEFAULT_LONG_PRESS_STATES, "press_states", multiple=True, custom=True),
        section=Section.ADVANCED,
    ),
    # --- the lower half of a rocker ---
    FieldSpec(
        CONF_DOWN_PRESS_ACTION,
        PressAction.ZONE_OFF.value,
        _select(PRESS_ACTIONS, "press_action"),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_DOWN_DOUBLE_PRESS_ACTION,
        PressAction.NONE.value,
        _select(PRESS_ACTIONS, "press_action"),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_DOWN_LONG_PRESS_ACTION,
        PressAction.DIM.value,
        _select(PRESS_ACTIONS, "press_action"),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_DOWN_PRESS_STATES,
        [],
        _select(DEFAULT_DOWN_PRESS_STATES, "press_states", multiple=True, custom=True),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_DOWN_DOUBLE_PRESS_STATES,
        [],
        _select(
            DEFAULT_DOWN_DOUBLE_PRESS_STATES,
            "press_states",
            multiple=True,
            custom=True,
        ),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_DOWN_LONG_PRESS_STATES,
        [],
        _select(
            DEFAULT_DOWN_LONG_PRESS_STATES, "press_states", multiple=True, custom=True
        ),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_DIM_STEP_PCT,
        10,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=50,
                step=1,
                unit_of_measurement="%",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        section=Section.DOWN,
    ),
    FieldSpec(
        CONF_PRESS_ATTRIBUTE,
        "event_type",
        selector.TextSelector(selector.TextSelectorConfig()),
        section=Section.ADVANCED,
    ),
    # --- debounce ---
    FieldSpec(
        CONF_MIN_PRESS_INTERVAL_MS,
        150,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=2000,
                step=10,
                unit_of_measurement="ms",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_COALESCE_WINDOW_MS,
        350,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=2000,
                step=10,
                unit_of_measurement="ms",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        section=Section.ADVANCED,
    ),
)


# --------------------------------------------------------------------------
# Zone presence. The full presence subsystem is milestone 5; this is only the
# input a cross-zone mode needs in order to gate on "is anybody in there".
# --------------------------------------------------------------------------

CONF_PRESENCE_ENTITY = "presence_entity"
CONF_PRESENCE_CLEAR_DELAY = "presence_clear_delay"


CONF_PRESENCE_COVERS = "presence_covers"
CONF_COVER_CONDITION = "cover_condition"
CONF_COVER_UNKNOWN_BLOCKS = "cover_unknown_blocks"
CONF_PRESENCE_ON_ACTION = "presence_on_action"
CONF_PRESENCE_ON_SCENE = "presence_on_scene_id"
CONF_PRESENCE_ON_ONLY_WHEN_OFF = "presence_on_only_when_off"
CONF_PRESENCE_OFF_ACTION = "presence_off_action"
CONF_PRESENCE_RESPECTS_MANUAL = "presence_respects_manual"

# --------------------------------------------------------------------------
# Insect mode: a window is open, so stop attracting everything outside.
# --------------------------------------------------------------------------

CONF_WINDOW_ENTITIES = "window_entities"
CONF_INSECT_SCENE = "insect_scene_id"
CONF_INSECT_ONLY_WHEN_ON = "insect_only_when_on"
CONF_INSECT_OPEN_DELAY = "insect_open_delay"
CONF_INSECT_CLOSE_DELAY = "insect_close_delay"
CONF_INSECT_OVERRIDABLE = "insect_overridable_by_press"


class InsectAction(StrEnum):
    """What an open window does to a room.

    Moths navigate by light and are drawn to short wavelengths, so the useful
    answers are "go amber" and "go dark" -- which is why this is not simply a
    scene picker.
    """

    SCENE = "scene"
    COLOR_TEMP = "color_temp"
    RGB_COLOR = "rgb_color"
    TURN_OFF = "turn_off"


CONF_INSECT_ACTION = "insect_action"
CONF_INSECT_COLOR_TEMP_K = "insect_color_temp_k"
CONF_INSECT_RGB_COLOR = "insect_rgb_color"
CONF_INSECT_BRIGHTNESS_PCT = "insect_brightness_pct"

ZONE_INSECT_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_INSECT_ACTION,
        InsectAction.COLOR_TEMP.value,
        _select([a.value for a in InsectAction], "insect_action"),
        section=Section.INSECT,
    ),
    FieldSpec(
        CONF_INSECT_COLOR_TEMP_K,
        2000,
        _kelvin(),
        section=Section.INSECT,
        depends_on=(CONF_INSECT_ACTION, (InsectAction.COLOR_TEMP.value,)),
    ),
    FieldSpec(
        CONF_INSECT_RGB_COLOR,
        [255, 140, 40],
        selector.ColorRGBSelector(),
        section=Section.INSECT,
        depends_on=(CONF_INSECT_ACTION, (InsectAction.RGB_COLOR.value,)),
    ),
    FieldSpec(
        CONF_INSECT_BRIGHTNESS_PCT,
        30,
        _pct(),
        section=Section.INSECT,
        depends_on=(
            CONF_INSECT_ACTION,
            (InsectAction.COLOR_TEMP.value, InsectAction.RGB_COLOR.value),
        ),
    ),
    FieldSpec(
        CONF_WINDOW_ENTITIES,
        [],
        selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor", "cover"], multiple=True
            )
        ),
        section=Section.INSECT,
    ),
    FieldSpec(
        CONF_INSECT_SCENE,
        None,
        _select([], "scene"),
        section=Section.INSECT,
        options_key="scenes",
    ),
    FieldSpec(CONF_INSECT_ONLY_WHEN_ON, True, _boolean(), section=Section.INSECT),
    FieldSpec(CONF_INSECT_OPEN_DELAY, 0, _seconds(0, 600), section=Section.INSECT),
    FieldSpec(CONF_INSECT_CLOSE_DELAY, 5, _seconds(0, 600), section=Section.INSECT),
    FieldSpec(CONF_INSECT_OVERRIDABLE, True, _boolean(), section=Section.INSECT),
)

ZONE_PRESENCE_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_PRESENCE_ENTITY,
        None,
        selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor", "input_boolean", "device_tracker", "person"]
            )
        ),
        section=Section.PRESENCE,
    ),
    FieldSpec(
        CONF_PRESENCE_CLEAR_DELAY,
        120,
        _seconds(0, 3600),
        section=Section.PRESENCE,
    ),
    FieldSpec(
        CONF_PRESENCE_ON_ACTION,
        PresenceOnAction.RESTORE.value,
        _select([a.value for a in PresenceOnAction], "presence_on_action"),
        section=Section.PRESENCE,
    ),
    FieldSpec(
        CONF_PRESENCE_ON_SCENE,
        None,
        _select([], "scene"),
        section=Section.PRESENCE,
        options_key="scenes",
    ),
    FieldSpec(
        CONF_PRESENCE_OFF_ACTION,
        PresenceOffAction.TURN_OFF.value,
        _select([a.value for a in PresenceOffAction], "presence_off_action"),
        section=Section.PRESENCE,
    ),
    # The cover gate of requirement 3: presence only lights the room when the
    # blinds are down, so a sunlit room is not lit pointlessly.
    FieldSpec(
        CONF_PRESENCE_COVERS,
        [],
        selector.EntitySelector(
            selector.EntitySelectorConfig(domain="cover", multiple=True)
        ),
        section=Section.PRESENCE,
    ),
    FieldSpec(
        CONF_COVER_CONDITION,
        CoverCondition.ALL_CLOSED.value,
        _select([c.value for c in CoverCondition], "cover_condition"),
        section=Section.PRESENCE,
    ),
    FieldSpec(
        CONF_PRESENCE_ON_ONLY_WHEN_OFF, True, _boolean(), section=Section.PRESENCE
    ),
    FieldSpec(
        CONF_PRESENCE_RESPECTS_MANUAL, True, _boolean(), section=Section.PRESENCE
    ),
    FieldSpec(CONF_COVER_UNKNOWN_BLOCKS, True, _boolean(), section=Section.PRESENCE),
)


# --------------------------------------------------------------------------
# Mode subentry: a cross-zone mode such as Home Cinema.
# --------------------------------------------------------------------------

CONF_STATES = "states"
CONF_SNAPSHOT_ON_ENTER = "snapshot_on_enter"
CONF_RESTORE_MODE = "restore_mode"
CONF_OPTED_OUT_ON_EXIT = "opted_out_on_exit"
CONF_DEFERRED_TTL_MIN = "deferred_ttl_minutes"
CONF_RULES = "rules"

# Rule fields.
CONF_RULE_STATES = "mode_states"
CONF_RULE_ZONES = "zones"
CONF_RULE_ACTION = "action"
CONF_RULE_SCENE = "scene_id"
CONF_RULE_RESPECT_PRESENCE = "respect_presence"
CONF_RULE_DEFER_IF_OCCUPIED = "defer_if_occupied"
CONF_RULE_ENTRY_SCENE = "presence_entry_scene"
CONF_RULE_ENTRY_ACTION = "presence_entry_action"
CONF_RULE_ON_FREE = "on_free_action"

IDLE_STATE = "off"
DEFAULT_MODE_STATES = ["playing", "paused", "credits"]

ZONE_ACTIONS = [a.value for a in ZoneAction]
RESTORE_MODES = [r.value for r in RestoreMode]
OPTED_OUT_ON_EXIT = [o.value for o in OptedOutOnExit]
ON_FREE_ACTIONS = ["turn_off", "keep", "reapply_mode_action"]

MODE_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        CONF_NAME,
        None,
        selector.TextSelector(selector.TextSelectorConfig()),
        required=True,
    ),
    FieldSpec(CONF_ICON, "mdi:movie-open", selector.IconSelector()),
    FieldSpec(
        CONF_STATES,
        DEFAULT_MODE_STATES,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=DEFAULT_MODE_STATES,
                multiple=True,
                custom_value=True,
                mode=selector.SelectSelectorMode.LIST,
                sort=False,
            )
        ),
    ),
    # --- advanced ---
    FieldSpec(CONF_SNAPSHOT_ON_ENTER, True, _boolean(), section=Section.ADVANCED),
    FieldSpec(
        CONF_RESTORE_MODE,
        RestoreMode.ADAPTIVE_ON_PREVIOUSLY_ON.value,
        _select(RESTORE_MODES, "restore_mode"),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_OPTED_OUT_ON_EXIT,
        OptedOutOnExit.KEEP.value,
        _select(OPTED_OUT_ON_EXIT, "opted_out_on_exit"),
        section=Section.ADVANCED,
    ),
    FieldSpec(
        CONF_DEFERRED_TTL_MIN,
        240,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=10,
                unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        section=Section.ADVANCED,
    ),
)


def mode_rule_specs(states: list[str]) -> tuple[FieldSpec, ...]:
    """The form for one (states x zones) rule.

    One rule can cover several states and several rooms, so the common case --
    "these three rooms go dark while the film is playing or the credits roll"
    -- is a single form rather than nine.
    """
    return (
        FieldSpec(
            CONF_RULE_STATES,
            states,
            _select(states, "mode_state", multiple=True),
            required=True,
        ),
        # One room per rule. A living room wants a different scene for
        # playing, paused and credits while the kitchen is simply off for all
        # three, and expressing that as one rule per room reads far better
        # than a grid -- as well as making the scene picker unambiguous, now
        # that a scene belongs to exactly one room.
        FieldSpec(
            CONF_RULE_ZONES,
            None,
            _select([], "zone"),
            required=True,
            options_key="zones",
        ),
        FieldSpec(
            CONF_RULE_ACTION,
            ZoneAction.KEEP.value,
            _select(ZONE_ACTIONS, "zone_action"),
        ),
        FieldSpec(CONF_RULE_SCENE, None, _select([], "scene"), options_key="scenes"),
        FieldSpec(
            CONF_RULE_RESPECT_PRESENCE, True, _boolean(), section=Section.ADVANCED
        ),
        FieldSpec(
            CONF_RULE_DEFER_IF_OCCUPIED, True, _boolean(), section=Section.ADVANCED
        ),
        # Walking back in mid-session deserves the same four choices as the
        # state change that emptied the room in the first place.
        FieldSpec(
            CONF_RULE_ENTRY_ACTION,
            ZoneAction.KEEP.value,
            _select(ZONE_ACTIONS, "zone_action"),
            section=Section.ADVANCED,
        ),
        FieldSpec(
            CONF_RULE_ENTRY_SCENE,
            None,
            _select([], "scene"),
            options_key="scenes",
            section=Section.ADVANCED,
        ),
        FieldSpec(
            CONF_RULE_ON_FREE,
            "turn_off",
            _select(ON_FREE_ACTIONS, "on_free_action"),
            section=Section.ADVANCED,
        ),
    )


ZONE_SPECS = (
    ZONE_SPECS
    + ZONE_ADAPTIVE_SPECS
    + ZONE_NIGHT_SPECS
    + ZONE_POWER_SPECS
    + ZONE_PRESENCE_SPECS
    + ZONE_INSECT_SPECS
)


SPECS_BY_SUBENTRY: dict[str, tuple[FieldSpec, ...]] = {
    SubentryType.ZONE.value: ZONE_SPECS,
    SubentryType.LIGHT_PROFILE.value: LIGHT_PROFILE_SPECS,
    SubentryType.SCENE.value: SCENE_SPECS,
    SubentryType.CONTROLLER.value: CONTROLLER_SPECS,
    SubentryType.MODE.value: MODE_SPECS,
}


def defaults_for(specs: tuple[FieldSpec, ...]) -> dict[str, Any]:
    """Every spec's default, for seeding a fresh config object."""
    return {spec.key: spec.default for spec in specs if spec.default is not None}
