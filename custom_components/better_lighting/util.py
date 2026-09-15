"""Small pure helpers shared by the calculation modules.

This module must not import from ``homeassistant`` so that the calculation
layer stays unit-testable without an event loop.
"""

from __future__ import annotations


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp ``value`` into ``[lower, upper]``.

    The bounds are sorted first, so callers may pass an inverted range (a
    ``min`` larger than its ``max``) to express a deliberately reversed
    schedule without the result collapsing to a single value.  Adaptive
    Lighting added the same behaviour after users asked for inverted
    brightness curves.
    """
    if lower > upper:
        lower, upper = upper, lower
    return max(lower, min(value, upper))


def clamp_int(value: float, lower: float, upper: float) -> int:
    """Round ``value`` to the nearest integer, then clamp it."""
    return int(clamp(round(value), lower, upper))
