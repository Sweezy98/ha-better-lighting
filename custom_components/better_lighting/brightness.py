"""Relative brightness maths for zone light groups.

Ported from Relative Light Group's ``brightness.py``, with one deliberate
change: the functions take plain ``{entity_id: brightness}`` mappings rather
than Home Assistant ``State`` objects, so this module imports nothing from
``homeassistant`` and can be unit-tested without an event loop.  The HA layer
extracts the brightness values from member states before calling in.

These are the *fallback* path.  When Better Lighting owns a light's brightness
axis it renders an absolute target from the adaptive curve plus the zone bias,
which preserves each light's configured offset exactly.  These functions are
used when there is no such model to work from -- lights under manual override,
or a zone driven by an external mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from statistics import median

from .util import clamp_int

# Never command 0: many integrations treat it as "off", which would make a
# dim-to-minimum indistinguishable from a turn-off.
BRIGHTNESS_MIN = 1
BRIGHTNESS_MAX = 255


class BrightnessStrategy(StrEnum):
    """How a group derives one representative brightness from its members."""

    AVERAGE = "average"
    MEDIAN = "median"
    MAX = "max"
    MIN = "min"


def representative_brightness(
    brightnesses: Mapping[str, int | None],
    strategy: BrightnessStrategy = BrightnessStrategy.AVERAGE,
) -> int | None:
    """Reduce member brightnesses to the single value the group reports.

    Callers pass only the members that are *on*; an off member has no
    brightness to contribute and including it would drag the average down.
    Returns ``None`` when no member reports a brightness.
    """
    values = [
        clamp_int(value, BRIGHTNESS_MIN, BRIGHTNESS_MAX)
        for value in brightnesses.values()
        if value is not None
    ]
    if not values:
        return None

    match strategy:
        case BrightnessStrategy.MEDIAN:
            return int(median(values))
        case BrightnessStrategy.MAX:
            return max(values)
        case BrightnessStrategy.MIN:
            return min(values)
        case _:
            return int(sum(values) / len(values))


def relative_brightness_map(
    brightnesses: Mapping[str, int | None],
    current_group_brightness: int,
    target_group_brightness: int,
) -> dict[str, int]:
    """Move every member by the same *fraction of its own headroom*.

    The group's requested change is expressed as a fraction of the group's
    available headroom -- the distance to 255 when brightening, or to 0 when
    dimming -- and each member then travels that same fraction of *its own*
    headroom.  Relative differences between members are roughly preserved, and
    every member reaches the top or the bottom together, so the group slider
    still spans the full range no matter how far apart the members started.

    A member with no known brightness (not dimmable, or unavailable) is given
    the group's absolute target instead, since there is no relative move to
    make for it.

    Returns an empty map when there is nothing to do -- no current brightness,
    no change requested, or no headroom left in the requested direction.
    """
    if current_group_brightness <= 0:
        return {}

    change = target_group_brightness - current_group_brightness
    if change == 0:
        return {}

    headroom = (
        BRIGHTNESS_MAX - current_group_brightness
        if change > 0
        else current_group_brightness
    )
    if headroom <= 0:
        return {}

    factor = change / headroom
    result: dict[str, int] = {}

    for entity_id, brightness in brightnesses.items():
        if brightness is None:
            result[entity_id] = clamp_int(
                target_group_brightness, BRIGHTNESS_MIN, BRIGHTNESS_MAX
            )
            continue

        member_headroom = BRIGHTNESS_MAX - brightness if change > 0 else brightness
        result[entity_id] = clamp_int(
            brightness + factor * member_headroom, BRIGHTNESS_MIN, BRIGHTNESS_MAX
        )

    return result


def base_relative_brightness_map(
    base_brightness: Mapping[str, int],
    target_group_brightness: int,
) -> dict[str, int]:
    """Scale members from a remembered *base* rather than their live values.

    Repeatedly dimming and re-brightening with :func:`relative_brightness_map`
    loses information, because each step re-reads values it just rounded.
    Anchoring on a base captured from *external* changes only makes the
    operation reversible: dimming is a pure proportional scale (exact ratio
    preservation), and brightening distributes headroom from the same base.

    The HA layer owns the base map and refreshes an entry only when that member
    changes under a context that is not ours.
    """
    if not base_brightness:
        return {}

    base_group = sum(base_brightness.values()) / len(base_brightness)
    if base_group <= 0:
        return {}

    direction = target_group_brightness - base_group
    result: dict[str, int] = {}

    if direction >= 0:
        group_headroom = BRIGHTNESS_MAX - base_group
        factor = direction / group_headroom if group_headroom > 0 else 0.0
        for entity_id, base in base_brightness.items():
            result[entity_id] = clamp_int(
                base + factor * (BRIGHTNESS_MAX - base),
                BRIGHTNESS_MIN,
                BRIGHTNESS_MAX,
            )
        return result

    factor = direction / base_group
    for entity_id, base in base_brightness.items():
        result[entity_id] = clamp_int(
            base + factor * base, BRIGHTNESS_MIN, BRIGHTNESS_MAX
        )
    return result


def group_by_brightness(brightness_map: Mapping[str, int]) -> dict[int, list[str]]:
    """Invert an entity->brightness map so identical targets share one call."""
    groups: dict[int, list[str]] = {}
    for entity_id, brightness in brightness_map.items():
        groups.setdefault(brightness, []).append(entity_id)
    return {brightness: sorted(ids) for brightness, ids in groups.items()}
