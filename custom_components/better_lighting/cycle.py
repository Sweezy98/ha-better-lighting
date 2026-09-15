"""What the next press does.

This is requirements 1 and 8: a press turns the room on in adaptive mode, each
further press advances through *that switch's* ordered list, and two switches in
the same room can carry different lists.

The whole thing is a pure function of the controller's list and the zone's
current step, which makes the awkward cases -- pressing the door switch while
the kitchen switch's scene is showing, pressing during a mode nobody's list
contains, the first press after the room was switched off -- table-driven tests
rather than something you have to reason about in a running house.

**The dismiss rule.** A press that interrupts something automatic (an insect
scene, a cinema mode, a manual override) lands on adaptive *without advancing*.
The second press then advances normally. This is why there is no "user" layer
with a timeout: a press is an event that clears what is above it and rewrites
the base intent, and the clearing is naturally scoped -- an opt-out dies with
the mode, an insect dismissal dies when the window shuts.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StepKind(StrEnum):
    """The kinds of thing a cycle position can be."""

    ADAPTIVE = "adaptive"
    SCENE = "scene"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class Step:
    """One position in a controller's cycle."""

    kind: StepKind
    scene_id: str | None = None

    def __str__(self) -> str:
        return self.scene_id if self.kind is StepKind.SCENE else self.kind.value


ADAPTIVE = Step(StepKind.ADAPTIVE)
OFF = Step(StepKind.OFF)


def scene_step(scene_id: str) -> Step:
    return Step(StepKind.SCENE, scene_id)


class AdaptivePosition(StrEnum):
    """Where adaptive sits in the cycle, if at all."""

    FIRST = "first"
    LAST = "last"
    NONE = "none"


class ForeignPolicy(StrEnum):
    """What to do when the zone's current step is not in *this* controller's list.

    The common case is two switches in one room with different lists: you set
    Cooking from the switch by the oven, then press the one by the door.
    """

    # Start this controller's list from the top. Because the top is normally
    # adaptive, this also gives the right answer for insect, cinema and manual
    # modes -- one rule covering four requirements.
    RESTART = "restart"
    # Carry on from wherever this controller last left off.
    REMEMBER = "remember_position"


@dataclass(frozen=True, slots=True)
class CycleConfig:
    """A controller's ordered list, already expanded."""

    steps: tuple[Step, ...]
    on_foreign: ForeignPolicy = ForeignPolicy.RESTART
    wrap: bool = True

    def index_of(self, step: Step | None) -> int | None:
        if step is None:
            return None
        for index, candidate in enumerate(self.steps):
            if candidate == step:
                return index
        return None


def build_cycle(
    scene_ids: tuple[str, ...],
    *,
    adaptive_position: AdaptivePosition = AdaptivePosition.FIRST,
    off_at_end: bool = False,
    on_foreign: ForeignPolicy = ForeignPolicy.RESTART,
    wrap: bool = True,
) -> CycleConfig:
    """Expand a configured scene order into the full cycle."""
    steps: list[Step] = []
    if adaptive_position is AdaptivePosition.FIRST:
        steps.append(ADAPTIVE)
    steps.extend(scene_step(scene_id) for scene_id in scene_ids)
    if adaptive_position is AdaptivePosition.LAST:
        steps.append(ADAPTIVE)
    if off_at_end:
        steps.append(OFF)
    return CycleConfig(tuple(steps), on_foreign=on_foreign, wrap=wrap)


@dataclass(frozen=True, slots=True)
class ZoneCycleState:
    """What the zone is doing, as far as cycling is concerned."""

    current: Step | None = None
    is_off: bool = False
    # Where this particular controller last left off, for REMEMBER.
    last_index: int | None = None
    # What a power cycle should resume to, when the zone is configured to
    # resume its last scene rather than returning to adaptive.
    resume: Step | None = None


@dataclass(frozen=True, slots=True)
class PressResult:
    """Where the press lands, and why."""

    step: Step
    index: int | None
    reason: str


def press(
    config: CycleConfig, state: ZoneCycleState, *, dismissed: bool = False
) -> PressResult:
    """Where a single press should take the zone.

    ``dismissed`` means this press has just interrupted something automatic.
    """
    if not config.steps:
        # A controller with no list still has to do something useful.
        return PressResult(ADAPTIVE, None, "empty_cycle")

    if dismissed:
        # Requirement 2's "a press during movie mode switches that zone to
        # adaptive, then cycles normally": land, do not advance.
        return PressResult(ADAPTIVE, config.index_of(ADAPTIVE), "dismissed")

    if state.is_off:
        if state.resume is not None and config.index_of(state.resume) is not None:
            return PressResult(
                state.resume, config.index_of(state.resume), "resume_last"
            )
        if state.resume is not None:
            # The zone resumes a scene this controller does not carry. Honour
            # the zone's wish rather than this switch's list.
            return PressResult(state.resume, None, "resume_foreign")
        return PressResult(config.steps[0], 0, "turn_on")

    index = config.index_of(state.current)
    if index is None:
        if config.on_foreign is ForeignPolicy.REMEMBER and (
            state.last_index is not None
        ):
            index = state.last_index
        else:
            # RESTART: begin this controller's list from the top on the next
            # advance, which lands on adaptive for a normally-ordered list.
            index = -1

    return _advance(config, index, +1)


def press_previous(config: CycleConfig, state: ZoneCycleState) -> PressResult:
    """The mirror of :func:`press`, for a double-press or an explicit service."""
    if not config.steps:
        return PressResult(ADAPTIVE, None, "empty_cycle")
    index = config.index_of(state.current)
    if index is None:
        index = 0
    return _advance(config, index, -1)


def _advance(config: CycleConfig, index: int, direction: int) -> PressResult:
    nxt = index + direction
    if nxt >= len(config.steps):
        if not config.wrap:
            # A list that does not wrap ends by switching the room off, which
            # is the only sensible "past the end" answer.
            return PressResult(OFF, None, "end_of_cycle")
        nxt = 0
    elif nxt < 0:
        nxt = len(config.steps) - 1
    return PressResult(config.steps[nxt], nxt, "advance")
