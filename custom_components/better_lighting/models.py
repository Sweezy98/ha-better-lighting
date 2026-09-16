"""Typed configuration objects parsed from the hub entry and its subentries.

The config flow stores plain JSON; these dataclasses are the typed view the
runtime works with, so a missing or stale key is defaulted in exactly one place.

This module also owns the three-layer resolution the user asked for:
**hub defaults -> zone override -> per-light profile**. Note the deliberate
asymmetry between the first two layers and the third, which the UI strings
spell out: hub and zone values define the *curve* (what "darkest" and
"brightest" mean across the day), while a light profile *clamps and calibrates*
whatever the curve produced.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import astral
from homeassistant.util import slugify

from .adaptive import AdaptiveConfig
from .brightness import BrightnessStrategy
from .const import (
    COLOR_FORMAT_INHERIT,
    COLOR_FORMAT_NONE,
    COLOR_FORMAT_RGB_WHITE,
    CONF_ADAPT_BRIGHTNESS,
    CONF_ADAPT_COLOR,
    CONF_ADAPTIVE_BRIGHTNESS_ON,
    CONF_ADAPTIVE_COLOR_ON,
    CONF_ADAPTIVE_OVERRIDE,
    CONF_ADAPTIVE_POSITION,
    CONF_ALL,
    CONF_ANY_CHANGE_IS_PRESS,
    CONF_AREA_ID,
    CONF_AUTORESET_MANUAL_S,
    CONF_BINDING_ENTITY,
    CONF_BINDING_TYPE,
    CONF_BRIGHTNESS_MODE,
    CONF_BRIGHTNESS_MULTIPLIER,
    CONF_BRIGHTNESS_OFFSET_PCT,
    CONF_BRIGHTNESS_PCT,
    CONF_BRIGHTNESS_STRATEGY,
    CONF_CLAMP_TO_DEVICE,
    CONF_COLD_WHITE,
    CONF_COLOR_FORMAT,
    CONF_COLOR_LIGHTS_DARK_MEMBERS,
    CONF_COLOR_NAME,
    CONF_COLOR_TEMP_KELVIN,
    CONF_COLOR_TEMP_OFFSET_K,
    CONF_COVER_CONDITION,
    CONF_COVER_UNKNOWN_BLOCKS,
    CONF_DEFERRED_TTL_MIN,
    CONF_DIM_STEP_PCT,
    CONF_DOUBLE_FROM_PRESSES,
    CONF_DOUBLE_PRESS_ACTION,
    CONF_DOUBLE_PRESS_STATES,
    CONF_DOUBLE_PRESS_WINDOW_MS,
    CONF_DOWN_DOUBLE_FROM_PRESSES,
    CONF_DOWN_DOUBLE_PRESS_ACTION,
    CONF_DOWN_DOUBLE_PRESS_STATES,
    CONF_DOWN_DOUBLE_PRESS_WINDOW_MS,
    CONF_DOWN_LONG_PRESS_ACTION,
    CONF_DOWN_LONG_PRESS_STATES,
    CONF_DOWN_PRESS_ACTION,
    CONF_DOWN_PRESS_STATES,
    CONF_ENABLED,
    CONF_EXPAND_LIGHT_GROUPS,
    CONF_HIDE_MEMBERS,
    CONF_HOLD_INTERVAL_MS,
    CONF_HOLD_RAMP,
    CONF_ICON,
    CONF_IGNORE_PRESENCE,
    CONF_INITIAL_TRANSITION,
    CONF_INSECT_ACTION,
    CONF_INSECT_BRIGHTNESS_PCT,
    CONF_INSECT_CLOSE_DELAY,
    CONF_INSECT_COLOR_TEMP_K,
    CONF_INSECT_ONLY_WHEN_ON,
    CONF_INSECT_OPEN_DELAY,
    CONF_INSECT_OVERRIDABLE,
    CONF_INSECT_RGB_COLOR,
    CONF_INSECT_SCENE,
    CONF_INTERCEPT_MEMBER_CALLS,
    CONF_INTERVAL,
    CONF_IS_DEFAULT,
    CONF_LIGHT_ACTION,
    CONF_LIGHT_ENTITY,
    CONF_LIGHTS,
    CONF_LONG_PRESS_ACTION,
    CONF_LONG_PRESS_STATES,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_K,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_K,
    CONF_NAME,
    CONF_NIGHT_BEHAVIOR,
    CONF_NIGHT_BRIGHTNESS_PCT,
    CONF_NIGHT_COLOR_TEMP_K,
    CONF_NIGHT_IGNORE_PRESENCE,
    CONF_NIGHT_SCENE,
    CONF_NIGHT_SOURCE,
    CONF_NIGHT_TRANSITION,
    CONF_OFF_AT_END,
    CONF_ON_FOREIGN,
    CONF_ON_LIGHTS_ONLY,
    CONF_ON_UNSUPPORTED_COLOR,
    CONF_OPTED_OUT_ON_EXIT,
    CONF_OTHERS,
    CONF_OVERRIDE_MODE,
    CONF_PREFER_RGB_COLOR,
    CONF_PRESENCE_CLEAR_DELAY,
    CONF_PRESENCE_COVERS,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_OFF_ACTION,
    CONF_PRESENCE_ON_ACTION,
    CONF_PRESENCE_ON_ONLY_WHEN_OFF,
    CONF_PRESENCE_ON_SCENE,
    CONF_PRESENCE_RESPECTS_MANUAL,
    CONF_PRESS_ATTRIBUTE,
    CONF_PRESS_STATES,
    CONF_RELEASE_STATES,
    CONF_REMEMBER_ON_STATE,
    CONF_RESTORE_MODE,
    CONF_RESTORE_ON_POWER_CYCLE,
    CONF_RESUME_MAX_AGE_MIN,
    CONF_RGB_COLOR,
    CONF_RULE_ACTION,
    CONF_RULE_DEFER_IF_OCCUPIED,
    CONF_RULE_ENTRY_ACTION,
    CONF_RULE_ENTRY_SCENE,
    CONF_RULE_ON_FREE,
    CONF_RULE_RESPECT_PRESENCE,
    CONF_RULE_SCENE,
    CONF_RULE_STATES,
    CONF_RULE_ZONES,
    CONF_RULES,
    CONF_SCENE_ID,
    CONF_SCENE_LIGHTS,
    CONF_SCENE_ORDER,
    CONF_SCENE_ORDER_EXCLUDED,
    CONF_SCENE_TRANSITION,
    CONF_SCENE_ZONES,
    CONF_SEND_SPLIT_DELAY_MS,
    CONF_SEPARATE_TURN_ON,
    CONF_SNAPSHOT_ON_ENTER,
    CONF_STATES,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SWITCH_ID,
    CONF_TAKE_OVER_CONTROL,
    CONF_TIME_DARK,
    CONF_TIME_LIGHT,
    CONF_TRANSITION,
    CONF_WARM_WHITE,
    CONF_WINDOW_ENTITIES,
    CONF_WRAP_AROUND,
    CONF_ZONE_ID,
    CONF_ZONE_PROFILES,
    CONF_ZONE_SCENES,
    CONF_ZONE_SWITCHES,
    CONTROLLER_SPECS,
    HUB_SPECS,
    LIGHT_PROFILE_SPECS,
    MODE_SPECS,
    SCENE_SPECS,
    ZONE_SPECS,
    BindingType,
    BrightnessMode,
    CoverCondition,
    InsectAction,
    NightBehavior,
    OptedOutOnExit,
    PresenceOffAction,
    PresenceOnAction,
    PressAction,
    RestoreMode,
    RestoreOnPowerCycle,
    SceneLightAction,
    ZoneAction,
    defaults_for,
)
from .cycle import AdaptivePosition, CycleConfig, ForeignPolicy, build_cycle
from .profiles import LightProfile
from .scenes import (
    ALL_LIGHTS,
    OthersPolicy,
    Scene,
    SceneLightSpec,
    SceneOverride,
    UnsupportedColorPolicy,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

_HUB_DEFAULTS = defaults_for(HUB_SPECS)
_ZONE_DEFAULTS = defaults_for(ZONE_SPECS)
_PROFILE_DEFAULTS = defaults_for(LIGHT_PROFILE_SPECS)
_SCENE_DEFAULTS = defaults_for(SCENE_SPECS)
_CONTROLLER_DEFAULTS = defaults_for(CONTROLLER_SPECS)
_MODE_DEFAULTS = defaults_for(MODE_SPECS)


@dataclass(frozen=True, slots=True)
class HubConfig:
    """Global defaults every zone inherits."""

    interval: int
    transition: float
    initial_transition: float
    scene_transition: float
    min_brightness_pct: float
    max_brightness_pct: float
    min_color_temp_k: int
    max_color_temp_k: int
    brightness_mode: BrightnessMode
    # The house-wide "everyone is asleep" helper. Each zone decides what to do
    # about it; a zone that still carries its own from an older version keeps
    # using that until this one is set.
    night_source_entity: str | None
    time_dark: int
    time_light: int
    sunrise_offset: int
    sunset_offset: int
    night_brightness_pct: float
    night_color_temp_k: int
    prefer_rgb_color: bool
    take_over_control: bool
    autoreset_manual_seconds: int
    separate_turn_on_commands: bool
    send_split_delay_ms: int
    intercept_member_calls: bool

    @classmethod
    def from_options(cls, options: dict[str, Any] | None) -> Self:
        """Build from ``entry.options``, defaulting anything absent."""
        raw = {**_HUB_DEFAULTS, **(options or {})}
        return cls(
            interval=int(raw[CONF_INTERVAL]),
            transition=float(raw[CONF_TRANSITION]),
            initial_transition=float(raw[CONF_INITIAL_TRANSITION]),
            scene_transition=float(raw[CONF_SCENE_TRANSITION]),
            min_brightness_pct=float(raw[CONF_MIN_BRIGHTNESS_PCT]),
            max_brightness_pct=float(raw[CONF_MAX_BRIGHTNESS_PCT]),
            min_color_temp_k=int(raw[CONF_MIN_COLOR_TEMP_K]),
            max_color_temp_k=int(raw[CONF_MAX_COLOR_TEMP_K]),
            brightness_mode=BrightnessMode(raw[CONF_BRIGHTNESS_MODE]),
            night_source_entity=raw.get(CONF_NIGHT_SOURCE) or None,
            time_dark=int(raw[CONF_TIME_DARK]),
            time_light=int(raw[CONF_TIME_LIGHT]),
            sunrise_offset=int(raw[CONF_SUNRISE_OFFSET]),
            sunset_offset=int(raw[CONF_SUNSET_OFFSET]),
            night_brightness_pct=float(raw[CONF_NIGHT_BRIGHTNESS_PCT]),
            night_color_temp_k=int(raw[CONF_NIGHT_COLOR_TEMP_K]),
            prefer_rgb_color=bool(raw[CONF_PREFER_RGB_COLOR]),
            take_over_control=bool(raw[CONF_TAKE_OVER_CONTROL]),
            autoreset_manual_seconds=int(raw[CONF_AUTORESET_MANUAL_S]),
            separate_turn_on_commands=bool(raw[CONF_SEPARATE_TURN_ON]),
            send_split_delay_ms=int(raw[CONF_SEND_SPLIT_DELAY_MS]),
            intercept_member_calls=bool(raw[CONF_INTERCEPT_MEMBER_CALLS]),
        )


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    """One room: its lights, its curve, and how its group entity behaves."""

    subentry_id: str
    name: str
    lights: tuple[str, ...]
    icon: str
    area_id: str | None

    # Group aggregation.
    all_members_on: bool
    hide_members: bool
    remember_on_state: bool
    brightness_strategy: BrightnessStrategy
    expand_light_groups: bool
    color_lights_dark_members: bool

    # Adaptive. When `adaptive_override` is False every value below is ignored
    # and the hub default is used instead.
    adaptive_override: bool
    # The two axes are independent: a room can track the sun's colour all
    # evening while its brightness stays wherever it was put.
    adaptive_brightness_on: bool
    adaptive_color_on: bool
    min_brightness_pct: float
    max_brightness_pct: float
    min_color_temp_k: int
    max_color_temp_k: int
    brightness_mode: BrightnessMode
    transition: float
    interval: int

    # Night mode, driven by an external entity rather than a schedule.
    night_source_entity: str | None
    night_behavior: NightBehavior
    night_scene_id: str | None
    night_brightness_pct: float
    night_color_temp_k: int
    night_transition: float
    night_ignore_presence: bool

    # What the first press after the room was switched off should do.
    restore_on_power_cycle: RestoreOnPowerCycle
    resume_max_age_minutes: int

    # Only the occupancy input here; what presence *does* is milestone 5.
    presence_entity: str | None
    presence_clear_delay: int
    presence_covers: tuple[str, ...]
    cover_condition: CoverCondition
    cover_unknown_blocks: bool
    presence_on_action: PresenceOnAction
    presence_on_scene_id: str | None
    presence_on_only_when_off: bool
    presence_off_action: PresenceOffAction
    presence_respects_manual: bool

    # This room's own scenes, in the order they were defined. A scene belongs
    # to exactly one room now: a reading scene for the living room and one for
    # the bedroom are different lists of different lights, and pretending they
    # are one house-wide recipe only ever produced a very long picker.
    scenes: tuple[Scene, ...]
    # Calibration for this room's lights, keyed by entity id. A calibration is
    # about one bulb in one room, so it belongs to the room rather than to a
    # list of its own halfway down the hub page.
    light_profiles: Mapping[str, LightProfile]
    # The switches on this room's walls. A switch drives exactly one room, so
    # it belongs to the room rather than to a flat list that had to name the
    # room in every entry.
    switches: tuple[ControllerConfig, ...]

    # Requirement 4: a window is open, so stop attracting everything outside.
    window_entities: tuple[str, ...]
    insect_action: InsectAction
    insect_scene_id: str | None
    insect_color_temp_k: int
    insect_rgb_color: tuple[int, int, int]
    insect_brightness_pct: float
    insect_only_when_on: bool
    insect_open_delay: int
    insect_close_delay: int
    insect_overridable_by_press: bool

    @property
    def slug(self) -> str:
        """Human-facing id for service calls and logs. Never a stored reference."""
        return slugify(self.name)

    def insect_scene(self, scenes: Mapping[str, Scene]) -> Scene | None:
        """What this room looks like while a window is open.

        Only one of the four answers is a scene; the others are built here so
        "go amber" does not oblige somebody to define a scene they will never
        pick from a menu.
        """
        match self.insect_action:
            case InsectAction.SCENE:
                return scenes.get(self.insect_scene_id or "")
            case InsectAction.TURN_OFF:
                spec = SceneLightSpec(turn_off=True)
            case InsectAction.RGB_COLOR:
                spec = SceneLightSpec(
                    brightness_pct=self.insect_brightness_pct,
                    color={CONF_RGB_COLOR: tuple(self.insect_rgb_color)},
                )
            case _:
                spec = SceneLightSpec(
                    brightness_pct=self.insect_brightness_pct,
                    color={CONF_COLOR_TEMP_KELVIN: self.insect_color_temp_k},
                )
        return Scene(
            scene_id=f"{self.subentry_id}:insect",
            name="Insect mode",
            icon="mdi:bee",
            lights={ALL_LIGHTS: spec},
            zones=frozenset({self.subentry_id}),
        )

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Self:
        raw = {**_ZONE_DEFAULTS, **dict(subentry.data)}
        # Built first because the switches are built from them: what a switch
        # cycles is a question about the room's scenes.
        scenes = tuple(
            zone_scene(entry, subentry.subentry_id)
            for entry in (raw.get(CONF_ZONE_SCENES) or ())
        )
        scene_ids = [scene.scene_id for scene in scenes]
        return cls(
            subentry_id=subentry.subentry_id,
            name=raw.get(CONF_NAME) or subentry.title,
            lights=tuple(raw.get(CONF_LIGHTS) or ()),
            icon=raw[CONF_ICON],
            area_id=raw.get(CONF_AREA_ID),
            all_members_on=bool(raw[CONF_ALL]),
            hide_members=bool(raw[CONF_HIDE_MEMBERS]),
            remember_on_state=bool(raw[CONF_REMEMBER_ON_STATE]),
            brightness_strategy=BrightnessStrategy(raw[CONF_BRIGHTNESS_STRATEGY]),
            expand_light_groups=bool(raw[CONF_EXPAND_LIGHT_GROUPS]),
            color_lights_dark_members=bool(raw[CONF_COLOR_LIGHTS_DARK_MEMBERS]),
            adaptive_override=bool(raw[CONF_ADAPTIVE_OVERRIDE]),
            adaptive_brightness_on=bool(raw[CONF_ADAPTIVE_BRIGHTNESS_ON]),
            adaptive_color_on=bool(raw[CONF_ADAPTIVE_COLOR_ON]),
            min_brightness_pct=float(raw[CONF_MIN_BRIGHTNESS_PCT]),
            max_brightness_pct=float(raw[CONF_MAX_BRIGHTNESS_PCT]),
            min_color_temp_k=int(raw[CONF_MIN_COLOR_TEMP_K]),
            max_color_temp_k=int(raw[CONF_MAX_COLOR_TEMP_K]),
            brightness_mode=BrightnessMode(raw[CONF_BRIGHTNESS_MODE]),
            transition=float(raw[CONF_TRANSITION]),
            interval=int(raw[CONF_INTERVAL]),
            night_source_entity=raw.get(CONF_NIGHT_SOURCE) or None,
            night_behavior=NightBehavior(raw[CONF_NIGHT_BEHAVIOR]),
            night_scene_id=raw.get(CONF_NIGHT_SCENE) or None,
            night_brightness_pct=float(raw[CONF_NIGHT_BRIGHTNESS_PCT]),
            night_color_temp_k=int(raw[CONF_NIGHT_COLOR_TEMP_K]),
            night_transition=float(raw[CONF_NIGHT_TRANSITION]),
            night_ignore_presence=bool(raw[CONF_NIGHT_IGNORE_PRESENCE]),
            scenes=scenes,
            light_profiles={
                entry[CONF_LIGHT_ENTITY]: zone_light_profile(entry)
                for entry in (raw.get(CONF_ZONE_PROFILES) or ())
                if entry.get(CONF_LIGHT_ENTITY)
            },
            switches=tuple(
                zone_switch(entry, subentry.subentry_id, scene_ids)
                for entry in (raw.get(CONF_ZONE_SWITCHES) or ())
            ),
            restore_on_power_cycle=RestoreOnPowerCycle(
                raw[CONF_RESTORE_ON_POWER_CYCLE]
            ),
            resume_max_age_minutes=int(raw[CONF_RESUME_MAX_AGE_MIN]),
            presence_entity=raw.get(CONF_PRESENCE_ENTITY) or None,
            presence_clear_delay=int(raw[CONF_PRESENCE_CLEAR_DELAY]),
            presence_covers=tuple(raw.get(CONF_PRESENCE_COVERS) or ()),
            cover_condition=CoverCondition(raw[CONF_COVER_CONDITION]),
            cover_unknown_blocks=bool(raw[CONF_COVER_UNKNOWN_BLOCKS]),
            presence_on_action=PresenceOnAction(raw[CONF_PRESENCE_ON_ACTION]),
            presence_on_scene_id=raw.get(CONF_PRESENCE_ON_SCENE) or None,
            presence_on_only_when_off=bool(raw[CONF_PRESENCE_ON_ONLY_WHEN_OFF]),
            presence_off_action=PresenceOffAction(raw[CONF_PRESENCE_OFF_ACTION]),
            presence_respects_manual=bool(raw[CONF_PRESENCE_RESPECTS_MANUAL]),
            window_entities=tuple(raw.get(CONF_WINDOW_ENTITIES) or ()),
            insect_action=InsectAction(
                raw.get(CONF_INSECT_ACTION, InsectAction.COLOR_TEMP.value)
            ),
            insect_scene_id=raw.get(CONF_INSECT_SCENE) or None,
            insect_color_temp_k=int(raw.get(CONF_INSECT_COLOR_TEMP_K, 2000)),
            insect_rgb_color=tuple(raw.get(CONF_INSECT_RGB_COLOR) or (255, 140, 40)),
            insect_brightness_pct=float(raw.get(CONF_INSECT_BRIGHTNESS_PCT, 30)),
            insect_only_when_on=bool(raw[CONF_INSECT_ONLY_WHEN_ON]),
            insect_open_delay=int(raw[CONF_INSECT_OPEN_DELAY]),
            insect_close_delay=int(raw[CONF_INSECT_CLOSE_DELAY]),
            insect_overridable_by_press=bool(raw[CONF_INSECT_OVERRIDABLE]),
        )

    # -- layer resolution --------------------------------------------------

    def effective_interval(self, hub: HubConfig) -> int:
        return self.interval if self.adaptive_override else hub.interval

    def effective_transition(self, hub: HubConfig) -> float:
        return self.transition if self.adaptive_override else hub.transition

    def adaptive_config(
        self,
        hub: HubConfig,
        observer: astral.Observer,
        timezone: datetime.tzinfo,
    ) -> AdaptiveConfig:
        """Layer the zone's overrides onto the hub defaults.

        A zone that has not opted in carries the hub's curve wholesale, so
        changing a global default reaches every zone that did not ask to differ.
        """
        override = self.adaptive_override
        return AdaptiveConfig(
            observer=observer,
            timezone=timezone,
            brightness_mode=(self.brightness_mode if override else hub.brightness_mode),
            min_brightness_pct=(
                self.min_brightness_pct if override else hub.min_brightness_pct
            ),
            max_brightness_pct=(
                self.max_brightness_pct if override else hub.max_brightness_pct
            ),
            min_color_temp_k=(
                self.min_color_temp_k if override else hub.min_color_temp_k
            ),
            max_color_temp_k=(
                self.max_color_temp_k if override else hub.max_color_temp_k
            ),
            # Night values are always the zone's own: a bedroom and a hallway
            # rarely want the same thing after dark, and the hub value is only
            # the seed the zone form was pre-filled with.
            night_brightness_pct=self.night_brightness_pct,
            night_color_temp_k=self.night_color_temp_k,
            time_dark=datetime.timedelta(seconds=hub.time_dark),
            time_light=datetime.timedelta(seconds=hub.time_light),
            sunrise_offset=datetime.timedelta(seconds=hub.sunrise_offset),
            sunset_offset=datetime.timedelta(seconds=hub.sunset_offset),
        )


@dataclass(frozen=True, slots=True)
class LightProfileConfig:
    """A per-light calibration subentry, bound to one light entity."""

    subentry_id: str
    light_entity: str
    profile: LightProfile

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Self:
        raw = {**_PROFILE_DEFAULTS, **dict(subentry.data)}
        return cls(
            subentry_id=subentry.subentry_id,
            light_entity=raw[CONF_LIGHT_ENTITY],
            profile=LightProfile(
                enabled=bool(raw[CONF_ENABLED]),
                min_brightness_pct=float(raw[CONF_MIN_BRIGHTNESS_PCT]),
                max_brightness_pct=float(raw[CONF_MAX_BRIGHTNESS_PCT]),
                brightness_offset_pct=float(raw[CONF_BRIGHTNESS_OFFSET_PCT]),
                brightness_multiplier=float(raw[CONF_BRIGHTNESS_MULTIPLIER]),
                min_color_temp_k=int(raw[CONF_MIN_COLOR_TEMP_K]),
                max_color_temp_k=int(raw[CONF_MAX_COLOR_TEMP_K]),
                color_temp_offset_k=int(raw[CONF_COLOR_TEMP_OFFSET_K]),
                adapt_brightness=bool(raw[CONF_ADAPT_BRIGHTNESS]),
                adapt_color=bool(raw[CONF_ADAPT_COLOR]),
                prefer_rgb=bool(raw[CONF_PREFER_RGB_COLOR]),
                clamp_to_device_limits=bool(raw[CONF_CLAMP_TO_DEVICE]),
            ),
        )


@dataclass(frozen=True, slots=True)
class SceneConfig:
    """A scene subentry, and the runtime :class:`Scene` it describes."""

    subentry_id: str
    name: str
    scene: Scene

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Self:
        raw = {**_SCENE_DEFAULTS, **dict(subentry.data)}
        name = raw.get(CONF_NAME) or subentry.title
        override = SceneOverride(raw[CONF_OVERRIDE_MODE])

        # Exactly one colour format is stored, chosen in step two of the flow.
        color: dict[str, Any] | None = None
        match raw.get(CONF_COLOR_FORMAT):
            case fmt if fmt in (None, COLOR_FORMAT_NONE):
                color = None
            case _ if (value := raw.get(CONF_COLOR_TEMP_KELVIN)) is not None:
                color = {CONF_COLOR_TEMP_KELVIN: int(value)}
            case _ if (value := raw.get(CONF_RGB_COLOR)) is not None:
                color = {CONF_RGB_COLOR: tuple(value)}
            case _ if (value := raw.get(CONF_COLOR_NAME)) is not None:
                color = {CONF_COLOR_NAME: str(value)}

        # A scene that overrides only brightness carries no colour at all, so a
        # stale value left behind by an edit cannot leak back into the render.
        if override in (SceneOverride.BRIGHTNESS, SceneOverride.NEITHER):
            color = None
        brightness = (
            float(raw[CONF_BRIGHTNESS_PCT])
            if override in (SceneOverride.BRIGHTNESS, SceneOverride.BOTH)
            else None
        )

        return cls(
            subentry_id=subentry.subentry_id,
            name=name,
            scene=Scene(
                lights=_scene_lights(raw.get(CONF_SCENE_LIGHTS) or {}),
                scene_id=subentry.subentry_id,
                name=name,
                icon=raw[CONF_ICON],
                override=override,
                brightness_pct=brightness,
                color=color,
                on_lights_only=bool(raw[CONF_ON_LIGHTS_ONLY]),
                zones=frozenset(raw.get(CONF_SCENE_ZONES) or ()),
                ignore_presence=bool(raw[CONF_IGNORE_PRESENCE]),
                others=OthersPolicy(raw[CONF_OTHERS]),
                transition=float(raw[CONF_TRANSITION]),
                on_unsupported_color=UnsupportedColorPolicy(
                    raw[CONF_ON_UNSUPPORTED_COLOR]
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    """One light switch, with its own ordered list of scenes."""

    subentry_id: str
    name: str
    zone_id: str
    binding_type: BindingType
    binding_entity: str | None
    is_default: bool
    scene_order: tuple[str, ...]
    adaptive_position: AdaptivePosition
    off_at_end: bool
    wrap_around: bool
    on_foreign: ForeignPolicy
    press_states: frozenset[str]
    double_press_states: frozenset[str]
    long_press_states: frozenset[str]
    press_attribute: str
    double_press_action: PressAction
    long_press_action: PressAction
    # The lower half of a rocker. Empty state sets mean the switch has only
    # one button, which is the common case and stays the default.
    down_press_states: frozenset[str] = frozenset()
    down_double_press_states: frozenset[str] = frozenset()
    down_long_press_states: frozenset[str] = frozenset()
    down_press_action: PressAction = PressAction.ZONE_OFF
    down_double_press_action: PressAction = PressAction.NONE
    down_long_press_action: PressAction = PressAction.DIM
    dim_step_pct: float = 10.0
    # For buttons that have no double press of their own and simply publish
    # the same single press twice. Each half of a rocker decides for itself.
    double_from_two_presses: bool = False
    double_press_window_ms: int = 400
    down_double_from_two_presses: bool = False
    down_double_press_window_ms: int = 400
    # For a switch whose words we have none of: any change is a press.
    any_change_is_a_press: bool = False
    # Holding: keep going until the finger comes off, rather than moving one
    # step and stopping.
    hold_ramp: bool = True
    hold_interval_ms: int = 400
    release_states: frozenset[str] = frozenset()

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def cycle(self, known_scene_ids: frozenset[str] | None = None) -> CycleConfig:
        """This controller's expanded cycle.

        Scenes that have since been deleted are dropped rather than left as
        dead positions, so a controller whose scene was removed simply has a
        shorter cycle instead of a press that does nothing.
        """
        scene_ids = tuple(
            scene_id
            for scene_id in self.scene_order
            if known_scene_ids is None or scene_id in known_scene_ids
        )
        return build_cycle(
            scene_ids,
            adaptive_position=self.adaptive_position,
            off_at_end=self.off_at_end,
            on_foreign=self.on_foreign,
            wrap=self.wrap_around,
        )

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Self:
        raw = {**_CONTROLLER_DEFAULTS, **dict(subentry.data)}
        return cls.from_mapping(
            raw,
            subentry_id=subentry.subentry_id,
            zone_id=raw.get(CONF_ZONE_ID) or "",
            fallback_name=subentry.title,
        )

    @classmethod
    def from_mapping(
        cls,
        raw: dict[str, Any],
        *,
        subentry_id: str,
        zone_id: str,
        fallback_name: str = "Switch",
    ) -> Self:
        return cls(
            subentry_id=subentry_id,
            name=raw.get(CONF_NAME) or fallback_name,
            zone_id=zone_id,
            binding_type=BindingType(raw[CONF_BINDING_TYPE]),
            binding_entity=raw.get(CONF_BINDING_ENTITY) or None,
            is_default=bool(raw[CONF_IS_DEFAULT]),
            scene_order=tuple(raw.get(CONF_SCENE_ORDER) or ()),
            adaptive_position=AdaptivePosition(raw[CONF_ADAPTIVE_POSITION]),
            off_at_end=bool(raw[CONF_OFF_AT_END]),
            wrap_around=bool(raw[CONF_WRAP_AROUND]),
            on_foreign=ForeignPolicy(raw[CONF_ON_FOREIGN]),
            press_states=frozenset(raw[CONF_PRESS_STATES]),
            double_press_states=frozenset(raw[CONF_DOUBLE_PRESS_STATES]),
            long_press_states=frozenset(raw[CONF_LONG_PRESS_STATES]),
            press_attribute=raw[CONF_PRESS_ATTRIBUTE],
            double_press_action=PressAction(raw[CONF_DOUBLE_PRESS_ACTION]),
            long_press_action=PressAction(raw[CONF_LONG_PRESS_ACTION]),
            down_press_states=frozenset(raw.get(CONF_DOWN_PRESS_STATES) or ()),
            down_double_press_states=frozenset(
                raw.get(CONF_DOWN_DOUBLE_PRESS_STATES) or ()
            ),
            down_long_press_states=frozenset(
                raw.get(CONF_DOWN_LONG_PRESS_STATES) or ()
            ),
            down_press_action=PressAction(
                raw.get(CONF_DOWN_PRESS_ACTION, PressAction.ZONE_OFF.value)
            ),
            down_double_press_action=PressAction(
                raw.get(CONF_DOWN_DOUBLE_PRESS_ACTION, PressAction.NONE.value)
            ),
            down_long_press_action=PressAction(
                raw.get(CONF_DOWN_LONG_PRESS_ACTION, PressAction.DIM.value)
            ),
            dim_step_pct=float(raw.get(CONF_DIM_STEP_PCT, 10)),
            double_from_two_presses=bool(raw.get(CONF_DOUBLE_FROM_PRESSES, False)),
            double_press_window_ms=int(raw.get(CONF_DOUBLE_PRESS_WINDOW_MS, 400)),
            down_double_from_two_presses=bool(
                raw.get(CONF_DOWN_DOUBLE_FROM_PRESSES, False)
            ),
            down_double_press_window_ms=int(
                raw.get(CONF_DOWN_DOUBLE_PRESS_WINDOW_MS, 400)
            ),
            any_change_is_a_press=bool(raw.get(CONF_ANY_CHANGE_IS_PRESS, False)),
            hold_ramp=bool(raw.get(CONF_HOLD_RAMP, True)),
            hold_interval_ms=int(raw.get(CONF_HOLD_INTERVAL_MS, 400)),
            release_states=frozenset(raw.get(CONF_RELEASE_STATES) or ()),
        )


def synthetic_controller(
    zone: ZoneConfig, scene_ids: tuple[str, ...]
) -> ControllerConfig:
    """The controller a zone gets when the user has not configured one.

    Its list is adaptive followed by every scene, so a plain wall switch wired
    straight to the zone's light entity cycles the room with no configuration
    at all. Adding a real controller replaces it and takes over the ordering.
    """
    return ControllerConfig(
        subentry_id=f"{zone.subentry_id}:default",
        name=f"{zone.name} (default)",
        zone_id=zone.subentry_id,
        binding_type=BindingType.ZONE_LIGHT,
        binding_entity=None,
        is_default=True,
        scene_order=scene_ids,
        adaptive_position=AdaptivePosition.FIRST,
        off_at_end=False,
        wrap_around=True,
        on_foreign=ForeignPolicy.RESTART,
        press_states=frozenset(),
        double_press_states=frozenset(),
        long_press_states=frozenset(),
        press_attribute="",
        double_press_action=PressAction.CYCLE_PREVIOUS,
        long_press_action=PressAction.RESET_ADAPTIVE,
    )


def _rule_zones(stored: Any) -> frozenset[str]:
    """The rooms a rule governs.

    Stored as a single room now, but rules written when one rule could span
    several are still read as they were meant.
    """
    if not stored:
        return frozenset()
    if isinstance(stored, str):
        return frozenset({stored})
    return frozenset(stored)


def _entry_action(raw: dict[str, Any]) -> ZoneAction:
    """What a room does when somebody walks back into it mid-session.

    Rules written before this was a choice carry only a scene, and meant
    "apply it", so a stored scene with no stored action still means that.
    """
    stored = raw.get(CONF_RULE_ENTRY_ACTION)
    if stored:
        return ZoneAction(stored)
    return ZoneAction.APPLY_SCENE if raw.get(CONF_RULE_ENTRY_SCENE) else ZoneAction.KEEP


@dataclass(frozen=True, slots=True)
class ModeRule:
    """What one cross-zone mode does to one room, in one or more of its states."""

    states: frozenset[str]
    zones: frozenset[str]
    action: ZoneAction = ZoneAction.KEEP
    scene_id: str | None = None
    # Requirement 2: a room with somebody in it is not plunged into darkness.
    respect_presence: bool = True
    defer_if_occupied: bool = True
    # What somebody walking in mid-session gets.
    presence_entry_action: ZoneAction = ZoneAction.KEEP
    presence_entry_scene: str | None = None
    on_free_action: str = "turn_off"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ModeRule:
        return cls(
            states=frozenset(raw.get(CONF_RULE_STATES) or ()),
            zones=_rule_zones(raw.get(CONF_RULE_ZONES)),
            action=ZoneAction(raw.get(CONF_RULE_ACTION, ZoneAction.KEEP.value)),
            scene_id=raw.get(CONF_RULE_SCENE) or None,
            respect_presence=bool(raw.get(CONF_RULE_RESPECT_PRESENCE, True)),
            defer_if_occupied=bool(raw.get(CONF_RULE_DEFER_IF_OCCUPIED, True)),
            presence_entry_action=_entry_action(raw),
            presence_entry_scene=raw.get(CONF_RULE_ENTRY_SCENE) or None,
            on_free_action=raw.get(CONF_RULE_ON_FREE, "turn_off"),
        )


@dataclass(frozen=True, slots=True)
class ModeConfig:
    """A named group of rooms with named states -- Home Cinema and its like."""

    subentry_id: str
    name: str
    icon: str
    states: tuple[str, ...]
    snapshot_on_enter: bool
    restore_mode: RestoreMode
    opted_out_on_exit: OptedOutOnExit
    deferred_ttl_minutes: int
    rules: tuple[ModeRule, ...]

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def zone_ids(self) -> frozenset[str]:
        """Every room any rule mentions."""
        return (
            frozenset().union(*(rule.zones for rule in self.rules))
            if self.rules
            else frozenset()
        )

    def rule_for(self, state: str, zone_id: str) -> ModeRule | None:
        """The rule governing one room in one state.

        Later rules win, so a broad rule can be written first and a specific
        exception added after it without reordering anything.
        """
        found = None
        for rule in self.rules:
            if state in rule.states and zone_id in rule.zones:
                found = rule
        return found

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Self:
        raw = {**_MODE_DEFAULTS, **dict(subentry.data)}
        return cls(
            subentry_id=subentry.subentry_id,
            name=raw.get(CONF_NAME) or subentry.title,
            icon=raw[CONF_ICON],
            states=tuple(raw.get(CONF_STATES) or ()),
            snapshot_on_enter=bool(raw[CONF_SNAPSHOT_ON_ENTER]),
            restore_mode=RestoreMode(raw[CONF_RESTORE_MODE]),
            opted_out_on_exit=OptedOutOnExit(raw[CONF_OPTED_OUT_ON_EXIT]),
            deferred_ttl_minutes=int(raw[CONF_DEFERRED_TTL_MIN]),
            rules=tuple(
                ModeRule.from_dict(rule) for rule in (raw.get(CONF_RULES) or ())
            ),
        )


def _scene_light_color(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One light's colour inside a scene.

    ``None`` means "use the scene's own colour"; an empty mapping means "leave
    this light's colour to the sun", which is how a desk lamp takes a scene's
    brightness while still warming through the evening.
    """
    match raw.get(CONF_COLOR_FORMAT, COLOR_FORMAT_INHERIT):
        case const_format if const_format == COLOR_FORMAT_INHERIT:
            return None
        case const_format if const_format == COLOR_FORMAT_NONE:
            return {}
        case const_format if const_format == CONF_COLOR_TEMP_KELVIN:
            return {CONF_COLOR_TEMP_KELVIN: int(raw[CONF_COLOR_TEMP_KELVIN])}
        case const_format if const_format == CONF_RGB_COLOR:
            return {CONF_RGB_COLOR: tuple(raw[CONF_RGB_COLOR])}
        case const_format if const_format == COLOR_FORMAT_RGB_WHITE:
            red, green, blue = tuple(raw[CONF_RGB_COLOR])
            # An RGBWW fixture takes five channels. Sending them explicitly is
            # what lets a strip sit against a warm wooden floor without the
            # white LEDs washing the colour out.
            return {
                "rgbww_color": (
                    red,
                    green,
                    blue,
                    int(raw.get(CONF_WARM_WHITE, 0)),
                    int(raw.get(CONF_COLD_WHITE, 0)),
                )
            }
    return None


def effective_scene_order(
    stored: Iterable[str], excluded: Iterable[str], scene_ids: Sequence[str]
) -> tuple[str, ...]:
    """Which of the room's scenes a switch cycles, in order.

    A switch cycles the whole room unless it has been told otherwise. That is
    the only defensible reading of an empty list: a switch nobody has
    configured should cycle the scenes that exist, not sit there doing nothing
    but adaptive -- which is what an empty order used to mean, and the
    commonest way this integration looked broken.

    So the stored order says what comes first, ``excluded`` remembers what was
    deliberately taken out, and anything the room has gained since joins the
    end. Scenes that no longer exist drop out of both.
    """
    known = set(scene_ids)
    order = [scene_id for scene_id in stored if scene_id in known]
    dropped = set(excluded)
    order.extend(
        scene_id
        for scene_id in scene_ids
        if scene_id not in order and scene_id not in dropped
    )
    return tuple(order)


def zone_switch(
    raw: dict[str, Any], zone_id: str, scene_ids: Sequence[str] = ()
) -> ControllerConfig:
    """One of a room's switches, as stored inside the room."""
    merged = {**_CONTROLLER_DEFAULTS, **raw}
    merged[CONF_SCENE_ORDER] = list(
        effective_scene_order(
            merged.get(CONF_SCENE_ORDER) or (),
            merged.get(CONF_SCENE_ORDER_EXCLUDED) or (),
            scene_ids,
        )
    )
    return ControllerConfig.from_mapping(
        merged,
        subentry_id=str(raw.get(CONF_SWITCH_ID) or ""),
        zone_id=zone_id,
    )


def zone_light_profile(raw: dict[str, Any]) -> LightProfile:
    """One light's calibration, as stored inside its room."""
    merged = {**_PROFILE_DEFAULTS, **raw}
    return LightProfile(
        enabled=bool(merged[CONF_ENABLED]),
        min_brightness_pct=float(merged[CONF_MIN_BRIGHTNESS_PCT]),
        max_brightness_pct=float(merged[CONF_MAX_BRIGHTNESS_PCT]),
        brightness_offset_pct=float(merged[CONF_BRIGHTNESS_OFFSET_PCT]),
        brightness_multiplier=float(merged[CONF_BRIGHTNESS_MULTIPLIER]),
        min_color_temp_k=int(merged[CONF_MIN_COLOR_TEMP_K]),
        max_color_temp_k=int(merged[CONF_MAX_COLOR_TEMP_K]),
        color_temp_offset_k=int(merged[CONF_COLOR_TEMP_OFFSET_K]),
        adapt_brightness=bool(merged[CONF_ADAPT_BRIGHTNESS]),
        adapt_color=bool(merged[CONF_ADAPT_COLOR]),
        prefer_rgb=bool(merged[CONF_PREFER_RGB_COLOR]),
        clamp_to_device_limits=bool(merged[CONF_CLAMP_TO_DEVICE]),
    )


def zone_scene(raw: dict[str, Any], zone_id: str) -> Scene:
    """One of a room's own scenes.

    Carries no brightness or colour of its own -- every value lives on a light.
    Which axes the scene takes over therefore follows from what each light
    actually names, so "7% and orange here, 20% but keep the sun's colour
    there" is one scene rather than an impossibility.
    """
    return Scene(
        scene_id=str(raw.get(CONF_SCENE_ID) or ""),
        name=str(raw.get(CONF_NAME) or ""),
        icon=str(raw.get(CONF_ICON) or "mdi:palette"),
        lights=_scene_lights(raw.get(CONF_SCENE_LIGHTS) or {}),
        on_lights_only=bool(raw.get(CONF_ON_LIGHTS_ONLY, False)),
        zones=frozenset({zone_id}),
        others=OthersPolicy(raw.get(CONF_OTHERS, OthersPolicy.ADAPTIVE.value)),
        ignore_presence=bool(raw.get(CONF_IGNORE_PRESENCE, False)),
        transition=(
            float(raw[CONF_TRANSITION])
            if raw.get(CONF_TRANSITION) is not None
            else None
        ),
        on_unsupported_color=UnsupportedColorPolicy(
            raw.get(CONF_ON_UNSUPPORTED_COLOR, UnsupportedColorPolicy.ADAPTIVE.value)
        ),
    )


def _scene_lights(raw: dict[str, Any]) -> dict[str, SceneLightSpec]:
    """Parse a scene's per-light entries."""
    lights: dict[str, SceneLightSpec] = {}
    for entity_id, entry in raw.items():
        action = SceneLightAction(entry.get(CONF_LIGHT_ACTION, "apply"))
        brightness = entry.get(CONF_BRIGHTNESS_PCT)
        lights[entity_id] = SceneLightSpec(
            brightness_pct=float(brightness) if brightness is not None else None,
            color=_scene_light_color(entry),
            turn_off=action is SceneLightAction.OFF,
            skip=action is SceneLightAction.LEAVE,
        )
    return lights
