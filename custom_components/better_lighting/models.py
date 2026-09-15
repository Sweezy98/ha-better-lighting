"""Typed configuration objects parsed from the hub entry and its subentries.

The config flow stores plain JSON; these dataclasses are the typed view the
runtime works with, so a missing or stale key is defaulted in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from homeassistant.util import slugify

from .brightness import BrightnessStrategy
from .const import (
    CONF_ALL,
    CONF_AREA_ID,
    CONF_AUTORESET_MANUAL_S,
    CONF_BRIGHTNESS_MODE,
    CONF_BRIGHTNESS_STRATEGY,
    CONF_EXPAND_LIGHT_GROUPS,
    CONF_HIDE_MEMBERS,
    CONF_ICON,
    CONF_INITIAL_TRANSITION,
    CONF_INTERCEPT_MEMBER_CALLS,
    CONF_INTERVAL,
    CONF_LIGHTS,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_K,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_K,
    CONF_NAME,
    CONF_NIGHT_BRIGHTNESS_PCT,
    CONF_NIGHT_COLOR_TEMP_K,
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
    ZONE_SPECS,
    BrightnessMode,
    defaults_for,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

_HUB_DEFAULTS = defaults_for(HUB_SPECS)
_ZONE_DEFAULTS = defaults_for(ZONE_SPECS)


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
    """One room: its lights and how its group entity behaves."""

    subentry_id: str
    name: str
    lights: tuple[str, ...]
    icon: str
    area_id: str | None
    all_members_on: bool
    hide_members: bool
    remember_on_state: bool
    brightness_strategy: BrightnessStrategy
    expand_light_groups: bool

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
        )
