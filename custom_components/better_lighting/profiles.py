"""Turn an adaptive curve value into a concrete target for one specific light.

This is where requirement 9 lives: per-light minimum and maximum brightness and
colour temperature, plus offsets so a bulb that reads dim or cold can be matched
to its neighbours.

The order of the six steps below is deliberate and load-bearing:

    1. the curve value for the room
    2. apply the light's OFFSET (and multiplier)
    3. clamp to the light's configured min/max
    4. quantise (percent -> 0..255, Kelvin -> nearest 5 K)
    5. clamp to what the device physically supports
    6. drop axes the device cannot do at all

Offset comes *before* clamp on purpose. The min/max expresses an operating limit
on the fixture -- "never below 15 %, it flickers", "never above 70 %, it is over
the bed" -- while the offset expresses a calibration -- "this one reads low". A
calibration must never be able to breach a limit the user set explicitly, which
is exactly what the reverse order would allow.

A corollary worth stating in the UI: per-light min/max are absolute targets, not
deltas from the room's range. With a room value of 15 %, an offset of +10 and a
minimum of 30, the result is 30 % -- not 25 %.

Pure: imports nothing from ``homeassistant`` except the colour conversions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntFlag, auto
from typing import Any

from homeassistant.util.color import color_temperature_to_rgb

from .adaptive import AdaptiveSettings, from_mired, to_mired
from .util import clamp

# Attribute names, kept local so this module stays free of HA imports.
ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
ATTR_HS_COLOR = "hs_color"
ATTR_RGB_COLOR = "rgb_color"
ATTR_RGBW_COLOR = "rgbw_color"
ATTR_RGBWW_COLOR = "rgbww_color"
ATTR_XY_COLOR = "xy_color"
ATTR_WHITE = "white"

BRIGHTNESS_MIN = 1
BRIGHTNESS_MAX = 255

DEFAULT_MIN_KELVIN = 2000
DEFAULT_MAX_KELVIN = 6535

# Colour modes that carry a chromatic value, in the order we would rather use.
CHROMATIC_MODES = ("rgbww", "rgbw", "rgb", "hs", "xy")
_MODE_TO_ATTR = {
    "rgbww": ATTR_RGBWW_COLOR,
    "rgbw": ATTR_RGBW_COLOR,
    "rgb": ATTR_RGB_COLOR,
    "hs": ATTR_HS_COLOR,
    "xy": ATTR_XY_COLOR,
}


class Axis(IntFlag):
    """Which of a light's two controllable dimensions something owns.

    The render pipeline is built entirely on masking these, so that a scene
    overriding only brightness leaves colour free to keep tracking the sun.
    """

    NONE = 0
    BRIGHTNESS = auto()
    COLOR = auto()
    ALL = BRIGHTNESS | COLOR


class Saturation(IntFlag):
    """Records a value that had to be clipped, so it can be surfaced.

    Silently clamping is the single biggest source of "I set +500 K and nothing
    happened" confusion, so the clipping is reported in entity attributes and
    diagnostics rather than swallowed.
    """

    NONE = 0
    BRIGHTNESS_LOW = auto()
    BRIGHTNESS_HIGH = auto()
    CT_LOW = auto()
    CT_HIGH = auto()


@dataclass(frozen=True, slots=True)
class LightProfile:
    """Per-light calibration and limits. All fields optional; defaults are no-ops."""

    enabled: bool = True
    min_brightness_pct: float | None = None
    max_brightness_pct: float | None = None
    brightness_offset_pct: float = 0.0
    brightness_multiplier: float = 1.0
    min_color_temp_k: int | None = None
    max_color_temp_k: int | None = None
    color_temp_offset_k: int = 0
    adapt_brightness: bool = True
    adapt_color: bool = True
    prefer_rgb: bool = False
    clamp_to_device_limits: bool = True

    @property
    def is_identity(self) -> bool:
        """True when this profile would not change anything."""
        return (
            self.enabled
            and self.min_brightness_pct is None
            and self.max_brightness_pct is None
            and self.brightness_offset_pct == 0.0
            and self.brightness_multiplier == 1.0
            and self.min_color_temp_k is None
            and self.max_color_temp_k is None
            and self.color_temp_offset_k == 0
            and self.adapt_brightness
            and self.adapt_color
            and not self.prefer_rgb
        )


IDENTITY_PROFILE = LightProfile()


@dataclass(frozen=True, slots=True)
class LightCapabilities:
    """What a light can actually do, read from its reported state attributes."""

    entity_id: str
    supports_brightness: bool = False
    supports_color_temp: bool = False
    supports_color: bool = False
    supports_white: bool = False
    supports_transition: bool = False
    color_modes: frozenset[str] = frozenset()
    min_color_temp_kelvin: int = DEFAULT_MIN_KELVIN
    max_color_temp_kelvin: int = DEFAULT_MAX_KELVIN

    @classmethod
    def from_attributes(
        cls,
        entity_id: str,
        attributes: Mapping[str, Any],
        *,
        supports_transition: bool = False,
    ) -> LightCapabilities:
        """Derive capabilities from a light's state attributes.

        ``min``/``max_color_temp_kelvin`` are read defensively: some
        integrations advertise colour-temperature support without publishing a
        range, and indexing those directly is a known crash in Adaptive Lighting.
        """
        modes = frozenset(attributes.get("supported_color_modes") or ())
        return cls(
            entity_id=entity_id,
            supports_brightness=bool(modes - {"onoff", "unknown"}),
            supports_color_temp="color_temp" in modes,
            supports_color=bool(modes & set(CHROMATIC_MODES)),
            supports_white="white" in modes,
            supports_transition=supports_transition,
            color_modes=modes,
            min_color_temp_kelvin=int(
                attributes.get("min_color_temp_kelvin") or DEFAULT_MIN_KELVIN
            ),
            max_color_temp_kelvin=int(
                attributes.get("max_color_temp_kelvin") or DEFAULT_MAX_KELVIN
            ),
        )


@dataclass(frozen=True, slots=True)
class LightTarget:
    """A concrete instruction for one light."""

    entity_id: str
    brightness: int | None = None
    color: dict[str, Any] | None = None
    axes: Axis = Axis.NONE
    saturation: Saturation = Saturation.NONE

    @property
    def is_empty(self) -> bool:
        return self.brightness is None and not self.color

    def as_service_data(self) -> dict[str, Any]:
        """The payload for ``light.turn_on``, without the entity id."""
        data: dict[str, Any] = {}
        if self.brightness is not None:
            data[ATTR_BRIGHTNESS] = self.brightness
        if self.color:
            data |= self.color
        return data


def chromatic_payload(
    rgb: tuple[float, float, float], caps: LightCapabilities
) -> dict[str, Any] | None:
    """Express an RGB colour in the richest mode this light understands."""
    red, green, blue = (round(channel) for channel in rgb)
    for mode in CHROMATIC_MODES:
        if mode not in caps.color_modes:
            continue
        match mode:
            case "rgb":
                return {ATTR_RGB_COLOR: (red, green, blue)}
            case "rgbw":
                return {ATTR_RGBW_COLOR: (red, green, blue, 0)}
            case "rgbww":
                return {ATTR_RGBWW_COLOR: (red, green, blue, 0, 0)}
            case "hs" | "xy":
                # Both are derived from RGB by the caller-facing helpers in
                # `adaptive`; converting here would duplicate that maths, so
                # fall through to RGB, which every chromatic light accepts.
                return {ATTR_RGB_COLOR: (red, green, blue)}
    return None


def brightness_from_pct(
    pct: float,
    profile: LightProfile,
    caps: LightCapabilities,
    *,
    bias_pct: float = 0.0,
    respect_adapt_flag: bool = True,
) -> tuple[int | None, Saturation]:
    """Steps 2-5 for an explicit brightness percentage.

    Shared by the adaptive curve and by a scene's own brightness, so a light's
    calibration and limits apply to both. A fixture that reads dim reads dim
    whoever asked, and a maximum the user set to protect their eyes must hold
    for a scene just as firmly as for the curve.

    ``respect_adapt_flag`` is False for a scene: switching off "adapt
    brightness" for a light means "do not track the sun", not "ignore any
    brightness I ever ask for".
    """
    if not caps.supports_brightness:
        return None, Saturation.NONE
    if respect_adapt_flag and not profile.adapt_brightness:
        return None, Saturation.NONE

    # 2. calibration: scale, then offset, then the room-wide relative dim.
    pct = pct * profile.brightness_multiplier + profile.brightness_offset_pct + bias_pct

    # 3. the fixture's configured operating limits.
    low = profile.min_brightness_pct if profile.min_brightness_pct is not None else 0.0
    high = (
        profile.max_brightness_pct if profile.max_brightness_pct is not None else 100.0
    )
    saturation = Saturation.NONE
    if pct < low:
        saturation |= Saturation.BRIGHTNESS_LOW
    if pct > high:
        saturation |= Saturation.BRIGHTNESS_HIGH
    pct = clamp(pct, low, high)

    # 4 & 5. quantise once, then keep it inside the protocol's range. Never 0:
    # many integrations read that as "off".
    brightness = int(
        clamp(round(BRIGHTNESS_MAX * pct / 100.0), BRIGHTNESS_MIN, BRIGHTNESS_MAX)
    )
    return brightness, saturation


def resolve_brightness(
    settings: AdaptiveSettings,
    profile: LightProfile,
    caps: LightCapabilities,
    *,
    bias_pct: float = 0.0,
) -> tuple[int | None, Saturation]:
    """The brightness axis of the adaptive curve, for one light."""
    return brightness_from_pct(
        settings.brightness_pct, profile, caps, bias_pct=bias_pct
    )


def resolve_color(
    settings: AdaptiveSettings,
    profile: LightProfile,
    caps: LightCapabilities,
) -> tuple[dict[str, Any] | None, Saturation]:
    """Steps 1-6 for the colour axis."""
    if not profile.adapt_color:
        return None, Saturation.NONE

    # 2. calibration, in Kelvin because that is how the UI asks for it.
    kelvin = float(settings.color_temp_kelvin) + profile.color_temp_offset_k

    # 3. configured limits.
    low = profile.min_color_temp_k if profile.min_color_temp_k is not None else 0
    high = (
        profile.max_color_temp_k if profile.max_color_temp_k is not None else 1_000_000
    )
    saturation = Saturation.NONE
    if kelvin < low:
        saturation |= Saturation.CT_LOW
    if kelvin > high:
        saturation |= Saturation.CT_HIGH
    kelvin = clamp(kelvin, low, high)

    use_color_temp = caps.supports_color_temp and not (
        profile.prefer_rgb and caps.supports_color
    )

    if use_color_temp:
        # 4. quantise, 5. clamp to the device's own reported range.
        kelvin = 5 * round(kelvin / 5)
        if profile.clamp_to_device_limits:
            if kelvin < caps.min_color_temp_kelvin:
                saturation |= Saturation.CT_LOW
            if kelvin > caps.max_color_temp_kelvin:
                saturation |= Saturation.CT_HIGH
            kelvin = clamp(
                kelvin, caps.min_color_temp_kelvin, caps.max_color_temp_kelvin
            )
        return {ATTR_COLOR_TEMP_KELVIN: int(kelvin)}, saturation

    if caps.supports_color:
        # A colour-only fixture still deserves the calibration: derive RGB from
        # the *offset* temperature, not the raw one. Adaptive Lighting reuses a
        # precomputed RGB here, so a colour-temp offset silently does nothing on
        # exactly the bulbs most likely to need matching.
        payload = chromatic_payload(color_temperature_to_rgb(kelvin), caps)
        return payload, saturation

    # 6. the light has no colour axis at all.
    return None, saturation


def resolve_target(
    settings: AdaptiveSettings,
    profile: LightProfile,
    caps: LightCapabilities,
    *,
    want: Axis = Axis.ALL,
    bias_pct: float = 0.0,
) -> LightTarget:
    """Render the adaptive curve into one light's concrete target.

    ``want`` restricts which axes are produced. The render pipeline uses it to
    emit only the axes a scene has not taken over, which is what lets "keep
    adaptive brightness, override colour" keep ticking on the brightness side.
    """
    if not profile.enabled:
        return LightTarget(caps.entity_id)

    axes = Axis.NONE
    saturation = Saturation.NONE
    brightness: int | None = None
    color: dict[str, Any] | None = None

    if Axis.BRIGHTNESS in want:
        brightness, brightness_saturation = resolve_brightness(
            settings, profile, caps, bias_pct=bias_pct
        )
        saturation |= brightness_saturation
        if brightness is not None:
            axes |= Axis.BRIGHTNESS

    if Axis.COLOR in want:
        color, color_saturation = resolve_color(settings, profile, caps)
        saturation |= color_saturation
        if color:
            axes |= Axis.COLOR

    return LightTarget(
        entity_id=caps.entity_id,
        brightness=brightness,
        color=color,
        axes=axes,
        saturation=saturation,
    )


def describe_saturation(saturation: Saturation) -> str | None:
    """A short label for entity attributes and diagnostics."""
    if saturation is Saturation.NONE:
        return None
    parts = [
        name
        for flag, name in (
            (Saturation.BRIGHTNESS_LOW, "brightness_low"),
            (Saturation.BRIGHTNESS_HIGH, "brightness_high"),
            (Saturation.CT_LOW, "color_temp_low"),
            (Saturation.CT_HIGH, "color_temp_high"),
        )
        if flag in saturation
    ]
    return ",".join(parts)


__all__ = [
    "IDENTITY_PROFILE",
    "Axis",
    "LightCapabilities",
    "LightProfile",
    "LightTarget",
    "Saturation",
    "brightness_from_pct",
    "describe_saturation",
    "from_mired",
    "resolve_target",
    "to_mired",
]
