"""Zones: parts of a room that can be told something different.

A living room is one room and several places. The couch and the desk are lit
together, switched together and adapt together -- until the desk is occupied
and the room is being darkened for a film, at which point the desk should
carry on being a desk and rejoin the room when whoever is working there leaves.

That is the whole of it, and it is why a zone is *not* a small room:

* A zone has no mode, no cycle and no scene list of its own by default. It
  follows its room, which is what "controlled as one entity" means.
* A zone becomes visible only when it has a reason to differ. Until then the
  room renders as one unit, exactly as it did before zones existed -- which is
  what keeps a room with no zones byte-identical to an earlier release.
* Rejoining is the default and detaching is the exception, so nothing has to
  be put back by hand. A zone detaches while its own condition holds and is
  back the moment it stops.

Membership is a subset of the room's lights. A light may be in at most one
zone of its room, for the same reason a light is in at most one room: two
answers to "what should this bulb be doing" is the bug, not the feature.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

__all__ = ["Zone", "ZonePlan", "plan_units"]


@dataclass(frozen=True, slots=True)
class Zone:
    """One part of a room, and the conditions under which it differs from it."""

    zone_id: str
    name: str = ""
    icon: str = "mdi:sofa-outline"
    # A subset of the room's lights. Anything the room no longer has is
    # ignored, as with a light group.
    lights: tuple[str, ...] = ()

    # -- when this zone stops following the room ---------------------------
    # Occupancy. A desk with somebody at it is the case this exists for.
    presence_entity: str | None = None
    # Seconds of no occupancy before the zone rejoins the room, so standing up
    # to fetch a coffee does not put the desk back into the film.
    presence_clear_delay: int = 120
    # Whether occupancy detaches this zone from a cross-room mode driving the
    # room. Off by default: a zone that merely exists should change nothing.
    detach_on_mode: bool = False
    # What the zone does while detached. None means "the lights, adaptively",
    # which is the sensible answer for a desk somebody is working at.
    detached_scene_id: str | None = None

    # -- what this zone answers for itself ---------------------------------
    # Sections the zone overrides rather than inherits. Everything not named
    # here follows the room, which is the point of a zone rather than a room.
    overrides: frozenset[str] = frozenset()
    # The overriding values, read only for the sections named above. Sparse:
    # a zone that overrides nothing stores nothing.
    settings: Mapping[str, object] = field(default_factory=dict)

    def overrides_section(self, section: str) -> bool:
        return section in self.overrides

    @property
    def own_curve(self) -> bool:
        """Whether this zone answers for its own adaptive curve.

        The same switch a room has against the hub: off, and every curve value
        the zone stores is ignored and the room's applies. A desk that wants
        to be brighter and cooler than the room it is in turns this on; a zone
        that is simply a part of the room leaves it alone.
        """
        return bool(self.settings.get("adaptive_override_enabled"))


@dataclass(frozen=True, slots=True)
class ZonePlan:
    """One group of a room's lights that renders together.

    A room with no zones produces exactly one plan holding every light and no
    zone, so the renderer sees what it always saw.
    """

    lights: tuple[str, ...]
    zone: Zone | None = None
    # True when this plan is a zone that has stepped out of what the room is
    # being told -- the desk, while somebody is at it and the film is on.
    detached: bool = False

    @property
    def zone_id(self) -> str | None:
        return self.zone.zone_id if self.zone else None


def plan_units(
    lights: Sequence[str],
    zones: Iterable[Zone],
    detached: Iterable[str] = (),
) -> list[ZonePlan]:
    """Split a room's lights into the groups that render together.

    Zones that are following the room are *not* split out: they are part of
    the room, and rendering them separately would only produce more service
    calls saying the same thing. Only a detached zone becomes a plan of its
    own, which is why a room whose zones are all behaving costs nothing.
    """
    zones = list(zones)
    if not zones:
        return [ZonePlan(tuple(lights))]

    stepped_out = set(detached)
    known = set(lights)
    plans: list[ZonePlan] = []
    spoken_for: set[str] = set()

    for zone in zones:
        if zone.zone_id not in stepped_out:
            continue
        mine = tuple(light for light in lights if light in zone.lights)
        if not mine:
            continue
        plans.append(ZonePlan(mine, zone, detached=True))
        spoken_for |= set(mine)

    rest = tuple(
        light for light in lights if light in known and light not in spoken_for
    )
    if rest:
        # The room itself, minus whatever has stepped out of it.
        plans.insert(0, ZonePlan(rest))
    return plans


def zone_of(zones: Iterable[Zone], entity_id: str) -> Zone | None:
    """The zone a light belongs to, if any."""
    for zone in zones:
        if entity_id in zone.lights:
            return zone
    return None


def overlapping_lights(zones: Sequence[Zone]) -> dict[str, list[str]]:
    """Lights claimed by more than one zone, by entity id.

    Refused at save time rather than resolved at render time: two answers to
    "what should this bulb be doing" is the bug that one-light-one-room exists
    to prevent, and a zone is the same argument one level down.
    """
    seen: dict[str, list[str]] = {}
    for zone in zones:
        for light in zone.lights:
            seen.setdefault(light, []).append(zone.name or zone.zone_id)
    return {light: names for light, names in seen.items() if len(names) > 1}
