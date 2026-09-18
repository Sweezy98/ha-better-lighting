"""Per-light calibration. Pure: no hass fixture, no event loop."""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.better_lighting.adaptive import AdaptiveSettings
from custom_components.better_lighting.profiles import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    Axis,
    LightCapabilities,
    LightProfile,
    Saturation,
    describe_saturation,
    resolve_target,
)


def settings(brightness_pct: float = 50.0, kelvin: int = 3000) -> AdaptiveSettings:
    return AdaptiveSettings(
        at=dt.datetime(2026, 6, 21, 12, tzinfo=dt.UTC),
        brightness_pct=brightness_pct,
        color_temp_kelvin=kelvin,
        sun_position=0.5,
        is_night=False,
        rgb_color=(255, 180, 120),
        xy_color=(0.4, 0.4),
        hs_color=(30.0, 50.0),
    )


def caps(
    entity_id: str = "light.test",
    modes: set[str] | None = None,
    min_k: int = 2000,
    max_k: int = 6500,
) -> LightCapabilities:
    return LightCapabilities.from_attributes(
        entity_id,
        {
            "supported_color_modes": list(modes or {"color_temp"}),
            "min_color_temp_kelvin": min_k,
            "max_color_temp_kelvin": max_k,
        },
    )


def pct_of(brightness: int) -> float:
    return brightness / BRIGHTNESS_MAX * 100


class TestOrderOfOperations:
    def test_offset_cannot_breach_the_configured_maximum(self):
        """The load-bearing ordering decision: calibrate, then clamp."""
        profile = LightProfile(brightness_offset_pct=30, max_brightness_pct=70)
        target = resolve_target(settings(brightness_pct=60), profile, caps())
        # 60 + 30 = 90, clamped to the 70 the user set. Clamping first would
        # have produced 60 -> 60 -> +30 -> 90 and blown past the limit.
        assert pct_of(target.brightness) == pytest.approx(70, abs=0.5)

    def test_offset_cannot_breach_the_configured_minimum(self):
        profile = LightProfile(brightness_offset_pct=-30, min_brightness_pct=25)
        target = resolve_target(settings(brightness_pct=40), profile, caps())
        assert pct_of(target.brightness) == pytest.approx(25, abs=0.5)

    def test_limits_are_absolute_not_relative(self):
        """Documented corollary: min/max are targets, not deltas."""
        profile = LightProfile(brightness_offset_pct=10, min_brightness_pct=30)
        target = resolve_target(settings(brightness_pct=15), profile, caps())
        # 15 + 10 = 25, raised to the 30 floor -- not 25, and not 40.
        assert pct_of(target.brightness) == pytest.approx(30, abs=0.5)

    def test_multiplier_applies_before_the_offset(self):
        profile = LightProfile(brightness_multiplier=2.0, brightness_offset_pct=10)
        target = resolve_target(settings(brightness_pct=20), profile, caps())
        # (20 * 2) + 10 = 50, not (20 + 10) * 2 = 60.
        assert pct_of(target.brightness) == pytest.approx(50, abs=0.5)

    def test_bias_stacks_on_top_of_the_calibration(self):
        """The room-wide relative dim is a further offset, not a replacement."""
        profile = LightProfile(brightness_offset_pct=10)
        plain = resolve_target(settings(brightness_pct=50), profile, caps())
        dimmed = resolve_target(
            settings(brightness_pct=50), profile, caps(), bias_pct=-20
        )
        assert pct_of(plain.brightness) == pytest.approx(60, abs=0.5)
        assert pct_of(dimmed.brightness) == pytest.approx(40, abs=0.5)


class TestBrightnessQuantisation:
    def test_full_scale(self):
        target = resolve_target(settings(brightness_pct=100), LightProfile(), caps())
        assert target.brightness == BRIGHTNESS_MAX

    def test_never_commands_zero(self):
        """0 reads as 'off' to many integrations, which would be a state change."""
        target = resolve_target(settings(brightness_pct=0), LightProfile(), caps())
        assert target.brightness == BRIGHTNESS_MIN

    def test_stays_in_range_across_absurd_calibration(self):
        profile = LightProfile(brightness_multiplier=3.0, brightness_offset_pct=50)
        for value in (0, 1, 25, 50, 99, 100):
            target = resolve_target(settings(brightness_pct=value), profile, caps())
            assert BRIGHTNESS_MIN <= target.brightness <= BRIGHTNESS_MAX

    def test_dropped_for_a_light_with_no_brightness(self):
        target = resolve_target(settings(), LightProfile(), caps(modes={"onoff"}))
        assert target.brightness is None
        assert Axis.BRIGHTNESS not in target.axes


class TestColorTemperature:
    def test_offset_is_applied(self):
        profile = LightProfile(color_temp_offset_k=500)
        target = resolve_target(settings(kelvin=3000), profile, caps())
        assert target.color["color_temp_kelvin"] == 3500

    def test_clamped_to_the_configured_range(self):
        profile = LightProfile(color_temp_offset_k=2000, max_color_temp_k=4000)
        target = resolve_target(settings(kelvin=3000), profile, caps())
        assert target.color["color_temp_kelvin"] == 4000

    def test_clamped_to_what_the_device_supports(self):
        profile = LightProfile(color_temp_offset_k=5000)
        target = resolve_target(settings(kelvin=3000), profile, caps(max_k=4500))
        assert target.color["color_temp_kelvin"] == 4500

    def test_device_clamp_can_be_disabled(self):
        profile = LightProfile(color_temp_offset_k=5000, clamp_to_device_limits=False)
        target = resolve_target(settings(kelvin=3000), profile, caps(max_k=4500))
        assert target.color["color_temp_kelvin"] == 8000

    def test_quantised_to_five_kelvin(self):
        profile = LightProfile(color_temp_offset_k=3)
        target = resolve_target(settings(kelvin=3001), profile, caps())
        assert target.color["color_temp_kelvin"] % 5 == 0

    def test_dropped_for_a_light_with_no_colour(self):
        target = resolve_target(settings(), LightProfile(), caps(modes={"brightness"}))
        assert target.color is None
        assert Axis.COLOR not in target.axes


class TestColourOnlyLights:
    """Where we diverge from Adaptive Lighting, and why it matters."""

    def test_colour_only_light_honours_the_colour_temp_offset(self):
        neutral = resolve_target(
            settings(kelvin=3000), LightProfile(), caps(modes={"rgb"})
        )
        warmed = resolve_target(
            settings(kelvin=3000),
            LightProfile(color_temp_offset_k=-1200),
            caps(modes={"rgb"}),
        )
        assert neutral.color is not None and warmed.color is not None
        # Adaptive Lighting reuses a precomputed RGB here, so the offset would
        # be silently ignored on exactly the bulbs most likely to need matching.
        assert warmed.color["rgb_color"] != neutral.color["rgb_color"]
        # Warmer means relatively less blue.
        assert warmed.color["rgb_color"][2] < neutral.color["rgb_color"][2]

    def test_prefer_rgb_uses_colour_on_a_dual_mode_light(self):
        profile = LightProfile(prefer_rgb=True)
        target = resolve_target(settings(), profile, caps(modes={"color_temp", "rgb"}))
        assert "rgb_color" in target.color
        assert "color_temp_kelvin" not in target.color

    def test_colour_temp_wins_by_default_on_a_dual_mode_light(self):
        target = resolve_target(
            settings(), LightProfile(), caps(modes={"color_temp", "rgb"})
        )
        assert "color_temp_kelvin" in target.color

    def test_rgbw_light_gets_an_rgbw_payload(self):
        target = resolve_target(settings(), LightProfile(), caps(modes={"rgbw"}))
        assert len(target.color["rgbw_color"]) == 4


class TestAxisMasking:
    def test_want_restricts_to_brightness(self):
        target = resolve_target(
            settings(), LightProfile(), caps(), want=Axis.BRIGHTNESS
        )
        assert target.brightness is not None
        assert target.color is None
        assert target.axes is Axis.BRIGHTNESS

    def test_want_restricts_to_colour(self):
        target = resolve_target(settings(), LightProfile(), caps(), want=Axis.COLOR)
        assert target.brightness is None
        assert target.color is not None
        assert target.axes is Axis.COLOR

    def test_want_nothing_produces_nothing(self):
        target = resolve_target(settings(), LightProfile(), caps(), want=Axis.NONE)
        assert target.is_empty

    def test_per_axis_opt_out(self):
        target = resolve_target(settings(), LightProfile(adapt_color=False), caps())
        assert target.brightness is not None
        assert target.color is None

    def test_disabled_profile_produces_nothing(self):
        target = resolve_target(settings(), LightProfile(enabled=False), caps())
        assert target.is_empty


class TestSaturationReporting:
    def test_flags_a_clipped_brightness(self):
        profile = LightProfile(brightness_offset_pct=50, max_brightness_pct=60)
        target = resolve_target(settings(brightness_pct=50), profile, caps())
        assert Saturation.BRIGHTNESS_HIGH in target.saturation
        assert describe_saturation(target.saturation) == "brightness_high"

    def test_flags_a_clipped_colour_temperature(self):
        profile = LightProfile(color_temp_offset_k=-4000)
        target = resolve_target(settings(kelvin=3000), profile, caps())
        assert Saturation.CT_LOW in target.saturation

    def test_clean_values_report_nothing(self):
        target = resolve_target(settings(), LightProfile(), caps())
        assert target.saturation is Saturation.NONE
        assert describe_saturation(target.saturation) is None


class TestCapabilityParsing:
    def test_missing_kelvin_range_falls_back_to_defaults(self):
        """A known crash in Adaptive Lighting: some integrations omit these."""
        parsed = LightCapabilities.from_attributes(
            "light.x", {"supported_color_modes": ["color_temp"]}
        )
        assert parsed.min_color_temp_kelvin == 2000
        assert parsed.max_color_temp_kelvin == 6535

    def test_onoff_light_has_no_axes(self):
        parsed = LightCapabilities.from_attributes(
            "light.x", {"supported_color_modes": ["onoff"]}
        )
        assert not parsed.supports_brightness
        assert not parsed.supports_color
        assert not parsed.supports_color_temp

    def test_no_attributes_at_all(self):
        parsed = LightCapabilities.from_attributes("light.x", {})
        assert not parsed.supports_brightness


class TestServiceData:
    def test_builds_a_turn_on_payload(self):
        target = resolve_target(settings(brightness_pct=50), LightProfile(), caps())
        data = target.as_service_data()
        assert data["brightness"] == target.brightness
        assert data["color_temp_kelvin"] == target.color["color_temp_kelvin"]
        assert "entity_id" not in data
