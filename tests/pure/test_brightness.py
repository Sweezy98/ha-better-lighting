"""Tests for the relative brightness maths. Pure: no hass fixture, no event loop."""

from __future__ import annotations

import pytest

from custom_components.better_lighting.brightness import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    BrightnessStrategy,
    base_relative_brightness_map,
    group_by_brightness,
    relative_brightness_map,
    representative_brightness,
)


class TestRepresentativeBrightness:
    def test_returns_none_without_values(self):
        assert representative_brightness({}) is None
        assert representative_brightness({"light.a": None}) is None

    @pytest.mark.parametrize(
        ("strategy", "expected"),
        [
            (BrightnessStrategy.AVERAGE, 100),
            (BrightnessStrategy.MEDIAN, 100),
            (BrightnessStrategy.MAX, 150),
            (BrightnessStrategy.MIN, 50),
        ],
    )
    def test_strategies(self, strategy, expected):
        values = {"light.a": 50, "light.b": 100, "light.c": 150}
        assert representative_brightness(values, strategy) == expected

    def test_ignores_members_without_brightness(self):
        values = {"light.a": 100, "light.b": None, "light.c": 200}
        assert representative_brightness(values) == 150

    def test_clamps_out_of_range_inputs(self):
        # Some integrations report 0 or >255; neither should skew the result.
        assert representative_brightness({"light.a": 0}) == BRIGHTNESS_MIN
        assert representative_brightness({"light.a": 999}) == BRIGHTNESS_MAX


class TestRelativeBrightnessMap:
    def test_no_change_returns_empty(self):
        assert relative_brightness_map({"light.a": 100}, 100, 100) == {}

    def test_no_current_brightness_returns_empty(self):
        assert relative_brightness_map({"light.a": 100}, 0, 200) == {}

    def test_no_headroom_returns_empty(self):
        assert relative_brightness_map({"light.a": 255}, 255, 255) == {}

    def test_brightening_moves_each_member_by_its_own_headroom_fraction(self):
        # Group at 100 asked for 177 consumes half of its 155 headroom, so each
        # member should also travel half of its own headroom.
        result = relative_brightness_map(
            {"light.dim": 50, "light.bright": 200}, 100, 177
        )
        assert result["light.dim"] == pytest.approx(50 + (255 - 50) * 0.5, abs=1)
        assert result["light.bright"] == pytest.approx(200 + (255 - 200) * 0.5, abs=1)

    def test_dimming_moves_each_member_by_its_own_fraction(self):
        # Group 100 -> 50 gives up half of its downward headroom.
        result = relative_brightness_map(
            {"light.dim": 50, "light.bright": 200}, 100, 50
        )
        assert result["light.dim"] == pytest.approx(25, abs=1)
        assert result["light.bright"] == pytest.approx(100, abs=1)

    def test_members_reach_the_ceiling_together(self):
        result = relative_brightness_map({"light.a": 10, "light.b": 240}, 125, 255)
        assert result == {"light.a": BRIGHTNESS_MAX, "light.b": BRIGHTNESS_MAX}

    def test_dim_to_zero_never_reaches_off(self):
        result = relative_brightness_map({"light.a": 10, "light.b": 240}, 125, 0)
        assert all(v >= BRIGHTNESS_MIN for v in result.values())

    def test_member_without_brightness_gets_the_absolute_target(self):
        result = relative_brightness_map({"light.a": 100, "light.plug": None}, 100, 200)
        assert result["light.plug"] == 200

    def test_never_leaves_the_valid_range(self):
        result = relative_brightness_map({"light.a": 1, "light.b": 255}, 128, 255)
        assert all(BRIGHTNESS_MIN <= v <= BRIGHTNESS_MAX for v in result.values())


class TestBaseRelativeBrightnessMap:
    def test_empty_base_returns_empty(self):
        assert base_relative_brightness_map({}, 100) == {}

    def test_dimming_preserves_ratios_exactly(self):
        # A pure proportional scale: halving the group halves every member.
        result = base_relative_brightness_map({"light.a": 100, "light.b": 200}, 75)
        assert result == {"light.a": 50, "light.b": 100}

    def test_brightening_distributes_headroom(self):
        result = base_relative_brightness_map({"light.a": 100, "light.b": 200}, 205)
        assert result["light.a"] > 100
        assert result["light.b"] > 200
        assert result["light.b"] <= BRIGHTNESS_MAX

    def test_round_trip_is_reversible(self):
        # The point of anchoring on a base: dim then restore lands where it started.
        base = {"light.a": 120, "light.b": 40}
        down = base_relative_brightness_map(base, 40)
        assert down != base
        back = base_relative_brightness_map(base, 80)
        assert back == base


class TestGroupByBrightness:
    def test_identical_targets_share_one_call(self):
        result = group_by_brightness({"light.a": 100, "light.b": 100, "light.c": 200})
        assert result == {100: ["light.a", "light.b"], 200: ["light.c"]}

    def test_empty(self):
        assert group_by_brightness({}) == {}
