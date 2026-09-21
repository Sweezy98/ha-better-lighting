"""Presence simulation: making an empty house look lived in.

The convincing version of this is not "turn some lights on at random". It is
*last week*: the same weekday, because a Tuesday evening in a house looks like
other Tuesday evenings, replayed with enough jitter that a watcher cannot set
their watch by it. A house whose hall light comes on at 19:03:12 every single
evening is advertising that nobody is home rather than the reverse.

So a replayed day is the recorded one, shifted. Every step keeps its place in
the order -- the lights still come on before they go off -- but each moves by
its own random amount, and the whole day can be nudged as well.

Three things a room can do while the house is empty, and the choice is the
room's: light adaptively, show a named scene, or replay itself. A bathroom
nobody can see from the street can sit it out entirely.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

import datetime
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "RecordedState",
    "ReplayStep",
    "SimulationMode",
    "build_timeline",
    "window_for",
]


class SimulationMode(StrEnum):
    """What one room does while the house is empty."""

    # Sit it out. A bathroom nobody can see from outside proves nothing.
    NONE = "none"
    # The lights, adaptively -- steady, and obviously so.
    ADAPTIVE = "adaptive"
    # A named scene of this room's.
    SCENE = "scene"
    # What this room actually did on the same weekday, shifted.
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class RecordedState:
    """One entry from a room's own history: when, and how it looked."""

    when: datetime.datetime
    on: bool
    brightness_pct: float | None = None


@dataclass(frozen=True, slots=True)
class ReplayStep:
    """One thing to do, this many seconds after the replay starts."""

    after: float
    on: bool
    brightness_pct: float | None = None


def window_for(
    now: datetime.datetime, days_back: int = 7
) -> tuple[datetime.datetime, datetime.datetime]:
    """The stretch of history to replay from: the same clock time, N days ago.

    A week by default, so a Tuesday is replayed from a Tuesday. The window
    runs to the same moment the following day, which is what lets a simulation
    started at eight in the evening carry on past midnight without asking for
    history twice.
    """
    start = now - datetime.timedelta(days=days_back)
    return start, start + datetime.timedelta(days=1)


def build_timeline(
    recorded: Sequence[RecordedState],
    window_start: datetime.datetime,
    *,
    jitter: float = 900.0,
    rng: random.Random | None = None,
    horizon: float | None = None,
) -> tuple[ReplayStep, ...]:
    """Turn a recorded day into things to do, each moved by its own amount.

    ``jitter`` is the most any one step may move, in seconds, in either
    direction. Applied per step rather than to the day as a whole: shifting
    everything by the same twelve minutes is still a recording, and still
    exactly as regular as the week before.

    Order is preserved after jittering. A light that came on and went off
    twenty seconds later must not end up going off before it came on, and
    sorting is a cheaper answer to that than rejecting the sample.

    Runs of the same thing collapse: an hour of a room being on is one
    instruction, not one per recorded row.
    """
    random_source = rng or random.Random()
    steps: list[ReplayStep] = []
    previous: tuple[bool, float | None] | None = None

    for entry in recorded:
        look = (entry.on, _rounded(entry.brightness_pct) if entry.on else None)
        if look == previous:
            continue
        previous = look
        offset = (entry.when - window_start).total_seconds()
        if offset < 0:
            # The state the room was already in when the window opened. It
            # belongs at the start rather than before it.
            offset = 0.0
        moved = offset + random_source.uniform(-jitter, jitter)
        steps.append(ReplayStep(max(0.0, moved), look[0], look[1]))

    steps.sort(key=lambda step: step.after)
    if horizon is not None:
        steps = [step for step in steps if step.after <= horizon]
    return tuple(steps)


def _rounded(pct: float | None) -> float | None:
    """Brightness to the nearest whole percent.

    A recorded day is full of one-percent drift from the adaptive curve
    itself. Replaying every one of those is a great many service calls saying
    nothing anybody could see.
    """
    return None if pct is None else float(round(pct))


def next_due(steps: Iterable[ReplayStep], elapsed: float) -> ReplayStep | None:
    """The first step still ahead of us, or None when the day is done."""
    return next((step for step in steps if step.after > elapsed), None)
