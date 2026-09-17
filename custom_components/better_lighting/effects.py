"""Effects: what a light does over time rather than what it is set to.

A scene is a value -- this bulb at 7% and amber. An effect is a shape: pulse
slowly, flicker like a candle, flash twice a second. The two compose, because
an effect here says nothing about *which* colour and *how* bright; it says
what to do with whichever colour and brightness it is handed. So one "breath"
is the breathing for every notification and every scene that wants it, and a
user who writes "three quick blips" writes it once.

Pure: imports nothing from ``homeassistant``. The adapter turns frames into
service calls and waits between them; everything about the shape is decided
here, which is what makes it table-testable rather than something you have to
watch a bulb to check.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


# A step's level is a fraction of the brightness the caller asked for, not an
# absolute: an effect that dimmed to 20% regardless would be a different
# effect at every brightness, and unusable in a room that is already dim.
@dataclass(frozen=True, slots=True)
class EffectStep:
    """One position in an effect, and how long to take getting there."""

    level: float = 1.0
    # Seconds spent moving to this level, and seconds spent sitting at it.
    transition: float = 0.0
    hold: float = 0.0
    # An effect may name its own colour for a step -- a candle warms as it
    # dips. Left unset, the colour the caller asked for is used throughout.
    color: Mapping[str, Any] | None = None

    def scaled(self, brightness_pct: float) -> float:
        """This step's brightness, given what the caller asked for."""
        return max(0.0, min(100.0, brightness_pct * self.level))


@dataclass(frozen=True, slots=True)
class Effect:
    """A named shape, built of steps."""

    effect_id: str
    name: str = ""
    steps: tuple[EffectStep, ...] = ()
    # Whether the steps start again when they run out. A notification plays
    # for a duration, so a repeating effect fills it and a one-shot effect
    # happens once and then holds.
    repeat: bool = True

    @property
    def cycle_seconds(self) -> float:
        return sum(step.transition + step.hold for step in self.steps)


@dataclass(frozen=True, slots=True)
class Frame:
    """One command to send, and how long to wait before the next."""

    brightness_pct: float
    color: Mapping[str, Any] | None
    transition: float
    wait: float


# Enough to be useful out of the box, and few enough that each is obviously
# different from the others.
SOLID = Effect(
    "solid",
    "Solid",
    (EffectStep(level=1.0, transition=0.4, hold=0.0),),
    repeat=False,
)
FLASH = Effect(
    "flash",
    "Flash",
    (
        EffectStep(level=1.0, transition=0.0, hold=0.25),
        EffectStep(level=0.05, transition=0.0, hold=0.25),
    ),
)
BREATHE = Effect(
    "breathe",
    "Breathe",
    (
        EffectStep(level=1.0, transition=1.1, hold=0.15),
        EffectStep(level=0.22, transition=1.1, hold=0.15),
    ),
)
PULSE = Effect(
    "pulse",
    "Pulse",
    (
        EffectStep(level=1.0, transition=0.35, hold=0.1),
        EffectStep(level=0.35, transition=0.35, hold=0.1),
    ),
)
# Small, uneven dips with a warmer colour at the bottom of each: a flame does
# not pulse evenly, and an evenly pulsing light does not read as one.
CANDLE = Effect(
    "candle",
    "Candle flicker",
    (
        EffectStep(level=1.0, transition=0.18, hold=0.22),
        EffectStep(level=0.74, transition=0.12, hold=0.1),
        EffectStep(level=0.9, transition=0.22, hold=0.35),
        EffectStep(
            level=0.6,
            transition=0.1,
            hold=0.08,
            color={"color_temp_kelvin": 1900},
        ),
        EffectStep(level=0.86, transition=0.3, hold=0.2),
    ),
)

BUILT_IN: tuple[Effect, ...] = (SOLID, FLASH, BREATHE, PULSE, CANDLE)
BUILT_IN_BY_ID: dict[str, Effect] = {effect.effect_id: effect for effect in BUILT_IN}


@dataclass(frozen=True, slots=True)
class EffectRequest:
    """Play ``effect`` in this colour, this bright, for this long."""

    effect: Effect
    brightness_pct: float = 100.0
    color: Mapping[str, Any] | None = None
    # Seconds. Zero means "until somebody stops it", which is what a scene
    # wants and a notification does not.
    duration: float = 0.0


def frames(request: EffectRequest) -> Iterator[Frame]:
    """The commands to send, in order, with the wait after each.

    Ends when the duration is used up. A repeating effect is cut off wherever
    the duration lands rather than finishing its cycle, because a
    notification that asked for two seconds means two seconds.
    """
    effect = request.effect
    if not effect.steps:
        return
    elapsed = 0.0
    limited = request.duration > 0

    while True:
        for step in effect.steps:
            span = step.transition + step.hold
            if limited and elapsed >= request.duration:
                return
            # The last frame is trimmed to land exactly on the duration, so
            # the light is not still moving when the room is put back.
            if limited:
                span = min(span, request.duration - elapsed)
            yield Frame(
                brightness_pct=step.scaled(request.brightness_pct),
                color=step.color or request.color,
                transition=min(step.transition, span),
                wait=span,
            )
            elapsed += span
        if not effect.repeat:
            return
        if not limited:
            # Nothing to stop it and nothing to count: the caller loops.
            return


def from_mapping(raw: Mapping[str, Any]) -> Effect:
    """One effect as the user wrote it."""
    return Effect(
        effect_id=str(raw.get("effect_id") or ""),
        name=str(raw.get("name") or ""),
        steps=tuple(
            EffectStep(
                level=float(step.get("level", 100)) / 100,
                transition=float(step.get("transition", 0)),
                hold=float(step.get("hold", 0)),
            )
            for step in (raw.get("steps") or ())
        ),
        repeat=bool(raw.get("repeat", True)),
    )


def resolve(effect_id: str | None, custom: Mapping[str, Effect]) -> Effect | None:
    """The effect that id names, whether ours or the user's."""
    if not effect_id:
        return None
    return custom.get(effect_id) or BUILT_IN_BY_ID.get(effect_id)
