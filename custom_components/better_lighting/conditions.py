"""Conditions: when an automation is allowed to act at all.

A motion sensor on a drive should light it at night and do nothing at noon. A
garage door should light the garage when it opens, but not while somebody is
already working in there with the lights on by hand. Both are the same shape:
the trigger says *something happened*, and a condition says *and it counts*.

Conditions are house-wide and named, for the same reason colour presets are:
"after dark" is one rule, wanted by every outdoor trigger, and describing it
once means changing it once. A room or a zone names the ones that apply to it.

They are **ANDed**. Every named condition has to hold, which is the reading
that makes a list of them safe to add to: another rule can only ever make an
automation fire less often, never more.

A condition that cannot be read -- an unavailable lux sensor, a helper that
has not come back after a restart -- blocks by default. "Allowed only if every
rule passes" is the promise, and a rule nobody can evaluate has not passed.
Each condition can say otherwise, because a flaky sensor that leaves somebody
walking up an unlit path is its own kind of wrong.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Condition",
    "ConditionKind",
    "Verdict",
    "evaluate",
]

# What a state looks like when there is nothing to read.
_UNREADABLE = (None, "", "unknown", "unavailable")


class ConditionKind(StrEnum):
    """The kinds of rule an automation can be gated on."""

    # Between two clock times, wrapping midnight when the end is the earlier.
    TIME_WINDOW = "time_window"
    # A number below a threshold: the darkness half of a lux sensor.
    BELOW = "below"
    # A number above a threshold.
    ABOVE = "above"
    # An entity in a particular state: a helper that arms the automation, a
    # presence toggle that disarms it, `sun.sun` being below the horizon.
    STATE_IS = "state_is"


@dataclass(frozen=True, slots=True)
class Condition:
    """One rule. Everything that does not apply to its kind is ignored."""

    condition_id: str
    name: str = ""
    kind: ConditionKind = ConditionKind.STATE_IS
    entity_id: str | None = None
    # BELOW / ABOVE.
    threshold: float = 0.0
    # TIME_WINDOW, as minutes since midnight. Equal values mean the whole day,
    # which is a rule that is always true rather than never.
    start_minute: int = 0
    end_minute: int = 0
    # STATE_IS. Usually "on" or "off", but anything a state can be: naming
    # `sun.sun` and "below_horizon" is how to say "after dark" without a lux
    # sensor at all.
    state: str = "on"
    # Whether an unreadable entity blocks. Blocking is the honest default --
    # a rule nobody can evaluate has not passed -- but a lux sensor that drops
    # out should not leave a path unlit, so it can be turned off.
    unknown_blocks: bool = True


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether the automation may act, and what decided it.

    The reason is for diagnostics and the log. "Blocked by After dark" is
    something somebody can go and look at; a bare False is a support thread.
    """

    allowed: bool
    blocked_by: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def evaluate(
    conditions: Sequence[Condition],
    now: datetime.datetime,
    states: Mapping[str, str | None],
) -> Verdict:
    """Whether every condition holds.

    ``states`` maps entity id to its state as a string; a missing key reads as
    unavailable, which is the same thing from here.
    """
    for condition in conditions:
        if not _holds(condition, now, states):
            return Verdict(False, condition.name or condition.condition_id)
    return Verdict(True)


def _holds(
    condition: Condition,
    now: datetime.datetime,
    states: Mapping[str, str | None],
) -> bool:
    if condition.kind is ConditionKind.TIME_WINDOW:
        return _within_window(condition, now)

    raw = states.get(condition.entity_id or "")
    if raw in _UNREADABLE:
        return not condition.unknown_blocks

    if condition.kind is ConditionKind.STATE_IS:
        return str(raw).casefold() == condition.state.casefold()

    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        # A sensor that is up but not a number is as unreadable as one that
        # is down, and is treated the same way rather than silently passing.
        return not condition.unknown_blocks

    if condition.kind is ConditionKind.BELOW:
        return value < condition.threshold
    return value > condition.threshold


def _within_window(condition: Condition, now: datetime.datetime) -> bool:
    """Whether the local time falls inside the window.

    A window whose end is earlier than its start runs through midnight, which
    is what almost every outdoor light actually wants: 22:00 to 06:00 is one
    night, not a contradiction.
    """
    start, end = condition.start_minute, condition.end_minute
    if start == end:
        return True
    minute = now.hour * 60 + now.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def minutes_from_text(text: str | None, fallback: int = 0) -> int:
    """Parse "HH:MM" -- or "HH:MM:SS", which is what a time selector sends."""
    if not text:
        return fallback
    parts = str(text).split(":")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return fallback
    return (hours % 24) * 60 + (minutes % 60)


def text_from_minutes(minute: int) -> str:
    """The inverse, for putting a stored window back into the form."""
    return f"{minute // 60 % 24:02d}:{minute % 60:02d}:00"
