"""Triggers: the sensors that ask for a room or a zone to be lit.

A drive has a motion sensor. A garage has a door. A porch may well have both,
and either one is reason enough to light it -- so triggers are **ORed**, where
the rules that gate them are ANDed. One more trigger can only make a light come
on more often; one more rule can only make it come on less.

"Active" is deliberately generous. A binary sensor says ``on``, a cover says
``open``, a device tracker says ``home``, a media player says ``playing``, and
all of them mean the same thing here. Anything else can be named outright,
which is how a sensor that reports ``detected`` or ``Open`` gets to be a
trigger too.

Each trigger can also carry its own rules, stand aside at night, and name the
scene it lights. A hallway sensor may light the living room only while night
mode is off; the sensor under the bed may light only the path to the door.
A trigger that was allowed when it went active stays counted until it goes
quiet again, so a lux rule cannot switch off the light that made it bright.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .conditions import Condition

__all__ = [
    "SensorTrigger",
    "active_entities",
    "any_active",
    "counted",
    "is_active",
]

# What counts as active when a trigger does not say. Covers the domains
# anybody actually points at a light: binary sensors, covers, device trackers,
# people, and a media player somebody is watching or listening to.
DEFAULT_ACTIVE = ("on", "open", "home", "detected", "playing", "paused", "buffering")

# Nothing to read. Held rather than guessed: a sensor that has dropped out is
# not evidence that a room is empty.
UNREADABLE = (None, "", "unknown", "unavailable")


@dataclass(frozen=True, slots=True)
class SensorTrigger:
    """One sensor that asks for the lights."""

    entity_id: str
    # The state that counts as active. Empty means the usual ones, which is
    # what almost every sensor wants.
    active_state: str = ""
    # This sensor's own rules, ANDed, on top of the room's. Checked when the
    # sensor goes active: they decide whether it may count at all.
    rules: tuple[Condition, ...] = ()
    # Stand aside while night mode is on: the presence sensor over the bed.
    ignore_at_night: bool = False
    # The scene this sensor lights, instead of whatever the room would.
    scene_id: str | None = None

    def counts(self, state: str | None) -> bool:
        if state in UNREADABLE:
            return False
        value = str(state).casefold()
        if self.active_state:
            return value == self.active_state.casefold()
        return value in DEFAULT_ACTIVE


def is_active(trigger: SensorTrigger, states: Mapping[str, str | None]) -> bool:
    """Whether this one trigger is asking for the lights right now."""
    return trigger.counts(states.get(trigger.entity_id))


def any_active(
    triggers: Sequence[SensorTrigger], states: Mapping[str, str | None]
) -> bool:
    """Whether any trigger is asking. No triggers is not asking."""
    return any(is_active(trigger, states) for trigger in triggers)


def counted(
    triggers: Sequence[SensorTrigger],
    states: Mapping[str, str | None],
    previous: Iterable[SensorTrigger],
    may_count: Callable[[SensorTrigger], bool],
) -> tuple[SensorTrigger, ...]:
    """The triggers that hold the lights on right now, in their own order.

    A trigger counts while it is active, provided it was allowed to count when
    it went active. Asked once and then held: a lux rule re-read while the
    light it switched on is shining would put that light straight back out.
    """
    held = set(previous)
    return tuple(
        trigger
        for trigger in triggers
        if is_active(trigger, states) and (trigger in held or may_count(trigger))
    )


def active_entities(triggers: Iterable[SensorTrigger]) -> list[str]:
    """The entities to subscribe to, with duplicates removed and order kept."""
    seen: dict[str, None] = {}
    for trigger in triggers:
        if trigger.entity_id:
            seen.setdefault(trigger.entity_id, None)
    return list(seen)
