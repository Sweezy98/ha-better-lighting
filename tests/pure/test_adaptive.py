"""The adaptive curve. Pure: no hass fixture, no event loop.

Structure and the polar-region cases are adapted from Adaptive Lighting's
``tests/test_color_and_brightness.py``, which needs only pytest and astral.
The mired-interpolation and night-mode cases cover where we diverge from it.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest
from astral import LocationInfo
from astral.location import Location

from custom_components.better_lighting.adaptive import (
    AdaptiveConfig,
    SunEvent,
    SunEventOrderError,
    compute,
    compute_for_transition,
    from_mired,
    lerp_color_temp,
    to_mired,
)
from custom_components.better_lighting.const import BrightnessMode
from custom_components.better_lighting.util import clamp

LOCATIONS = [
    (52.379189, 4.899431, "Europe/Amsterdam"),
    (32.87336, -117.22743, "US/Pacific"),
    (-33.8688, 151.2093, "Australia/Sydney"),
    (60.0, 50.0, "UTC"),
]


@pytest.fixture(params=LOCATIONS, ids=[loc[2] for loc in LOCATIONS])
def place(request):
    lat, long, tz_name = request.param
    tz = zoneinfo.ZoneInfo(tz_name)
    location = Location(
        LocationInfo(
            name="test", region="test", timezone=tz_name, latitude=lat, longitude=long
        )
    )
    return location.observer, tz


@pytest.fixture
def config(place):
    observer, tz = place
    return AdaptiveConfig(observer=observer, timezone=tz)


def _day(tz, month=10, day=17, hour=12):
    return dt.datetime(2026, month, day, hour, tzinfo=tz)


class TestSunPosition:
    def test_stays_within_range_all_year(self, config, place):
        _, tz = place
        start = dt.datetime(2026, 1, 1, tzinfo=tz)
        for hours in range(0, 365 * 24, 7):
            position = config.sun.sun_position(start + dt.timedelta(hours=hours))
            assert -1.0 <= position <= 1.0

    def test_is_zero_at_the_horizon_crossings(self, config, place):
        _, tz = place
        day = _day(tz).date()
        assert config.sun.sun_position(config.sun.sunrise(day)) == pytest.approx(
            0, abs=1e-6
        )
        assert config.sun.sun_position(config.sun.sunset(day)) == pytest.approx(
            0, abs=1e-6
        )

    def test_peaks_at_noon_and_troughs_at_midnight(self, config, place):
        _, tz = place
        day = _day(tz)
        sunrise = config.sun.sunrise(day.date())
        sunset = config.sun.sunset(day.date())
        noon, midnight = config.sun.noon_and_midnight(day.date(), sunset, sunrise)
        assert config.sun.sun_position(noon) == pytest.approx(1.0, abs=1e-6)
        assert config.sun.sun_position(midnight) == pytest.approx(-1.0, abs=1e-6)

    def test_is_positive_by_day_and_negative_by_night(self, config, place):
        _, tz = place
        day = _day(tz).date()
        sunrise, sunset = config.sun.sunrise(day), config.sun.sunset(day)
        midday = sunrise + (sunset - sunrise) / 2
        assert config.sun.sun_position(midday) > 0
        assert config.sun.sun_position(sunrise - dt.timedelta(hours=1)) < 0
        assert config.sun.sun_position(sunset + dt.timedelta(hours=1)) < 0

    def test_moves_smoothly(self, config, place):
        """No discontinuity where the curve switches between event pairs."""
        _, tz = place
        start = _day(tz, hour=0)
        previous = config.sun.sun_position(start)
        for minutes in range(5, 60 * 48, 5):
            current = config.sun.sun_position(start + dt.timedelta(minutes=minutes))
            assert abs(current - previous) < 0.05
            previous = current


class TestBrightness:
    def test_sun_mode_is_flat_while_the_sun_is_up(self, place):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer, timezone=tz, brightness_mode=BrightnessMode.SUN
        )
        day = _day(tz).date()
        midday = (
            config.sun.sunrise(day)
            + (config.sun.sunset(day) - config.sun.sunrise(day)) / 2
        )
        assert (
            config.brightness_pct(midday, is_night=False) == config.max_brightness_pct
        )

    def test_sun_mode_ramps_below_the_horizon(self, place):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer,
            timezone=tz,
            brightness_mode=BrightnessMode.SUN,
            min_brightness_pct=10,
            max_brightness_pct=100,
        )
        day = _day(tz).date()
        just_after = config.brightness_pct(
            config.sun.sunset(day) + dt.timedelta(minutes=30), is_night=False
        )
        much_later = config.brightness_pct(
            config.sun.sunset(day) + dt.timedelta(hours=4), is_night=False
        )
        assert 10 <= much_later < just_after <= 100

    def test_tanh_mode_falls_through_sunset(self, place):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer, timezone=tz, brightness_mode=BrightnessMode.TANH
        )
        sunset = config.sun.sunset(_day(tz).date())
        before = config.brightness_pct(sunset - dt.timedelta(hours=1), is_night=False)
        after = config.brightness_pct(sunset + dt.timedelta(hours=1), is_night=False)
        # The point of tanh over the sun curve: it is already fading beforehand.
        assert before > after

    def test_tanh_mode_rises_through_sunrise(self, place):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer, timezone=tz, brightness_mode=BrightnessMode.TANH
        )
        sunrise = config.sun.sunrise(_day(tz).date())
        before = config.brightness_pct(sunrise - dt.timedelta(hours=1), is_night=False)
        after = config.brightness_pct(sunrise + dt.timedelta(hours=1), is_night=False)
        assert after > before

    @pytest.mark.parametrize("mode", list(BrightnessMode))
    def test_never_leaves_the_configured_range(self, place, mode):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer,
            timezone=tz,
            brightness_mode=mode,
            min_brightness_pct=15,
            max_brightness_pct=80,
        )
        start = _day(tz, hour=0)
        for minutes in range(0, 60 * 24, 13):
            value = config.brightness_pct(
                start + dt.timedelta(minutes=minutes), is_night=False
            )
            assert 15 <= value <= 80


class TestColorTemperature:
    def test_holds_at_the_warm_end_below_the_horizon(self, config, place):
        _, tz = place
        sunset = config.sun.sunset(_day(tz).date())
        position = config.sun.sun_position(sunset + dt.timedelta(hours=2))
        assert config.color_temp_kelvin(position, is_night=False) == (
            config.min_color_temp_k
        )

    def test_approaches_the_cold_end_at_noon(self, config):
        assert config.color_temp_kelvin(1.0, is_night=False) == pytest.approx(
            config.max_color_temp_k, abs=5
        )

    def test_rises_with_the_sun(self, config):
        values = [
            config.color_temp_kelvin(p, is_night=False)
            for p in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]
        assert values == sorted(values)

    def test_mired_interpolation_is_warmer_at_the_midpoint(self, place):
        """Our documented divergence from Adaptive Lighting."""
        observer, tz = place
        mired = AdaptiveConfig(observer=observer, timezone=tz, ct_space="mired")
        kelvin = AdaptiveConfig(observer=observer, timezone=tz, ct_space="kelvin")
        # Linear-in-Kelvin spends most of the day in the top of the range; the
        # perceptually even version sits noticeably warmer halfway up.
        assert mired.color_temp_kelvin(0.5, is_night=False) < kelvin.color_temp_kelvin(
            0.5, is_night=False
        )

    def test_endpoints_agree_in_both_spaces(self, place):
        observer, tz = place
        for space in ("mired", "kelvin"):
            config = AdaptiveConfig(observer=observer, timezone=tz, ct_space=space)
            assert config.color_temp_kelvin(0.0, is_night=False) == pytest.approx(
                config.min_color_temp_k, abs=5
            )
            assert config.color_temp_kelvin(1.0, is_night=False) == pytest.approx(
                config.max_color_temp_k, abs=5
            )

    def test_mired_round_trip(self):
        assert from_mired(to_mired(3000)) == pytest.approx(3000)

    def test_lerp_endpoints(self):
        assert lerp_color_temp(2000, 6000, 0.0) == pytest.approx(2000)
        assert lerp_color_temp(2000, 6000, 1.0) == pytest.approx(6000)


class TestNightMode:
    def test_overrides_both_axes(self, config, place):
        _, tz = place
        settings = compute(config, _day(tz), is_night=True)
        assert settings.brightness_pct == config.night_brightness_pct
        assert settings.color_temp_kelvin == config.night_color_temp_k
        assert settings.is_night is True

    def test_applies_at_any_hour(self, config, place):
        """Night follows an external entity, so it must not depend on the sun."""
        _, tz = place
        for hour in (0, 6, 12, 18):
            settings = compute(config, _day(tz, hour=hour), is_night=True)
            assert settings.brightness_pct == config.night_brightness_pct

    def test_daytime_is_unaffected_when_night_is_off(self, config, place):
        _, tz = place
        settings = compute(config, _day(tz), is_night=False)
        assert settings.brightness_pct > config.night_brightness_pct


class TestComputedColours:
    def test_derives_consistent_colour_representations(self, config, place):
        _, tz = place
        settings = compute(config, _day(tz))
        assert len(settings.rgb_color) == 3
        assert all(0 <= channel <= 255 for channel in settings.rgb_color)
        assert len(settings.xy_color) == 2
        assert 0 <= settings.hs_color[0] <= 360
        assert 0 <= settings.hs_color[1] <= 100

    def test_transition_aims_at_the_end_of_the_fade(self, config, place):
        _, tz = place
        now = _day(tz, hour=20)
        landed = compute_for_transition(config, 3600, now=now)
        assert landed.at == now + dt.timedelta(seconds=3600)

    def test_no_transition_aims_at_now(self, config, place):
        _, tz = place
        now = _day(tz, hour=20)
        assert compute_for_transition(config, None, now=now).at == now


class TestPolarRegions:
    """Above the circle astral has no sunrise to report; we synthesise one."""

    @pytest.fixture
    def arctic(self):
        location = Location(
            LocationInfo("Longyearbyen", "Svalbard", "UTC", 78.22, 15.63)
        )
        return AdaptiveConfig(
            observer=location.observer, timezone=zoneinfo.ZoneInfo("UTC")
        )

    @pytest.mark.parametrize(
        "date", [dt.date(2026, 1, 15), dt.date(2026, 6, 21), dt.date(2026, 12, 21)]
    )
    def test_polar_days_do_not_raise(self, arctic, date):
        moment = dt.datetime.combine(date, dt.time(12), tzinfo=zoneinfo.ZoneInfo("UTC"))
        settings = compute(arctic, moment)
        assert -1.0 <= settings.sun_position <= 1.0
        assert 0 <= settings.brightness_pct <= 100

    def test_sun_position_valid_all_year_in_the_arctic(self, arctic):
        start = dt.datetime(2026, 1, 1, tzinfo=zoneinfo.ZoneInfo("UTC"))
        for hours in range(0, 365 * 24, 11):
            position = arctic.sun.sun_position(start + dt.timedelta(hours=hours))
            assert -1.0 <= position <= 1.0


class TestEventOrdering:
    def test_absurd_offsets_are_rejected(self, place):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer,
            timezone=tz,
            # Pushing sunrise most of a day forward cannot produce a valid day.
            sunrise_offset=dt.timedelta(hours=20),
        )
        with pytest.raises(SunEventOrderError):
            config.sun.sun_events(_day(tz))

    def test_fixed_times_are_honoured(self, place):
        observer, tz = place
        config = AdaptiveConfig(
            observer=observer,
            timezone=tz,
            sunrise_time=dt.time(7, 0),
            sunset_time=dt.time(21, 0),
        )
        day = _day(tz).date()
        assert config.sun.sunrise(day).astimezone(tz).hour == 7
        assert config.sun.sunset(day).astimezone(tz).hour == 21

    def test_closest_event_finds_a_horizon_crossing(self, config, place):
        _, tz = place
        event, _ = config.sun.closest_event(_day(tz, hour=13))
        assert event in (SunEvent.SUNRISE, SunEvent.SUNSET)


class TestClamp:
    def test_inverted_bounds_are_sorted(self):
        """An inverted range is a supported way to express a reversed schedule."""
        assert clamp(50, 100, 10) == 50
        assert clamp(5, 100, 10) == 10
        assert clamp(150, 100, 10) == 100

    def test_normal_bounds(self):
        assert clamp(50, 10, 100) == 50
        assert clamp(5, 10, 100) == 10
        assert clamp(150, 10, 100) == 100
