"""Sessions: what a cross-zone mode remembers, and what it owes.

Two ideas live here, both pure so the awkward cases are ordinary tests.

**Snapshots.** Taken once, when a mode session begins, and kept verbatim across
every state change inside it. Re-snapshotting on "paused" would capture the
film-watching state, and the restore at the end would then relight nothing --
which is the failure requirement 2 is most obviously about.

**Deferred actions.** A room that should go dark but has somebody in it does
not go dark; the instruction waits for the room to empty. The interesting part
is everything that must *cancel* it: the session ended, the mode moved on, the
user took the room back, the room went dark by other means, or it simply waited
too long.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ZoneAction(StrEnum):
    """What a cross-zone mode does to one room in one of its states."""

    APPLY_SCENE = "apply_scene"
    TURN_OFF = "turn_off"
    # Leave the room exactly as it is. The default, so a mode only ever touches
    # rooms it was actually told about.
    KEEP = "keep"
    ADAPTIVE = "adaptive"


class RestoreMode(StrEnum):
    """How a mode puts things back when it ends."""

    # Relight only the lights that were on beforehand, at whatever the curve
    # says *now*. The snapshot supplies which lights; the adaptive engine
    # supplies how bright. Replaying stored brightness would immediately fight
    # the curve and be corrected a moment later.
    ADAPTIVE_ON_PREVIOUSLY_ON = "adaptive_on_previously_on"
    EXACT = "exact"
    NONE = "none"


class OptedOutOnExit(StrEnum):
    """What happens to a room the user took back mid-session."""

    KEEP = "keep"
    RESTORE = "restore"
    ADAPTIVE = "adaptive"


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LightSnapshotEntry:
    """One light, as it was."""

    entity_id: str
    is_on: bool
    brightness: int | None = None
    color: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "is_on": self.is_on,
            "brightness": self.brightness,
            "color": dict(self.color) if self.color else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> LightSnapshotEntry:
        return cls(
            entity_id=raw["entity_id"],
            is_on=bool(raw.get("is_on")),
            brightness=raw.get("brightness"),
            color=raw.get("color") or None,
        )


@dataclass(slots=True)
class ZoneSnapshot:
    """One room, as it was when the session began."""

    zone_id: str
    mode: str
    scene_id: str | None
    lights: tuple[LightSnapshotEntry, ...]
    # Cleared when the user takes the room back, so the exit leaves it alone.
    restore_on_exit: bool = True

    @property
    def previously_on(self) -> frozenset[str]:
        return frozenset(light.entity_id for light in self.lights if light.is_on)

    def as_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "mode": self.mode,
            "scene_id": self.scene_id,
            "restore_on_exit": self.restore_on_exit,
            "lights": [light.as_dict() for light in self.lights],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ZoneSnapshot:
        return cls(
            zone_id=raw["zone_id"],
            mode=raw.get("mode", "adaptive"),
            scene_id=raw.get("scene_id"),
            lights=tuple(
                LightSnapshotEntry.from_dict(light) for light in raw.get("lights", ())
            ),
            restore_on_exit=bool(raw.get("restore_on_exit", True)),
        )


@dataclass(slots=True)
class ModeSnapshot:
    """Everything one session needs in order to undo itself."""

    session_id: str
    mode_id: str
    taken_at: str
    zones: dict[str, ZoneSnapshot] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode_id": self.mode_id,
            "taken_at": self.taken_at,
            "zones": {
                zone_id: snapshot.as_dict() for zone_id, snapshot in self.zones.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ModeSnapshot:
        return cls(
            session_id=raw["session_id"],
            mode_id=raw["mode_id"],
            taken_at=raw.get("taken_at", ""),
            zones={
                zone_id: ZoneSnapshot.from_dict(snapshot)
                for zone_id, snapshot in (raw.get("zones") or {}).items()
            },
        )


# --------------------------------------------------------------------------
# Deferred actions
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeferredAction:
    """An instruction waiting for a room to empty."""

    zone_id: str
    session_id: str
    mode_id: str
    mode_state: str
    action: ZoneAction
    scene_id: str | None = None
    created_at: float = 0.0
    expires_at: float | None = None

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at


class DeferredRegistry:
    """At most one pending action per (session, room).

    A queue would only create ordering bugs: there is never a reason to hold
    two pending turn-offs for one room, so a second enqueue replaces the first.
    """

    def __init__(self) -> None:
        self._actions: dict[tuple[str, str], DeferredAction] = {}

    def __len__(self) -> int:
        return len(self._actions)

    def __iter__(self):
        return iter(self._actions.values())

    def enqueue(self, action: DeferredAction) -> None:
        self._actions[(action.session_id, action.zone_id)] = action

    def get(self, session_id: str, zone_id: str) -> DeferredAction | None:
        return self._actions.get((session_id, zone_id))

    def pop(self, session_id: str, zone_id: str) -> DeferredAction | None:
        return self._actions.pop((session_id, zone_id), None)

    def for_zone(self, zone_id: str) -> list[DeferredAction]:
        return [a for a in self._actions.values() if a.zone_id == zone_id]

    def drop_zone(self, zone_id: str) -> list[DeferredAction]:
        """The user took this room back, or it went dark by other means."""
        dropped = self.for_zone(zone_id)
        for action in dropped:
            self._actions.pop((action.session_id, action.zone_id), None)
        return dropped

    def drop_session(self, session_id: str) -> list[DeferredAction]:
        """The session ended, or moved to a state with different intentions."""
        dropped = [a for a in self._actions.values() if a.session_id == session_id]
        for action in dropped:
            self._actions.pop((action.session_id, action.zone_id), None)
        return dropped

    def drop_expired(self, now: float) -> list[DeferredAction]:
        """A stuck sensor must not leave an instruction armed forever."""
        expired = [a for a in self._actions.values() if a.is_expired(now)]
        for action in expired:
            self._actions.pop((action.session_id, action.zone_id), None)
        return expired

    def clear(self) -> None:
        self._actions.clear()


def is_still_wanted(
    action: DeferredAction,
    *,
    active_session_id: str | None,
    active_state: str | None,
    opted_out: Iterable[str],
    zone_is_off: bool,
    zone_is_manual: bool,
    now: float,
) -> tuple[bool, str]:
    """Re-check a deferred action at the moment it would fire.

    Validated here rather than at enqueue time, because everything interesting
    happens in between. Returns ``(wanted, reason)`` so a rejection can say why.
    """
    if active_session_id != action.session_id:
        return False, "session_ended"
    if active_state != action.mode_state:
        # The mode moved on. The new state's own rule decides afresh; replaying
        # the old one would apply an intention nobody holds any more.
        return False, "state_changed"
    if action.zone_id in opted_out:
        return False, "opted_out"
    if action.action is ZoneAction.TURN_OFF and zone_is_off:
        return False, "already_satisfied"
    if zone_is_manual:
        return False, "manual_override"
    if action.is_expired(now):
        return False, "expired"
    return True, "ok"
