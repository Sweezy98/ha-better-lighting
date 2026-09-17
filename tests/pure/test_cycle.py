"""What the next press does. Pure: no hass fixture, no event loop."""

from __future__ import annotations

from custom_components.better_lighting.cycle import (
    ADAPTIVE,
    ADAPTIVE_STEP,
    OFF,
    ForeignPolicy,
    ZoneCycleState,
    build_cycle,
    press,
    press_previous,
    scene_step,
)

COOKING = scene_step("cooking")
BRIGHT = scene_step("bright")
DINING = scene_step("dining")
MOVIE = scene_step("movie")

# The worked example from the requirements: two switches in one kitchen.
OVEN = build_cycle((ADAPTIVE_STEP, "cooking", "bright"))
DOOR = build_cycle((ADAPTIVE_STEP, "dining", "movie"))


class TestBuildCycle:
    def test_adaptive_leads_by_default(self):
        assert OVEN.steps == (ADAPTIVE, COOKING, BRIGHT)

    def test_adaptive_can_sit_anywhere(self):
        """It is an entry in the list now, not a setting about the list --
        which is what lets a switch put it between two scenes."""
        assert build_cycle(("cooking", ADAPTIVE_STEP)).steps == (COOKING, ADAPTIVE)
        assert build_cycle(("cooking", ADAPTIVE_STEP, "bright")).steps == (
            COOKING,
            ADAPTIVE,
            BRIGHT,
        )

    def test_adaptive_can_be_left_out(self):
        """A switch for one scene and nothing else: a reading light."""
        assert build_cycle(("cooking",)).steps == (COOKING,)

    def test_off_can_close_the_cycle(self):
        cycle = build_cycle((ADAPTIVE_STEP, "cooking"), off_at_end=True)
        assert cycle.steps == (ADAPTIVE, COOKING, OFF)


class TestFirstPress:
    def test_a_dark_room_turns_on_adaptive(self):
        result = press(OVEN, ZoneCycleState(is_off=True))
        assert result.step == ADAPTIVE
        assert result.reason == "turn_on"

    def test_power_cycle_can_resume_the_last_scene(self):
        result = press(OVEN, ZoneCycleState(is_off=True, resume=COOKING))
        assert result.step == COOKING
        assert result.reason == "resume_last"

    def test_resuming_a_scene_this_switch_does_not_carry(self):
        """The zone's wish outranks this particular switch's list."""
        result = press(OVEN, ZoneCycleState(is_off=True, resume=MOVIE))
        assert result.step == MOVIE
        assert result.reason == "resume_foreign"


class TestCycling:
    def test_each_press_advances(self):
        first = press(OVEN, ZoneCycleState(current=ADAPTIVE))
        assert first.step == COOKING
        second = press(OVEN, ZoneCycleState(current=COOKING))
        assert second.step == BRIGHT

    def test_the_last_step_wraps_to_the_start(self):
        result = press(OVEN, ZoneCycleState(current=BRIGHT))
        assert result.step == ADAPTIVE

    def test_a_non_wrapping_cycle_ends_by_switching_off(self):
        cycle = build_cycle((ADAPTIVE_STEP, "cooking"), wrap=False)
        result = press(cycle, ZoneCycleState(current=COOKING))
        assert result.step == OFF
        assert result.reason == "end_of_cycle"

    def test_previous_walks_backwards(self):
        assert press_previous(OVEN, ZoneCycleState(current=BRIGHT)).step == COOKING

    def test_previous_wraps_to_the_end(self):
        assert press_previous(OVEN, ZoneCycleState(current=ADAPTIVE)).step == BRIGHT


class TestTwoSwitchesOneRoom:
    """Requirement 8, as the requirement describes it."""

    def test_the_oven_switch_reaches_cooking_first(self):
        assert press(OVEN, ZoneCycleState(current=ADAPTIVE)).step == COOKING

    def test_the_door_switch_reaches_dining_first(self):
        assert press(DOOR, ZoneCycleState(current=ADAPTIVE)).step == DINING

    def test_pressing_the_other_switch_restarts_its_own_list(self):
        # Cooking was set from the oven switch; the door switch does not carry
        # it, so by default it starts from the top -- which is adaptive.
        result = press(DOOR, ZoneCycleState(current=COOKING))
        assert result.step == ADAPTIVE

        # And the next press moves into the door switch's own list.
        assert press(DOOR, ZoneCycleState(current=ADAPTIVE)).step == DINING

    def test_remember_policy_continues_where_it_left_off(self):
        door = build_cycle(
            (ADAPTIVE_STEP, "dining", "movie"), on_foreign=ForeignPolicy.REMEMBER
        )
        result = press(door, ZoneCycleState(current=COOKING, last_index=1))
        assert result.step == MOVIE


class TestDismissal:
    """A press interrupting something automatic lands on adaptive, no advance."""

    def test_a_dismissing_press_does_not_advance(self):
        result = press(OVEN, ZoneCycleState(current=COOKING), dismissed=True)
        assert result.step == ADAPTIVE
        assert result.reason == "dismissed"

    def test_the_following_press_cycles_normally(self):
        # The exact sequence requirement 2 describes for a press during a movie.
        first = press(OVEN, ZoneCycleState(current=MOVIE), dismissed=True)
        assert first.step == ADAPTIVE
        second = press(OVEN, ZoneCycleState(current=first.step))
        assert second.step == COOKING

    def test_dismissal_beats_being_off(self):
        result = press(OVEN, ZoneCycleState(is_off=True, resume=BRIGHT), dismissed=True)
        assert result.step == ADAPTIVE


class TestForeignModes:
    """Insect, cinema and manual are all "not in my list", and resolve the same."""

    def test_an_unknown_current_step_restarts_the_list(self):
        result = press(OVEN, ZoneCycleState(current=scene_step("insect_amber")))
        assert result.step == ADAPTIVE

    def test_no_current_step_at_all_restarts_the_list(self):
        assert press(OVEN, ZoneCycleState(current=None)).step == ADAPTIVE


class TestDegenerateCycles:
    def test_an_empty_list_still_gives_adaptive(self):
        cycle = build_cycle(())
        assert press(cycle, ZoneCycleState()).step == ADAPTIVE
        assert press_previous(cycle, ZoneCycleState()).step == ADAPTIVE

    def test_a_single_step_cycle_stays_put(self):
        cycle = build_cycle((ADAPTIVE_STEP,))
        assert press(cycle, ZoneCycleState(current=ADAPTIVE)).step == ADAPTIVE

    def test_a_cycle_without_adaptive_still_advances(self):
        cycle = build_cycle(("cooking", "bright"))
        assert press(cycle, ZoneCycleState(current=COOKING)).step == BRIGHT
        # And a dismissing press still returns to adaptive, even though the
        # list has no adaptive position of its own.
        assert press(cycle, ZoneCycleState(current=COOKING), dismissed=True).step == (
            ADAPTIVE
        )


class TestWhatASwitchCycles:
    """A switch's list against the room's scenes.

    The rule has to answer two questions with one piece of stored data: what
    has this switch not been told about yet, and what has it been told to
    leave out. Getting them the same way round is how a scene somebody
    removed comes back on its own.
    """

    def test_a_switch_with_no_list_cycles_the_whole_room(self) -> None:
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order((), (), ["a", "b", "c"]) == (
            ADAPTIVE_STEP,
            "a",
            "b",
            "c",
        )

    def test_a_list_keeps_its_own_order(self) -> None:
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order(
            [ADAPTIVE_STEP, "c", "a"], ["b"], ["a", "b", "c"]
        ) == (ADAPTIVE_STEP, "c", "a")

    def test_a_new_scene_joins_the_end(self) -> None:
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order(
            [ADAPTIVE_STEP, "c", "a"], ["b"], ["a", "b", "c", "d"]
        ) == (ADAPTIVE_STEP, "c", "a", "d")

    def test_a_removed_scene_stays_removed(self) -> None:
        """The whole reason removals are remembered separately: otherwise the
        next scene added to the room brings every removed one back with it."""
        from custom_components.better_lighting.models import effective_scene_order

        assert "b" not in effective_scene_order(["a", "c"], ["b"], ["a", "b", "c", "d"])

    def test_a_deleted_scene_leaves_both_lists(self) -> None:
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order(
            [ADAPTIVE_STEP, "a", "gone"], ["also_gone"], ["a", "b"]
        ) == (ADAPTIVE_STEP, "a", "b")


class TestAdaptiveAsAnEntry:
    """Adaptive is a step in the list rather than a setting about the list.

    Which is what lets a switch put it between two scenes, leave it out for a
    switch that only ever does one thing, and get it back afterwards.
    """

    def test_a_switch_written_before_this_keeps_where_it_had_it(self) -> None:
        from custom_components.better_lighting.cycle import AdaptivePosition
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order(["a"], (), ["a"], AdaptivePosition.LAST) == (
            "a",
            ADAPTIVE_STEP,
        )
        assert effective_scene_order(["a"], (), ["a"], AdaptivePosition.NONE) == ("a",)

    def test_it_can_be_taken_out_and_stays_out(self) -> None:
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order(["a", "b"], [ADAPTIVE_STEP], ["a", "b"]) == (
            "a",
            "b",
        )

    def test_and_put_back_where_it_is_wanted(self) -> None:
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order(["a", ADAPTIVE_STEP, "b"], (), ["a", "b"]) == (
            "a",
            ADAPTIVE_STEP,
            "b",
        )

    def test_a_list_is_never_empty(self) -> None:
        """Deleting the last scene from a switch leaves adaptive rather than
        a switch that does nothing at all."""
        from custom_components.better_lighting.models import effective_scene_order

        assert effective_scene_order((), [ADAPTIVE_STEP], []) == (ADAPTIVE_STEP,)
