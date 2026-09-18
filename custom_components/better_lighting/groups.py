"""Light groups: a name for several bulbs, and a way to address them at once.

Two quite different needs, which is why this is one module and not two:

* **Sending.** Three smart bulbs in one fitting should change together. Asking
  Home Assistant to set three entities sends three Zigbee commands and you can
  watch them arrive. A Zigbee group entity is one multicast, so the fitting
  changes as one thing. :func:`route` swaps the members out for the group
  whenever every member is being told exactly the same thing, and leaves them
  alone the moment they differ -- which is what makes a scene with one red,
  one green and one blue bulb still possible inside a group that usually moves
  as a unit.

* **Naming.** A 3x3 ceiling matrix is three rows of three. Being able to say
  "the middle row" in a scene, and to build the matrix *out of* the rows, is
  the difference between a scene with two rows in it and a scene with nine.

Groups nest, so they can describe a cycle, so :func:`find_cycle` exists and the
save path refuses one rather than hanging.

A group is not a claim of ownership: the room's ``lights`` list is still the
only place membership is decided, and a light still belongs to exactly one
room. A group that names a light the room does not have is ignored rather than
being an error, for the same reason a controller pointing at a deleted scene
shortens its cycle rather than crashing.

Pure: imports nothing from ``homeassistant``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from .render import LightCommand
from .scenes import ALL_LIGHTS, Scene, SceneLightSpec

__all__ = [
    "GroupTree",
    "LightGroup",
    "find_cycle",
    "flatten_scene",
    "route",
]


@dataclass(frozen=True, slots=True)
class LightGroup:
    """Several of a room's lights, named, and optionally addressable at once."""

    group_id: str
    name: str = ""
    icon: str = "mdi:lightbulb-group"
    # Light entity ids. Anything the room does not have is ignored.
    lights: tuple[str, ...] = ()
    # Other groups of the same room, by group_id. This is what makes a 3x3 out
    # of three rows -- and what makes cycles possible.
    groups: tuple[str, ...] = ()
    # The entity that reaches every member in one command: a Zigbee group, a
    # Hue room, a light group helper. None means "address the members
    # individually", which is correct and merely slower.
    send_entity: str | None = None


def find_cycle(groups: Mapping[str, LightGroup]) -> tuple[str, ...] | None:
    """The first cycle among nested groups, outermost first, or None.

    Returned as a path rather than a bool so the message can name the loop:
    "Ceiling contains Middle row contains Ceiling" is actionable where "a group
    cannot contain itself" is not.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(groups, WHITE)

    def walk(group_id: str, path: list[str]) -> tuple[str, ...] | None:
        colour[group_id] = GREY
        path.append(group_id)
        for child in groups[group_id].groups:
            if child not in groups:
                continue  # A deleted group, not a cycle.
            if colour[child] == GREY:
                # Trim the run-up: only the loop itself is interesting.
                return (*path[path.index(child) :], child)
            if colour[child] == WHITE and (found := walk(child, path)) is not None:
                return found
        path.pop()
        colour[group_id] = BLACK
        return None

    for group_id in groups:
        if colour[group_id] == WHITE and (found := walk(group_id, [])) is not None:
            return found
    return None


@dataclass(frozen=True, slots=True)
class GroupTree:
    """A room's groups, resolved once so lookups are cheap and safe.

    Built through :meth:`build`, which refuses a cyclic definition. Nothing
    downstream has to think about cycles, because a tree cannot hold one.
    """

    groups: Mapping[str, LightGroup] = field(default_factory=dict)
    # Every light each group reaches, nested groups included.
    _reach: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # Each light's groups, outermost first. A light in no group has none.
    _paths: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        groups: Iterable[LightGroup],
        room_lights: Iterable[str] = (),
    ) -> GroupTree:
        """Resolve the groups of one room.

        ``room_lights``, when given, is the membership the room actually has:
        a group naming a light that has since moved rooms simply stops
        reaching it.
        """
        by_id = {group.group_id: group for group in groups}
        if (cycle := find_cycle(by_id)) is not None:
            raise ValueError(f"light groups form a cycle: {' -> '.join(cycle)}")

        known = frozenset(room_lights)
        reach: dict[str, frozenset[str]] = {}

        def resolve(group_id: str) -> frozenset[str]:
            if group_id in reach:
                return reach[group_id]
            group = by_id[group_id]
            lights = {light for light in group.lights if not known or light in known}
            for child in group.groups:
                if child in by_id:
                    lights |= resolve(child)
            reach[group_id] = frozenset(lights)
            return reach[group_id]

        for group_id in by_id:
            resolve(group_id)

        # Outermost first, so a scene's outer value is laid down before the
        # inner one overrides it. Depth is by reach: a group that contains
        # another necessarily reaches at least as many lights.
        paths: dict[str, list[str]] = {}
        for group_id, lights in reach.items():
            for light in lights:
                paths.setdefault(light, []).append(group_id)
        ordered = {
            light: tuple(sorted(ids, key=lambda gid: (-len(reach[gid]), gid)))
            for light, ids in paths.items()
        }
        return cls(by_id, reach, ordered)

    def reaches(self, group_id: str) -> frozenset[str]:
        """Every light this group reaches, nested groups included."""
        return self._reach.get(group_id, frozenset())

    def path_to(self, entity_id: str) -> tuple[str, ...]:
        """The groups holding this light, outermost first."""
        return self._paths.get(entity_id, ())

    def sendable(self) -> tuple[LightGroup, ...]:
        """Groups that can be addressed in one command, largest first.

        Largest first so the ceiling is tried before one of its rows: sending
        one command beats sending three.
        """
        return tuple(
            sorted(
                (
                    g
                    for g in self.groups.values()
                    if g.send_entity and self.reaches(g.group_id)
                ),
                key=lambda g: (-len(self.reaches(g.group_id)), g.group_id),
            )
        )


def _merge(under: SceneLightSpec, over: SceneLightSpec) -> SceneLightSpec:
    """``over`` wins wherever it says anything."""
    return SceneLightSpec(
        brightness_pct=(
            over.brightness_pct
            if over.brightness_pct is not None
            else under.brightness_pct
        ),
        color=over.color if over.color is not None else under.color,
        turn_off=over.turn_off or under.turn_off,
        skip=over.skip or under.skip,
    )


def flatten_scene(scene: Scene, tree: GroupTree, room_lights: Iterable[str]) -> Scene:
    """Resolve a scene's group entries into per-light ones.

    Everything downstream keeps working in entities, which is why this is a
    translation at the edge rather than a third case inside the renderer. A
    light takes the outermost group's value first and the innermost last, and
    its own entry beats all of them -- so "the whole ceiling amber, the middle
    row dim, that one bulb off" reads the way it sounds.

    A scene that names no group is returned unchanged, so nothing that worked
    before can be affected by this at all.
    """
    if not scene.groups:
        return scene

    lights = dict(scene.lights)
    # An empty `lights` means every member. Adding entries would silently
    # narrow that, so say "everything" explicitly before adding any.
    if not lights:
        lights[ALL_LIGHTS] = SceneLightSpec()

    for entity_id in room_lights:
        layers = [
            scene.groups[group_id]
            for group_id in tree.path_to(entity_id)
            if group_id in scene.groups
        ]
        if not layers:
            continue
        spec = layers[0]
        for layer in layers[1:]:
            spec = _merge(spec, layer)
        if (own := scene.lights.get(entity_id)) is not None:
            spec = _merge(spec, own)
        lights[entity_id] = spec

    return replace(scene, lights=lights, groups={})


def route(commands: Sequence[LightCommand], tree: GroupTree) -> list[LightCommand]:
    """Send through a group entity wherever every member is told the same thing.

    The point is the wire, not the code: three entities is three Zigbee
    commands and the bulbs visibly stagger, where the group is one multicast.
    So this only fires when the whole group agrees -- identical action and
    identical payload. One bulb differing means the group is being used to
    hold different colours, and then addressing it as a unit would be wrong.

    Groups are tried largest first, and a light already covered is not
    revisited, so a 3x3 goes as one command rather than as three rows.
    """
    by_entity = {command.entity_id: command for command in commands}
    taken: set[str] = set()
    routed: list[LightCommand] = []

    for group in tree.sendable():
        members = tree.reaches(group.group_id)
        if members & taken or not members <= by_entity.keys():
            continue
        first = by_entity[next(iter(members))]
        if any(not _same(first, by_entity[member]) for member in members):
            continue
        taken |= members
        routed.append(
            replace(
                first,
                entity_id=group.send_entity or "",
                reason=f"{first.reason} via {group.name or group.group_id}".strip(),
                members=tuple(sorted(members)),
            )
        )

    routed += [c for c in commands if c.entity_id not in taken]
    return routed


def _same(one: LightCommand, other: LightCommand) -> bool:
    """Whether two commands would leave their lights in the same state."""
    return one.action == other.action and one.data == other.data
