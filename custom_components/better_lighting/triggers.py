"""Triggers: the sensors that ask for a room or a zone to be lit.

A drive has a motion sensor. A garage has a door. A porch may well have both,
and either one is reason enough to light it -- so triggers are **ORed**, where
the rules that gate them are ANDed. One more trigger can only make a light come
on more often; one more rule can only make it come on less.

"Active" is deliberately generous. A binary sensor says ``on``, a cover says
``open``, a device tracker says ``home``, and all three mean the same thing
here. Anything else can be named outright, which is how a sensor that reports
``detected`` or ``Open`` gets to be a trigger too.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["SensorTrigger", "active_entities", "any_active", "is_active"]

# What counts as active when a trigger does not say. Covers the four domains
# anybody actually points at a light: binary sensors, covers, device trackers
# and people.
DEFAULT_ACTIVE = ("on", "open", "home", "detected")

# Nothing to read. Held rather than guessed: a sensor that has dropped out is
# not evidence that a room is empty.
UNREADABLE = (None, "", "unknown", "unavailable")


@dataclass(frozen=True, slots=True)
class SensorTrigger:
    """One sensor that asks for the lights."""

    entity_id: str
    # The state that counts as active. Empty means the usual four, which is
    # what almost every sensor wants.
    active_state: str = ""

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


def active_entities(triggers: Iterable[SensorTrigger]) -> list[str]:
    """The entities to subscribe to, with duplicates removed and order kept."""
    seen: dict[str, None] = {}
    for trigger in triggers:
        if trigger.entity_id:
            seen.setdefault(trigger.entity_id, None)
    return list(seen)
