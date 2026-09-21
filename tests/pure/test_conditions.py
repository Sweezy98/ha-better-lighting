"""Conditions: when an automation is allowed to act."""

from __future__ import annotations

import datetime

from custom_components.better_lighting.conditions import (
    Condition,
    ConditionKind,
    evaluate,
    minutes_from_text,
    text_from_minutes,
)


def at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 9, 21, hour, minute)


def window(start: str, end: str, **kw) -> Condition:
    return Condition(
        "w",
        kw.pop("name", "After dark"),
        ConditionKind.TIME_WINDOW,
        start_minute=minutes_from_text(start),
        end_minute=minutes_from_text(end),
        **kw,
    )


DARK = Condition(
    "lux", "Dark enough", ConditionKind.BELOW, entity_id="sensor.lux", threshold=10
)
ARMED = Condition(
    "armed",
    "Armed",
    ConditionKind.STATE_IS,
    entity_id="input_boolean.armed",
    state="on",
)


class TestTimeWindows:
    def test_inside_a_daytime_window(self) -> None:
        assert evaluate([window("09:00", "17:00")], at(12), {})

    def test_outside_a_daytime_window(self) -> None:
        assert not evaluate([window("09:00", "17:00")], at(8), {})

    def test_a_window_that_runs_through_midnight(self) -> None:
        """22:00 to 06:00 is one night, which is what an outdoor light wants."""
        rule = [window("22:00", "06:00")]

        assert evaluate(rule, at(23), {})
        assert evaluate(rule, at(2), {})
        assert not evaluate(rule, at(12), {})

    def test_the_start_is_inside_and_the_end_is_not(self) -> None:
        rule = [window("22:00", "06:00")]

        assert evaluate(rule, at(22, 0), {})
        assert not evaluate(rule, at(6, 0), {})

    def test_an_empty_window_is_always_true(self) -> None:
        """Equal times mean the whole day, not a contradiction nobody can satisfy."""
        assert evaluate([window("00:00", "00:00")], at(3), {})

    def test_a_window_needs_no_entity(self) -> None:
        assert evaluate([window("22:00", "06:00")], at(23), {})


class TestNumericThresholds:
    def test_below_a_threshold(self) -> None:
        assert evaluate([DARK], at(12), {"sensor.lux": "4"})

    def test_at_the_threshold_is_not_below_it(self) -> None:
        assert not evaluate([DARK], at(12), {"sensor.lux": "10"})

    def test_above_a_threshold(self) -> None:
        bright = Condition(
            "l", "Bright", ConditionKind.ABOVE, entity_id="sensor.lux", threshold=10
        )

        assert evaluate([bright], at(12), {"sensor.lux": "40"})
        assert not evaluate([bright], at(12), {"sensor.lux": "4"})

    def test_a_decimal_reading(self) -> None:
        assert evaluate([DARK], at(12), {"sensor.lux": "9.5"})


class TestStates:
    def test_an_entity_that_arms_the_automation(self) -> None:
        assert evaluate([ARMED], at(12), {"input_boolean.armed": "on"})
        assert not evaluate([ARMED], at(12), {"input_boolean.armed": "off"})

    def test_an_entity_that_disarms_it(self) -> None:
        """The same rule the other way round: the user picks which state counts."""
        away = Condition(
            "a",
            "Nobody home",
            ConditionKind.STATE_IS,
            entity_id="binary_sensor.home",
            state="off",
        )

        assert evaluate([away], at(12), {"binary_sensor.home": "off"})
        assert not evaluate([away], at(12), {"binary_sensor.home": "on"})

    def test_any_state_at_all_not_only_on_and_off(self) -> None:
        """ "After dark" without a lux sensor: the sun is an entity like any other."""
        night = Condition(
            "s",
            "Dark",
            ConditionKind.STATE_IS,
            entity_id="sun.sun",
            state="below_horizon",
        )

        assert evaluate([night], at(23), {"sun.sun": "below_horizon"})
        assert not evaluate([night], at(12), {"sun.sun": "above_horizon"})

    def test_case_does_not_matter(self) -> None:
        assert evaluate([ARMED], at(12), {"input_boolean.armed": "ON"})


class TestWhatCannotBeRead:
    def test_an_unavailable_sensor_blocks(self) -> None:
        """Allowed only if every rule passes, and this one has not passed."""
        assert not evaluate([DARK], at(12), {"sensor.lux": "unavailable"})

    def test_a_missing_entity_blocks(self) -> None:
        assert not evaluate([DARK], at(12), {})

    def test_a_sensor_that_is_not_a_number_blocks(self) -> None:
        """Up but reporting nonsense is as unreadable as being down."""
        assert not evaluate([DARK], at(12), {"sensor.lux": "very bright"})

    def test_a_condition_can_choose_to_let_it_through(self) -> None:
        """A flaky lux sensor should not leave somebody on an unlit path."""
        forgiving = Condition(
            "lux",
            "Dark enough",
            ConditionKind.BELOW,
            entity_id="sensor.lux",
            threshold=10,
            unknown_blocks=False,
        )

        assert evaluate([forgiving], at(12), {"sensor.lux": "unavailable"})


class TestCombining:
    def test_no_conditions_means_always(self) -> None:
        assert evaluate([], at(12), {})

    def test_every_one_has_to_hold(self) -> None:
        rules = [window("22:00", "06:00"), DARK, ARMED]
        states = {"sensor.lux": "4", "input_boolean.armed": "on"}

        assert evaluate(rules, at(23), states)
        assert not evaluate(rules, at(12), states)
        assert not evaluate(rules, at(23), {**states, "sensor.lux": "400"})
        assert not evaluate(rules, at(23), {**states, "input_boolean.armed": "off"})

    def test_the_verdict_names_what_blocked_it(self) -> None:
        """ "Blocked by Dark enough" is something somebody can go and look at."""
        verdict = evaluate(
            [window("22:00", "06:00"), DARK], at(23), {"sensor.lux": "400"}
        )

        assert not verdict
        assert verdict.blocked_by == "Dark enough"

    def test_the_first_failure_is_the_one_reported(self) -> None:
        verdict = evaluate(
            [window("22:00", "06:00"), DARK], at(12), {"sensor.lux": "400"}
        )

        assert verdict.blocked_by == "After dark"


class TestParsingTimes:
    def test_a_time_selector_sends_seconds_too(self) -> None:
        assert minutes_from_text("22:30:00") == 22 * 60 + 30

    def test_plain_hours_and_minutes(self) -> None:
        assert minutes_from_text("06:05") == 6 * 60 + 5

    def test_nothing_falls_back(self) -> None:
        assert minutes_from_text(None, 99) == 99
        assert minutes_from_text("", 99) == 99
        assert minutes_from_text("nonsense", 99) == 99

    def test_it_round_trips(self) -> None:
        assert text_from_minutes(minutes_from_text("22:30")) == "22:30:00"
        assert text_from_minutes(0) == "00:00:00"
