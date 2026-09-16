"""The scene model: a reusable recipe, not a set of entity states.

This is the key divergence from Scenery, whose scene is a mapping of
``entity_id -> State``. A Better Lighting scene says *what a room should look
like* -- a brightness, one colour, and which of those two it takes over -- with
no reference to any particular light. That is what makes "Cooking" or "Movie
night" reusable across every room rather than something you redefine per room.

The colour model itself is ported from Scenery's ``light_utils.py``, which got
it right: a colour is a mapping carrying *exactly one* of the eight attributes
Home Assistant understands, enforced with ``vol.Exclusive``. The tolerance-based
comparators come from there too; they are what lets us ask "is this light still
showing what we told it?" despite lossy round-tripping through a bulb.

Pure: imports nothing from ``homeassistant`` except the colour conversions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import voluptuous as vol
from homeassistant.util.color import (
    color_name_to_rgb,
    color_RGB_to_hs,
    color_RGB_to_xy,
    color_temperature_to_rgb,
)

from .profiles import (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    Axis,
    LightCapabilities,
)

ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_MODE = "color_mode"
ATTR_COLOR_NAME = "color_name"

# Every colour format a scene may be expressed in.
COLOR_ATTRS = (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_XY_COLOR,
    ATTR_WHITE,
    ATTR_COLOR_NAME,
)

type Color = Mapping[str, Any]

_COLOR_GROUP = "color"


def _byte(value: Any) -> int:
    number = int(value)
    if not 0 <= number <= 255:
        raise vol.Invalid(f"{number} is not between 0 and 255")
    return number


def _small_float(value: Any) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise vol.Invalid(f"{number} is not between 0 and 1")
    return number


# Exactly one colour format per colour. Two would be a contradiction that only
# shows up as the wrong light on the wall, so it is rejected at the boundary.
COLOR_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_COLOR_TEMP_KELVIN, _COLOR_GROUP): vol.All(
            vol.Coerce(int), vol.Range(min=1000, max=10000)
        ),
        vol.Exclusive(ATTR_HS_COLOR, _COLOR_GROUP): vol.All(
            vol.Coerce(tuple),
            vol.ExactSequence(
                (
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=360)),
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                )
            ),
        ),
        vol.Exclusive(ATTR_RGB_COLOR, _COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((_byte,) * 3)
        ),
        vol.Exclusive(ATTR_RGBW_COLOR, _COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((_byte,) * 4)
        ),
        vol.Exclusive(ATTR_RGBWW_COLOR, _COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((_byte,) * 5)
        ),
        vol.Exclusive(ATTR_XY_COLOR, _COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((_small_float, _small_float))
        ),
        vol.Exclusive(ATTR_WHITE, _COLOR_GROUP): _byte,
        vol.Exclusive(ATTR_COLOR_NAME, _COLOR_GROUP): str,
    }
)


def extract_color(values: Mapping[str, Any]) -> Color | None:
    """Pull the single colour attribute out of a wider mapping."""
    color = {attr: values[attr] for attr in COLOR_ATTRS if values.get(attr) is not None}
    return color or None


class SceneOverride(StrEnum):
    """Which axes a scene takes over, leaving the rest adaptive.

    This is requirement 7. The two partial modes are the interesting ones: they
    mean the interval tick must keep updating the axis the scene did *not*
    claim, so a "Cooking" scene can pin the brightness while the colour keeps
    tracking the sun all evening.
    """

    BRIGHTNESS = "brightness"
    COLOR = "color"
    BOTH = "both"
    # Selects which lights are on and leaves both axes adaptive. Costs nothing
    # to support and expresses "these three fixtures, normal light" exactly.
    NEITHER = "neither"


SCENE_AXES: dict[SceneOverride, Axis] = {
    SceneOverride.BRIGHTNESS: Axis.BRIGHTNESS,
    SceneOverride.COLOR: Axis.COLOR,
    SceneOverride.BOTH: Axis.ALL,
    SceneOverride.NEITHER: Axis.NONE,
}


class OthersPolicy(StrEnum):
    """What happens to zone members a scene does not name."""

    # Keep adapting them. The default: a scene that mentions two lamps should
    # not plunge the rest of the room into darkness.
    ADAPTIVE = "adaptive"
    OFF = "off"
    LEAVE = "leave"


class UnsupportedColorPolicy(StrEnum):
    """What to do when a fixture cannot show a scene's colour at all."""

    # Leave it on its adaptive white. Reads as "that bulb can't do colour",
    # which is true, and is the least surprising outcome.
    ADAPTIVE = "adaptive"
    # Project onto the Planckian locus. Honest only for near-white scenes.
    NEAREST_CT = "nearest_ct"
    SKIP = "skip"


# The key of the entry that applies to every light in the room. A scene can
# then say "all of you at 20%" once and override only the lights that differ.
ALL_LIGHTS = "*"


@dataclass(frozen=True, slots=True)
class SceneLightSpec:
    """What a scene says about one particular light.

    Anything left unset falls back to the scene's own value, and anything the
    scene does not set either stays adaptive -- which is how a desk lamp can
    take the scene's brightness while its colour keeps tracking the sun.
    """

    brightness_pct: float | None = None
    color: Color | None = None
    # This light is dark in this scene, while the rest of the room is not.
    # Distinct from `skip`, which means "do not touch it at all".
    turn_off: bool = False
    skip: bool = False


@dataclass(frozen=True, slots=True)
class Scene:
    """A reusable look, independent of any particular room."""

    scene_id: str
    name: str = ""
    icon: str = "mdi:palette"
    override: SceneOverride = SceneOverride.BOTH
    brightness_pct: float | None = None
    color: Color | None = None
    # Empty means "every member of whatever zone this is applied to".
    lights: Mapping[str, SceneLightSpec] = field(default_factory=dict)
    on_lights_only: bool = False
    # Rooms this scene is offered in. Empty means everywhere. Purely about
    # what the pickers show: applying a scene has only ever affected the one
    # room it was applied to.
    zones: frozenset[str] = frozenset()
    others: OthersPolicy = OthersPolicy.ADAPTIVE
    ignore_presence: bool = False
    transition: float | None = None
    on_unsupported_color: UnsupportedColorPolicy = UnsupportedColorPolicy.ADAPTIVE

    def offered_in(self, zone_id: str) -> bool:
        """Whether this scene should appear in a given room's choices."""
        return not self.zones or zone_id in self.zones

    def spec_for(self, entity_id: str) -> SceneLightSpec:
        """This scene's intent for one light.

        Resolved in three layers, each filling in what the one above left
        unsaid: the light's own entry, then the room-wide ``*`` entry, then
        any scene-level value. The ``*`` entry is what makes "everything at
        20%, except the ceiling which is off" a two-row scene rather than one
        row per bulb.
        """
        spec = self.lights.get(entity_id)
        fallback = self.lights.get(ALL_LIGHTS)
        if spec is None:
            spec = fallback if fallback is not None else SceneLightSpec()
            fallback = None

        def pick(value: Any, other: Any, scene_level: Any) -> Any:
            if value is not None:
                return value
            if fallback is not None and other is not None:
                return other
            return scene_level

        return SceneLightSpec(
            brightness_pct=pick(
                spec.brightness_pct,
                fallback.brightness_pct if fallback else None,
                self.brightness_pct,
            ),
            color=pick(
                spec.color,
                fallback.color if fallback else None,
                self.color,
            ),
            turn_off=spec.turn_off or bool(fallback and fallback.turn_off),
            skip=spec.skip or bool(fallback and fallback.skip),
        )

    def axes_specified_for(self, entity_id: str) -> Axis:
        """Which axes this scene actually carries a value for.

        The override mode states *intent*; this states what was supplied. A
        BOTH scene that only names a colour for one light leaves that light's
        brightness adaptive rather than emitting nothing for it.
        """
        spec = self.spec_for(entity_id)
        axes = Axis.NONE
        if spec.brightness_pct is not None:
            axes |= Axis.BRIGHTNESS
        # Truthiness, not `is not None`: an empty colour is how a light says
        # "leave my colour to the sun" while still taking the scene's
        # brightness. `None` means "use the scene's colour".
        if spec.color:
            axes |= Axis.COLOR
        return axes

    def targets(self, entity_id: str) -> bool:
        """Whether this scene has anything to say about ``entity_id``."""
        if (
            self.lights
            and entity_id not in self.lights
            and ALL_LIGHTS not in self.lights
        ):
            return False
        return not self.spec_for(entity_id).skip


# --------------------------------------------------------------------------
# Reconciling a scene's colour with what a fixture can actually do.
# --------------------------------------------------------------------------

_CHROMATIC_PREFERENCE = (
    (ATTR_RGBWW_COLOR, "rgbww"),
    (ATTR_RGBW_COLOR, "rgbw"),
    (ATTR_RGB_COLOR, "rgb"),
    (ATTR_HS_COLOR, "hs"),
    (ATTR_XY_COLOR, "xy"),
)


def to_rgb(color: Color) -> tuple[float, float, float]:
    """Best-effort conversion of any colour format to RGB."""
    if (name := color.get(ATTR_COLOR_NAME)) is not None:
        return tuple(color_name_to_rgb(str(name)))
    if (kelvin := color.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
        return color_temperature_to_rgb(float(kelvin))
    for attr in (ATTR_RGBWW_COLOR, ATTR_RGBW_COLOR, ATTR_RGB_COLOR):
        if (value := color.get(attr)) is not None:
            return tuple(float(channel) for channel in value[:3])
    if (hs_value := color.get(ATTR_HS_COLOR)) is not None:
        from homeassistant.util.color import color_hs_to_RGB

        return color_hs_to_RGB(*hs_value)
    if (xy_value := color.get(ATTR_XY_COLOR)) is not None:
        from homeassistant.util.color import color_xy_to_RGB

        return color_xy_to_RGB(*xy_value)
    if (white := color.get(ATTR_WHITE)) is not None:
        return (float(white), float(white), float(white))
    return (255.0, 255.0, 255.0)


def rgb_to_nearest_kelvin(rgb: tuple[float, float, float]) -> int:
    """Approximate the colour temperature closest to an RGB value.

    Deliberately crude: it walks the Planckian locus and keeps the closest
    match. Only used when the user explicitly asks for `nearest_ct`, because
    projecting a saturated colour onto white is usually not what they meant.
    """
    best_kelvin, best_distance = 4000, None
    for kelvin in range(1500, 9001, 50):
        candidate = color_temperature_to_rgb(kelvin)
        distance = sum((a - b) ** 2 for a, b in zip(rgb, candidate, strict=True))
        if best_distance is None or distance < best_distance:
            best_kelvin, best_distance = kelvin, distance
    return best_kelvin


def select_color_attrs(
    color: Color,
    caps: LightCapabilities,
    *,
    adaptive_kelvin: int | None = None,
    policy: UnsupportedColorPolicy = UnsupportedColorPolicy.ADAPTIVE,
) -> dict[str, Any] | None:
    """Express one of the eight colour formats in a form this fixture accepts.

    Returns ``None`` when the fixture cannot carry the colour at all, which the
    caller reads as "leave this light's colour axis alone".
    """
    # Normalise the two indirect formats first.
    if (name := color.get(ATTR_COLOR_NAME)) is not None:
        color = {ATTR_RGB_COLOR: tuple(color_name_to_rgb(str(name)))}

    if (white := color.get(ATTR_WHITE)) is not None:
        if caps.supports_white:
            return {ATTR_WHITE: int(white)}
        if caps.supports_color_temp:
            # "White" has no temperature of its own; a neutral one is the
            # closest honest reading.
            return {
                ATTR_COLOR_TEMP_KELVIN: int(
                    _clamp_kelvin(4000, caps),
                )
            }
        return None

    if (kelvin := color.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
        if caps.supports_color_temp:
            return {ATTR_COLOR_TEMP_KELVIN: int(_clamp_kelvin(float(kelvin), caps))}
        if caps.supports_color:
            return _chromatic(color_temperature_to_rgb(float(kelvin)), caps)
        return None

    # A chromatic colour. Use the fixture's native encoding where it has one.
    for attr, mode in _CHROMATIC_PREFERENCE:
        if color.get(attr) is not None and mode in caps.color_modes:
            return {attr: tuple(color[attr])}

    if caps.supports_color:
        return _chromatic(to_rgb(color), caps)

    if caps.supports_color_temp:
        if policy is UnsupportedColorPolicy.NEAREST_CT:
            return {
                ATTR_COLOR_TEMP_KELVIN: int(
                    _clamp_kelvin(rgb_to_nearest_kelvin(to_rgb(color)), caps)
                )
            }
        if policy is UnsupportedColorPolicy.ADAPTIVE and adaptive_kelvin is not None:
            return {ATTR_COLOR_TEMP_KELVIN: int(_clamp_kelvin(adaptive_kelvin, caps))}
    return None


def _clamp_kelvin(kelvin: float, caps: LightCapabilities) -> float:
    return min(max(kelvin, caps.min_color_temp_kelvin), caps.max_color_temp_kelvin)


def _chromatic(
    rgb: tuple[float, float, float], caps: LightCapabilities
) -> dict[str, Any] | None:
    """Encode an RGB colour in the richest mode the fixture understands."""
    red, green, blue = (int(min(max(round(channel), 0), 255)) for channel in rgb)
    if "rgbww" in caps.color_modes:
        return {ATTR_RGBWW_COLOR: (red, green, blue, 0, 0)}
    if "rgbw" in caps.color_modes:
        return {ATTR_RGBW_COLOR: (red, green, blue, 0)}
    if "rgb" in caps.color_modes:
        return {ATTR_RGB_COLOR: (red, green, blue)}
    if "hs" in caps.color_modes:
        return {ATTR_HS_COLOR: color_RGB_to_hs(red, green, blue)}
    if "xy" in caps.color_modes:
        return {ATTR_XY_COLOR: color_RGB_to_xy(red, green, blue)}
    return None


# --------------------------------------------------------------------------
# Tolerance-based comparison, for "is this light still where we put it?".
# --------------------------------------------------------------------------

TOLERANCE_HUE = 5
TOLERANCE_SATURATION = 5
TOLERANCE_PRIMARY = 5
TOLERANCE_CHROMATICITY = 0.05
TOLERANCE_KELVIN = 100
TOLERANCE_BRIGHTNESS = 3


def _hue_close(a: float, b: float) -> bool:
    delta = float(a) - float(b)
    # Hue wraps: 359 and 1 are two degrees apart, not 358.
    return delta % 360 < TOLERANCE_HUE or -delta % 360 <= TOLERANCE_HUE


def compare_state_to_color(attributes: Mapping[str, Any], target: Color) -> bool:
    """Is the light currently showing ``target``, within tolerance?

    Asymmetric on purpose: ``attributes`` is a live light state, which may carry
    several equivalent representations of one colour, while ``target`` carries
    exactly one. The target's colour temperature is clamped into the light's own
    range first, so asking a 2200 K-floor bulb for 2000 K still compares equal.
    """
    if (name := target.get(ATTR_COLOR_NAME)) is not None:
        target = {ATTR_RGB_COLOR: tuple(color_name_to_rgb(str(name)))}
    if target.get(ATTR_WHITE) is not None:
        return attributes.get(ATTR_COLOR_MODE) == "white"

    current_kelvin = attributes.get(ATTR_COLOR_TEMP_KELVIN)
    target_kelvin = target.get(ATTR_COLOR_TEMP_KELVIN)
    if current_kelvin is not None and target_kelvin is not None:
        wanted = float(target_kelvin)
        if (ceiling := attributes.get("max_color_temp_kelvin")) is not None:
            wanted = min(wanted, float(ceiling))
        if (floor := attributes.get("min_color_temp_kelvin")) is not None:
            wanted = max(wanted, float(floor))
        if abs(float(current_kelvin) - wanted) <= TOLERANCE_KELVIN:
            return True

    for attr, size in (
        (ATTR_RGBWW_COLOR, 5),
        (ATTR_RGBW_COLOR, 4),
        (ATTR_RGB_COLOR, 3),
    ):
        current, wanted_value = attributes.get(attr), target.get(attr)
        if (
            current is not None
            and wanted_value is not None
            and all(
                abs(int(current[i]) - int(wanted_value[i])) <= TOLERANCE_PRIMARY
                for i in range(size)
            )
        ):
            return True

    current_hs, target_hs = attributes.get(ATTR_HS_COLOR), target.get(ATTR_HS_COLOR)
    if (
        current_hs is not None
        and target_hs is not None
        and _hue_close(current_hs[0], target_hs[0])
        and abs(float(current_hs[1]) - float(target_hs[1])) <= TOLERANCE_SATURATION
    ):
        return True

    current_xy, target_xy = attributes.get(ATTR_XY_COLOR), target.get(ATTR_XY_COLOR)
    return (
        current_xy is not None
        and target_xy is not None
        and all(
            abs(float(current_xy[i]) - float(target_xy[i])) <= TOLERANCE_CHROMATICITY
            for i in range(2)
        )
    )


def compare_state_to_brightness(attributes: Mapping[str, Any], brightness: int) -> bool:
    current = attributes.get(ATTR_BRIGHTNESS)
    return current is not None and abs(int(current) - int(brightness)) <= (
        TOLERANCE_BRIGHTNESS
    )


__all__ = [
    "COLOR_ATTRS",
    "COLOR_SCHEMA",
    "SCENE_AXES",
    "Color",
    "OthersPolicy",
    "Scene",
    "SceneLightSpec",
    "SceneOverride",
    "UnsupportedColorPolicy",
    "compare_state_to_brightness",
    "compare_state_to_color",
    "extract_color",
    "select_color_attrs",
]
