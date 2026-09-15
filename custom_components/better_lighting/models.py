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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import astral
from homeassistant.util import slugify

from .adaptive import AdaptiveConfig
from .brightness import BrightnessStrategy
from .const import (
    CONF_ADAPT_BRIGHTNESS,
    CONF_ADAPT_COLOR,
    CONF_ADAPTIVE_DEFAULT_ON,
    CONF_ADAPTIVE_OVERRIDE,
    CONF_ALL,
    CONF_AREA_ID,
    CONF_AUTORESET_MANUAL_S,
    CONF_BRIGHTNESS_MODE,
    CONF_BRIGHTNESS_MULTIPLIER,
    CONF_BRIGHTNESS_OFFSET_PCT,
    CONF_BRIGHTNESS_STRATEGY,
    CONF_CLAMP_TO_DEVICE,
    CONF_COLOR_TEMP_OFFSET_K,
    CONF_ENABLED,
    CONF_EXPAND_LIGHT_GROUPS,
    CONF_HIDE_MEMBERS,
    CONF_ICON,
    CONF_INITIAL_TRANSITION,
    CONF_INTERCEPT_MEMBER_CALLS,
    CONF_INTERVAL,
    CONF_LIGHT_ENTITY,
    CONF_LIGHTS,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_K,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_K,
    CONF_NAME,
    CONF_NIGHT_BEHAVIOR,
    CONF_NIGHT_BRIGHTNESS_PCT,
    CONF_NIGHT_COLOR_TEMP_K,
    CONF_NIGHT_IGNORE_PRESENCE,
    CONF_NIGHT_SOURCE,
    CONF_NIGHT_TRANSITION,
    CONF_PREFER_RGB_COLOR,
    CONF_REMEMBER_ON_STATE,
    CONF_SCENE_TRANSITION,
    CONF_SEND_SPLIT_DELAY_MS,
    CONF_SEPARATE_TURN_ON,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_TAKE_OVER_CONTROL,
    CONF_TIME_DARK,
    CONF_TIME_LIGHT,
    CONF_TRANSITION,
    HUB_SPECS,
    LIGHT_PROFILE_SPECS,
    ZONE_SPECS,
    BrightnessMode,
    NightBehavior,
    defaults_for,
)
from .profiles import LightProfile

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

_HUB_DEFAULTS = defaults_for(HUB_SPECS)
_ZONE_DEFAULTS = defaults_for(ZONE_SPECS)
_PROFILE_DEFAULTS = defaults_for(LIGHT_PROFILE_SPECS)


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

    # Adaptive. When `adaptive_override` is False every value below is ignored
    # and the hub default is used instead.
    adaptive_override: bool
    adaptive_default_on: bool
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
    night_brightness_pct: float
    night_color_temp_k: int
    night_transition: float
    night_ignore_presence: bool

    @property
    def slug(self) -> str:
        """Human-facing id for service calls and logs. Never a stored reference."""
        return slugify(self.name)

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Self:
        raw = {**_ZONE_DEFAULTS, **dict(subentry.data)}
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
            adaptive_override=bool(raw[CONF_ADAPTIVE_OVERRIDE]),
            adaptive_default_on=bool(raw[CONF_ADAPTIVE_DEFAULT_ON]),
            min_brightness_pct=float(raw[CONF_MIN_BRIGHTNESS_PCT]),
            max_brightness_pct=float(raw[CONF_MAX_BRIGHTNESS_PCT]),
            min_color_temp_k=int(raw[CONF_MIN_COLOR_TEMP_K]),
            max_color_temp_k=int(raw[CONF_MAX_COLOR_TEMP_K]),
            brightness_mode=BrightnessMode(raw[CONF_BRIGHTNESS_MODE]),
            transition=float(raw[CONF_TRANSITION]),
            interval=int(raw[CONF_INTERVAL]),
            night_source_entity=raw.get(CONF_NIGHT_SOURCE) or None,
            night_behavior=NightBehavior(raw[CONF_NIGHT_BEHAVIOR]),
            night_brightness_pct=float(raw[CONF_NIGHT_BRIGHTNESS_PCT]),
            night_color_temp_k=int(raw[CONF_NIGHT_COLOR_TEMP_K]),
            night_transition=float(raw[CONF_NIGHT_TRANSITION]),
            night_ignore_presence=bool(raw[CONF_NIGHT_IGNORE_PRESENCE]),
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
