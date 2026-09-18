"""Light groups: nesting, cycles, scene layering and group sending."""

from __future__ import annotations

import pytest

from custom_components.better_lighting.groups import (
    GroupTree,
    LightGroup,
    find_cycle,
    flatten_scene,
    route,
)
from custom_components.better_lighting.profiles import Axis
from custom_components.better_lighting.render import LightCommand
from custom_components.better_lighting.scenes import ALL_LIGHTS, Scene, SceneLightSpec

CEILING = "light.ceiling_group"

# A bedroom 3x3 of ceiling spots, built as three rows.
ROWS = {
    "front": ("light.a1", "light.a2", "light.a3"),
    "middle": ("light.b1", "light.b2", "light.b3"),
    "back": ("light.c1", "light.c2", "light.c3"),
}
MATRIX = [
    *(LightGroup(name, name.title(), lights=lights) for name, lights in ROWS.items()),
    LightGroup("ceiling", "Ceiling", groups=tuple(ROWS), send_entity=CEILING),
]
ALL_NINE = [light for lights in ROWS.values() for light in lights]


def cmd(entity_id: str, brightness: int = 100, action: str = "turn_on"):
    return LightCommand(entity_id, action, {"brightness": brightness}, Axis.BRIGHTNESS)


class TestNesting:
    def test_a_group_of_groups_reaches_every_light(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)

        assert tree.reaches("ceiling") == frozenset(ALL_NINE)
        assert tree.reaches("middle") == frozenset(ROWS["middle"])

    def test_a_light_knows_its_groups_outermost_first(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)

        assert tree.path_to("light.b2") == ("ceiling", "middle")

    def test_a_light_in_no_group_has_no_path(self) -> None:
        tree = GroupTree.build(MATRIX, [*ALL_NINE, "light.lamp"])

        assert tree.path_to("light.lamp") == ()

    def test_a_light_the_room_no_longer_has_is_dropped(self) -> None:
        """A group is a name for some of the room's lights, not a claim on one."""
        tree = GroupTree.build(
            MATRIX, [light for light in ALL_NINE if light != "light.b2"]
        )

        assert "light.b2" not in tree.reaches("ceiling")
        assert tree.reaches("middle") == frozenset({"light.b1", "light.b3"})

    def test_a_group_naming_a_deleted_group_is_not_a_cycle(self) -> None:
        tree = GroupTree.build(
            [LightGroup("ceiling", groups=("gone",), lights=("light.a1",))],
            ["light.a1"],
        )

        assert tree.reaches("ceiling") == frozenset({"light.a1"})


class TestCycles:
    def test_a_group_containing_itself(self) -> None:
        groups = {"a": LightGroup("a", groups=("a",))}

        assert find_cycle(groups) == ("a", "a")

    def test_a_longer_loop_is_named_in_full(self) -> None:
        """The message has to name the loop; "cannot contain itself" is useless."""
        groups = {
            "a": LightGroup("a", groups=("b",)),
            "b": LightGroup("b", groups=("c",)),
            "c": LightGroup("c", groups=("a",)),
        }

        assert find_cycle(groups) == ("a", "b", "c", "a")

    def test_the_run_up_to_a_loop_is_not_part_of_it(self) -> None:
        groups = {
            "outer": LightGroup("outer", groups=("a",)),
            "a": LightGroup("a", groups=("b",)),
            "b": LightGroup("b", groups=("a",)),
        }

        assert find_cycle(groups) == ("a", "b", "a")

    def test_a_shape_that_is_not_a_loop(self) -> None:
        """Two groups may hold the same row without that being a cycle."""
        groups = {
            "left": LightGroup("left", groups=("shared",)),
            "right": LightGroup("right", groups=("shared",)),
            "shared": LightGroup("shared", lights=("light.a1",)),
        }

        assert find_cycle(groups) is None

    def test_building_a_tree_refuses_a_cycle(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            GroupTree.build([LightGroup("a", groups=("a",))])


class TestScenesOverGroups:
    def test_the_whole_matrix_takes_one_row_in_a_scene(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)
        scene = Scene("s", groups={"ceiling": SceneLightSpec(brightness_pct=40)})

        flat = flatten_scene(scene, tree, ALL_NINE)

        assert all(flat.spec_for(light).brightness_pct == 40 for light in ALL_NINE)

    def test_an_inner_group_overrides_the_outer_one(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)
        scene = Scene(
            "s",
            groups={
                "ceiling": SceneLightSpec(brightness_pct=40),
                "middle": SceneLightSpec(brightness_pct=10),
            },
        )

        flat = flatten_scene(scene, tree, ALL_NINE)

        assert flat.spec_for("light.b2").brightness_pct == 10
        assert flat.spec_for("light.a2").brightness_pct == 40

    def test_one_light_beats_every_group_holding_it(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)
        scene = Scene(
            "s",
            groups={
                "ceiling": SceneLightSpec(brightness_pct=40),
                "middle": SceneLightSpec(brightness_pct=10),
            },
            lights={"light.b2": SceneLightSpec(turn_off=True)},
        )

        flat = flatten_scene(scene, tree, ALL_NINE)

        assert flat.spec_for("light.b2").turn_off is True
        assert flat.spec_for("light.b1").brightness_pct == 10

    def test_a_light_in_no_named_group_keeps_the_scene_value(self) -> None:
        tree = GroupTree.build(MATRIX, [*ALL_NINE, "light.lamp"])
        scene = Scene(
            "s",
            brightness_pct=75,
            groups={"middle": SceneLightSpec(brightness_pct=10)},
        )

        flat = flatten_scene(scene, tree, [*ALL_NINE, "light.lamp"])

        assert flat.spec_for("light.lamp").brightness_pct == 75

    def test_flattening_does_not_narrow_a_scene_that_named_no_lights(self) -> None:
        """An empty `lights` means every member; adding entries must not change that."""
        tree = GroupTree.build(MATRIX, [*ALL_NINE, "light.lamp"])
        scene = Scene("s", groups={"middle": SceneLightSpec(brightness_pct=10)})

        flat = flatten_scene(scene, tree, [*ALL_NINE, "light.lamp"])

        assert ALL_LIGHTS in flat.lights
        assert flat.targets("light.lamp")

    def test_a_scene_naming_no_group_is_returned_untouched(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)
        scene = Scene("s", brightness_pct=50, lights={"light.a1": SceneLightSpec()})

        assert flatten_scene(scene, tree, ALL_NINE) is scene


class TestGroupSending:
    def test_one_command_when_every_member_agrees(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)

        routed = route([cmd(light) for light in ALL_NINE], tree)

        assert [c.entity_id for c in routed] == [CEILING]

    def test_the_members_go_individually_when_one_differs(self) -> None:
        """A group holding three colours is exactly why per-bulb control exists."""
        tree = GroupTree.build(MATRIX, ALL_NINE)
        commands = [cmd(light) for light in ALL_NINE]
        commands[4] = cmd(ALL_NINE[4], brightness=10)

        routed = route(commands, tree)

        assert sorted(c.entity_id for c in routed) == sorted(ALL_NINE)

    def test_a_group_without_a_send_entity_is_left_alone(self) -> None:
        """Naming a bundle is useful on its own; sending needs a real entity."""
        tree = GroupTree.build(
            [LightGroup("row", "Row", lights=ROWS["middle"])], ROWS["middle"]
        )

        routed = route([cmd(light) for light in ROWS["middle"]], tree)

        assert sorted(c.entity_id for c in routed) == sorted(ROWS["middle"])

    def test_a_partly_addressed_group_is_left_alone(self) -> None:
        """Half a group on the group entity would light the other half too."""
        tree = GroupTree.build(MATRIX, ALL_NINE)

        routed = route([cmd(light) for light in ROWS["middle"]], tree)

        assert sorted(c.entity_id for c in routed) == sorted(ROWS["middle"])

    def test_the_largest_group_wins_over_the_rows_inside_it(self) -> None:
        tree = GroupTree.build(
            [
                *(
                    LightGroup(name, name, lights=lights, send_entity=f"light.{name}")
                    for name, lights in ROWS.items()
                ),
                LightGroup(
                    "ceiling", "Ceiling", groups=tuple(ROWS), send_entity=CEILING
                ),
            ],
            ALL_NINE,
        )

        routed = route([cmd(light) for light in ALL_NINE], tree)

        assert [c.entity_id for c in routed] == [CEILING]

    def test_rows_are_used_when_the_matrix_does_not_agree(self) -> None:
        tree = GroupTree.build(
            [
                *(
                    LightGroup(name, name, lights=lights, send_entity=f"light.{name}")
                    for name, lights in ROWS.items()
                ),
                LightGroup(
                    "ceiling", "Ceiling", groups=tuple(ROWS), send_entity=CEILING
                ),
            ],
            ALL_NINE,
        )
        commands = [
            cmd(light, 100 if light in ROWS["front"] else 10) for light in ALL_NINE
        ]

        routed = route(commands, tree)

        assert sorted(c.entity_id for c in routed) == [
            "light.back",
            "light.front",
            "light.middle",
        ]

    def test_ungrouped_lights_come_through_untouched(self) -> None:
        tree = GroupTree.build(MATRIX, [*ALL_NINE, "light.lamp"])

        routed = route([cmd(light) for light in [*ALL_NINE, "light.lamp"]], tree)

        assert sorted(c.entity_id for c in routed) == [CEILING, "light.lamp"]

    def test_turning_off_goes_through_the_group_too(self) -> None:
        tree = GroupTree.build(MATRIX, ALL_NINE)

        routed = route([cmd(light, action="turn_off") for light in ALL_NINE], tree)

        assert [(c.entity_id, c.action) for c in routed] == [(CEILING, "turn_off")]

    def test_no_groups_at_all_changes_nothing(self) -> None:
        commands = [cmd("light.a1"), cmd("light.a2")]

        assert route(commands, GroupTree.build([])) == commands
