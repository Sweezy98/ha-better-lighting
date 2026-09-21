"""Presence simulation: replaying a recorded day without repeating it."""

from __future__ import annotations

import datetime
import random

from custom_components.better_lighting.simulation import (
    RecordedState,
    ReplayStep,
    build_timeline,
    next_due,
    window_for,
)

START = datetime.datetime(2026, 9, 14, 18, 0)  # A Monday evening.


def at(minutes: float, *, on: bool = True, pct: float | None = None) -> RecordedState:
    return RecordedState(START + datetime.timedelta(minutes=minutes), on, pct)


class TestTheWindow:
    def test_a_week_back_is_the_same_weekday(self) -> None:
        """A Tuesday evening looks like other Tuesday evenings, not like a Sunday."""
        now = datetime.datetime(2026, 9, 21, 18, 30)

        start, _end = window_for(now)

        assert start.weekday() == now.weekday()
        assert start == datetime.datetime(2026, 9, 14, 18, 30)

    def test_it_runs_a_full_day_forward(self) -> None:
        """So a simulation started in the evening carries on past midnight."""
        start, end = window_for(datetime.datetime(2026, 9, 21, 18, 30))

        assert end - start == datetime.timedelta(days=1)

    def test_how_far_back_can_be_asked_for(self) -> None:
        start, _end = window_for(datetime.datetime(2026, 9, 21, 12, 0), days_back=14)

        assert start == datetime.datetime(2026, 9, 7, 12, 0)


class TestBuildingATimeline:
    def test_offsets_are_measured_from_the_start_of_the_window(self) -> None:
        steps = build_timeline([at(30, pct=50)], START, jitter=0)

        assert steps == (ReplayStep(1800.0, True, 50.0),)

    def test_a_state_already_held_lands_at_the_start(self) -> None:
        """History includes what the room was already doing; that is not the past."""
        before = RecordedState(START - datetime.timedelta(hours=2), True, 40)

        steps = build_timeline([before], START, jitter=0)

        assert steps[0].after == 0.0

    def test_runs_of_the_same_look_collapse(self) -> None:
        """An hour of a room being on is one instruction, not sixty."""
        recorded = [at(0, pct=50), at(10, pct=50), at(20, pct=50), at(30, pct=80)]

        steps = build_timeline(recorded, START, jitter=0)

        assert [step.brightness_pct for step in steps] == [50.0, 80.0]

    def test_a_percent_of_drift_is_not_a_change(self) -> None:
        """A recorded day is full of the curve's own one-percent movement."""
        recorded = [at(0, pct=50.2), at(10, pct=49.8), at(20, pct=50.4)]

        steps = build_timeline(recorded, START, jitter=0)

        assert len(steps) == 1

    def test_going_off_is_a_step(self) -> None:
        steps = build_timeline([at(0, pct=50), at(60, on=False)], START, jitter=0)

        assert [step.on for step in steps] == [True, False]

    def test_off_carries_no_brightness(self) -> None:
        steps = build_timeline([at(0, on=False, pct=50)], START, jitter=0)

        assert steps[0].brightness_pct is None

    def test_nothing_recorded_is_nothing_to_do(self) -> None:
        assert build_timeline([], START) == ()


class TestJitter:
    def test_it_moves_things(self) -> None:
        """The point: not the same time as last week, to the second."""
        recorded = [at(60, pct=50)]

        steps = build_timeline(recorded, START, jitter=600, rng=random.Random(1))

        assert steps[0].after != 3600.0
        assert abs(steps[0].after - 3600.0) <= 600.0

    def test_each_step_moves_by_its_own_amount(self) -> None:
        """Shifting the whole day together is still exactly last week."""
        recorded = [at(m, pct=10 * (m // 30 + 1)) for m in (0, 30, 60, 90)]

        steps = build_timeline(recorded, START, jitter=600, rng=random.Random(7))
        shifts = {
            round(step.after - 1800.0 * index, 3) for index, step in enumerate(steps)
        }

        assert len(shifts) > 1

    def test_the_order_survives_it(self) -> None:
        """A light must not go off before it came on, however the dice fall."""
        recorded = [at(0, pct=50), at(0.2, on=False)]

        for seed in range(30):
            steps = build_timeline(recorded, START, jitter=900, rng=random.Random(seed))
            assert [step.after for step in steps] == sorted(
                step.after for step in steps
            )

    def test_nothing_is_scheduled_before_the_start(self) -> None:
        steps = build_timeline(
            [at(1, pct=50)], START, jitter=3600, rng=random.Random(3)
        )

        assert all(step.after >= 0 for step in steps)

    def test_no_jitter_is_exactly_last_week(self) -> None:
        steps = build_timeline([at(45, pct=50)], START, jitter=0)

        assert steps[0].after == 2700.0


class TestTheHorizon:
    def test_steps_beyond_it_are_dropped(self) -> None:
        recorded = [at(10, pct=50), at(600, pct=20)]

        steps = build_timeline(recorded, START, jitter=0, horizon=3600)

        assert len(steps) == 1


class TestWhatIsNext:
    def test_the_first_one_still_ahead(self) -> None:
        steps = (ReplayStep(10, True), ReplayStep(20, False), ReplayStep(30, True))

        assert next_due(steps, 15).after == 20

    def test_nothing_left(self) -> None:
        assert next_due((ReplayStep(10, True),), 50) is None
