"""Zones: which lights render together, and when one steps out of the room."""

from __future__ import annotations

from custom_components.better_lighting.zones import (
    Zone,
    overlapping_lights,
    plan_units,
    zone_of,
)

COUCH = Zone("couch", "Couch", lights=("light.lamp", "light.strip"))
DESK = Zone(
    "desk",
    "Desk",
    lights=("light.desk",),
    presence_entity="binary_sensor.desk",
    detach_on_mode=True,
)
LIVING_ROOM = ["light.lamp", "light.strip", "light.desk", "light.ceiling"]


class TestPlanningWithoutZones:
    def test_a_room_with_no_zones_is_one_unit(self) -> None:
        """The case that must stay exactly as it was before zones existed."""
        plans = plan_units(LIVING_ROOM, [])

        assert len(plans) == 1
        assert plans[0].lights == tuple(LIVING_ROOM)
        assert plans[0].zone is None

    def test_zones_that_are_behaving_do_not_split_the_room(self) -> None:
        """Following the room *is* being part of it; splitting would only talk more."""
        plans = plan_units(LIVING_ROOM, [COUCH, DESK])

        assert len(plans) == 1
        assert plans[0].lights == tuple(LIVING_ROOM)


class TestPlanningWithADetachedZone:
    def test_a_detached_zone_becomes_its_own_unit(self) -> None:
        plans = plan_units(LIVING_ROOM, [COUCH, DESK], detached=["desk"])

        assert [plan.zone_id for plan in plans] == [None, "desk"]
        assert plans[1].lights == ("light.desk",)
        assert plans[1].detached

    def test_the_rest_of_the_room_keeps_every_other_light(self) -> None:
        plans = plan_units(LIVING_ROOM, [COUCH, DESK], detached=["desk"])

        assert plans[0].lights == ("light.lamp", "light.strip", "light.ceiling")

    def test_two_zones_can_step_out_at_once(self) -> None:
        plans = plan_units(LIVING_ROOM, [COUCH, DESK], detached=["desk", "couch"])

        assert sorted(plan.zone_id or "" for plan in plans) == ["", "couch", "desk"]

    def test_a_room_entirely_detached_has_no_room_unit(self) -> None:
        plans = plan_units(["light.desk"], [DESK], detached=["desk"])

        assert [plan.zone_id for plan in plans] == ["desk"]

    def test_a_zone_naming_a_light_the_room_lost_is_ignored(self) -> None:
        plans = plan_units(["light.lamp"], [DESK], detached=["desk"])

        assert [plan.zone_id for plan in plans] == [None]

    def test_only_the_lights_asked_for_are_planned(self) -> None:
        """A render aimed at one light must not drag its zone's others in."""
        plans = plan_units(["light.desk"], [COUCH, DESK], detached=["desk"])

        assert [plan.lights for plan in plans] == [("light.desk",)]


class TestMembership:
    def test_a_light_knows_its_zone(self) -> None:
        assert zone_of([COUCH, DESK], "light.desk") is DESK
        assert zone_of([COUCH, DESK], "light.ceiling") is None

    def test_two_zones_claiming_one_light_are_reported(self) -> None:
        other = Zone("other", "Other", lights=("light.desk",))

        assert overlapping_lights([DESK, other]) == {"light.desk": ["Desk", "Other"]}

    def test_nothing_to_report_when_the_zones_are_disjoint(self) -> None:
        assert overlapping_lights([COUCH, DESK]) == {}


class TestInheritance:
    def test_a_zone_follows_the_room_unless_it_says_otherwise(self) -> None:
        assert not DESK.overrides_section("adaptive")

    def test_a_zone_can_answer_for_one_section(self) -> None:
        bright = Zone("desk", overrides=frozenset({"adaptive"}))

        assert bright.overrides_section("adaptive")
        assert not bright.overrides_section("night")
