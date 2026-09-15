"""What the next press does. Pure: no hass fixture, no event loop."""

from __future__ import annotations

from custom_components.better_lighting.cycle import (
    ADAPTIVE,
    OFF,
    AdaptivePosition,
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
OVEN = build_cycle(("cooking", "bright"))
DOOR = build_cycle(("dining", "movie"))


class TestBuildCycle:
    def test_adaptive_leads_by_default(self):
        assert OVEN.steps == (ADAPTIVE, COOKING, BRIGHT)

    def test_adaptive_can_trail(self):
        cycle = build_cycle(("cooking",), adaptive_position=AdaptivePosition.LAST)
        assert cycle.steps == (COOKING, ADAPTIVE)

    def test_adaptive_can_be_left_out(self):
        cycle = build_cycle(("cooking",), adaptive_position=AdaptivePosition.NONE)
        assert cycle.steps == (COOKING,)

    def test_off_can_close_the_cycle(self):
        cycle = build_cycle(("cooking",), off_at_end=True)
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
        cycle = build_cycle(("cooking",), wrap=False)
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
        door = build_cycle(("dining", "movie"), on_foreign=ForeignPolicy.REMEMBER)
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
        cycle = build_cycle((), adaptive_position=AdaptivePosition.NONE)
        assert press(cycle, ZoneCycleState()).step == ADAPTIVE
        assert press_previous(cycle, ZoneCycleState()).step == ADAPTIVE

    def test_a_single_step_cycle_stays_put(self):
        cycle = build_cycle((), adaptive_position=AdaptivePosition.FIRST)
        assert press(cycle, ZoneCycleState(current=ADAPTIVE)).step == ADAPTIVE

    def test_a_cycle_without_adaptive_still_advances(self):
        cycle = build_cycle(
            ("cooking", "bright"), adaptive_position=AdaptivePosition.NONE
        )
        assert press(cycle, ZoneCycleState(current=COOKING)).step == BRIGHT
        # And a dismissing press still returns to adaptive, even though the
        # list has no adaptive position of its own.
        assert press(cycle, ZoneCycleState(current=COOKING), dismissed=True).step == (
            ADAPTIVE
        )
