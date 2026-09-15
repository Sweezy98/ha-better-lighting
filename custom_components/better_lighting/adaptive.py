"""The adaptive curve: what brightness and colour temperature the sun implies.

Ported from Adaptive Lighting's ``color_and_brightness.py``. Like the original,
this module deliberately imports nothing from ``homeassistant`` except the pure
colour-conversion helpers, so the whole curve is unit-testable with a fake clock
and no event loop.

Three deliberate differences from the original:

* **Colour temperature is interpolated in mired, not Kelvin.** Equal steps in
  mired are roughly equal perceived steps. Interpolating linearly in Kelvin
  spends most of the afternoon in the top third of the range and then drops
  2000 K in the final hour. Set ``ct_space="kelvin"`` for the old behaviour.
* **Night is a boolean, not a time window.** Better Lighting takes night mode
  from an external entity, so this module is told whether it is night rather
  than working it out. The visual smoothness comes from the transition.
* **Only two brightness curves.** ``linear`` was dropped: it takes the same two
  knobs as ``tanh`` but has a kinked derivative at both ends, producing a
  visible "the fade just stopped" moment.
"""

from __future__ import annotations

import bisect
import datetime
import logging
import math
from dataclasses import dataclass
from datetime import UTC, timedelta
from enum import StrEnum
from functools import cached_property, partial
from typing import Literal

import astral
import astral.sun
from homeassistant.util.color import (
    color_RGB_to_xy,
    color_temperature_to_rgb,
    color_xy_to_hs,
)

from .const import BrightnessMode
from .util import clamp

_LOGGER = logging.getLogger(__name__)

utcnow: partial[datetime.datetime] = partial(datetime.datetime.now, UTC)

# Above the polar circle the sun may not cross the horizon at all. On such days
# a synthetic one-hour "day" or "night" is placed this far from solar noon or
# midnight so the cycle keeps running instead of raising.
_POLAR_OFFSET = timedelta(minutes=30)
_POLAR_EPSILON = timedelta(seconds=1)


class SunEvent(StrEnum):
    """The four points that anchor the daily curve."""

    SUNRISE = "sunrise"
    NOON = "solar_noon"
    SUNSET = "sunset"
    MIDNIGHT = "solar_midnight"


_ORDER = (SunEvent.SUNRISE, SunEvent.NOON, SunEvent.SUNSET, SunEvent.MIDNIGHT)
_ALLOWED_ORDERS = {_ORDER[i:] + _ORDER[:i] for i in range(len(_ORDER))}


class SunEventOrderError(ValueError):
    """The configured offsets or fixed times produced an impossible day.

    Raised rather than silently producing nonsense, but the caller is expected
    to log this once per configuration change and freeze the zone at its last
    good settings -- not to log it on every interval tick.
    """


def to_mired(kelvin: float) -> float:
    """Kelvin to mired (reciprocal megakelvin)."""
    return 1_000_000.0 / kelvin


def from_mired(mired: float) -> float:
    """Mired back to Kelvin."""
    return 1_000_000.0 / mired


def lerp_color_temp(
    k1: float, k2: float, t: float, space: Literal["mired", "kelvin"] = "mired"
) -> float:
    """Interpolate between two colour temperatures at fraction ``t``."""
    if space == "kelvin":
        return k1 + (k2 - k1) * t
    m1, m2 = to_mired(k1), to_mired(k2)
    return from_mired(m1 + (m2 - m1) * t)


def find_a_b(x1: float, x2: float, y1: float, y2: float) -> tuple[float, float]:
    """Fit ``y = 0.5 * (tanh(a * (x - b)) + 1)`` through two points."""
    a = (math.atanh(2 * y2 - 1) - math.atanh(2 * y1 - 1)) / (x2 - x1)
    b = x1 - (math.atanh(2 * y1 - 1) / a)
    return a, b


def scaled_tanh(
    x: float,
    x1: float,
    x2: float,
    y1: float = 0.05,
    y2: float = 0.95,
    y_min: float = 0.0,
    y_max: float = 100.0,
) -> float:
    """A tanh S-curve scaled so it reaches ``y1`` at ``x1`` and ``y2`` at ``x2``."""
    a, b = find_a_b(x1, x2, y1, y2)
    return y_min + (y_max - y_min) * 0.5 * (math.tanh(a * (x - b)) + 1)


@dataclass(frozen=True)
class SunEvents:
    """Resolves the day's four sun events, with offsets and clamps applied."""

    observer: astral.Observer
    timezone: datetime.tzinfo = UTC
    sunrise_time: datetime.time | None = None
    sunset_time: datetime.time | None = None
    min_sunrise_time: datetime.time | None = None
    max_sunrise_time: datetime.time | None = None
    min_sunset_time: datetime.time | None = None
    max_sunset_time: datetime.time | None = None
    sunrise_offset: timedelta = timedelta()
    sunset_offset: timedelta = timedelta()

    def _combine(self, day: datetime.date, time: datetime.time) -> datetime.datetime:
        naive = datetime.datetime.combine(day, time)
        return naive.replace(tzinfo=self.timezone).astimezone(UTC)

    def _astral_event(
        self,
        day: datetime.date,
        event: Literal[SunEvent.SUNRISE, SunEvent.SUNSET],
        offset: timedelta,
    ) -> datetime.datetime:
        """Astral's sunrise/sunset, with a synthetic fallback inside the circle."""
        func = astral.sun.sunrise if event is SunEvent.SUNRISE else astral.sun.sunset
        try:
            return func(self.observer, day) + offset
        except ValueError:
            # Polar day or night: astral has no crossing to report.
            noon = astral.sun.noon(self.observer, day)
            midnight = astral.sun.midnight(self.observer, day)
            next_midnight = astral.sun.midnight(self.observer, day + timedelta(days=1))
            # The sum of the day's highest and lowest elevation is about twice
            # the solar declination, so its sign separates midnight sun from
            # polar night even on the boundary days.
            if (
                astral.sun.elevation(self.observer, noon)
                + astral.sun.elevation(self.observer, midnight)
                > 0
            ):
                synthetic = (
                    midnight + _POLAR_OFFSET
                    if event is SunEvent.SUNRISE
                    else next_midnight - _POLAR_OFFSET
                )
            else:
                sign = -1 if event is SunEvent.SUNRISE else 1
                synthetic = noon + sign * _POLAR_OFFSET

            lower, upper = (
                (midnight, noon)
                if event is SunEvent.SUNRISE
                else (noon, next_midnight)
            )
            return min(
                max(synthetic + offset, lower + _POLAR_EPSILON),
                upper - _POLAR_EPSILON,
            )

    def sunrise(self, day: datetime.date) -> datetime.datetime:
        value = (
            self._astral_event(day, SunEvent.SUNRISE, self.sunrise_offset)
            if self.sunrise_time is None
            else self._combine(day, self.sunrise_time) + self.sunrise_offset
        )
        if self.min_sunrise_time is not None:
            value = max(self._combine(day, self.min_sunrise_time), value)
        if self.max_sunrise_time is not None:
            value = min(self._combine(day, self.max_sunrise_time), value)
        return value

    def sunset(self, day: datetime.date) -> datetime.datetime:
        value = (
            self._astral_event(day, SunEvent.SUNSET, self.sunset_offset)
            if self.sunset_time is None
            else self._combine(day, self.sunset_time) + self.sunset_offset
        )
        if self.min_sunset_time is not None:
            value = max(self._combine(day, self.min_sunset_time), value)
        if self.max_sunset_time is not None:
            value = min(self._combine(day, self.max_sunset_time), value)
        return value

    def noon_and_midnight(
        self,
        day: datetime.date,
        sunset: datetime.datetime,
        sunrise: datetime.datetime,
    ) -> tuple[datetime.datetime, datetime.datetime]:
        """Solar noon and midnight, honouring any manual sunrise/sunset shift."""
        if not any(
            (
                self.sunrise_time,
                self.sunset_time,
                self.min_sunrise_time,
                self.max_sunrise_time,
                self.min_sunset_time,
                self.max_sunset_time,
            )
        ):
            return (
                astral.sun.noon(self.observer, day),
                astral.sun.midnight(self.observer, day),
            )

        # With the day artificially shifted, place noon midway through it and
        # midnight twelve hours away, so the parabola stays symmetric.
        middle = abs(sunset - sunrise) / 2
        if sunset > sunrise:
            noon = sunrise + middle
            midnight = noon + timedelta(hours=12) * (1 if noon.hour < 12 else -1)
        else:
            midnight = sunset + middle
            noon = midnight + timedelta(hours=12) * (1 if midnight.hour < 12 else -1)
        return noon, midnight

    def sun_events(self, dt: datetime.datetime) -> list[tuple[SunEvent, float]]:
        """The day's four events as (name, unix timestamp)."""
        sunrise = self.sunrise(dt)
        sunset = self.sunset(dt)
        noon, midnight = self.noon_and_midnight(dt, sunset, sunrise)
        events = [
            (SunEvent.SUNRISE, sunrise.timestamp()),
            (SunEvent.SUNSET, sunset.timestamp()),
            (SunEvent.NOON, noon.timestamp()),
            (SunEvent.MIDNIGHT, midnight.timestamp()),
        ]
        self._validate_order(events)
        return events

    def _validate_order(self, events: list[tuple[SunEvent, float]]) -> None:
        names, _ = zip(*sorted(events, key=lambda e: e[1]), strict=True)
        if names not in _ALLOWED_ORDERS:
            raise SunEventOrderError(
                f"Sun events {names} are out of order. This usually means a "
                f"sunrise/sunset offset is too large, or a manually set time "
                f"falls on the wrong side of noon or midnight."
            )

    def prev_and_next_events(
        self, dt: datetime.datetime
    ) -> list[tuple[SunEvent, float]]:
        """The event just before and just after ``dt``."""
        events = sorted(
            (
                event
                for offset in (-1, 0, 1)
                for event in self.sun_events(dt + timedelta(days=offset))
            ),
            key=lambda e: e[1],
        )
        index = bisect.bisect([ts for _, ts in events], dt.timestamp())
        return events[index - 1 : index + 1]

    def sun_position(self, dt: datetime.datetime) -> float:
        """Where the sun is, from -1 at solar midnight to +1 at solar noon.

        Zero at sunrise and sunset. This is not the true elevation: it is a
        parabola stitched between the four events, which keeps the curve smooth
        and symmetric regardless of latitude or season.
        """
        target = dt.timestamp()
        (_, prev_ts), (next_event, next_ts) = self.prev_and_next_events(dt)
        # Anchor the parabola's vertex on noon/midnight and its zero on the
        # horizon crossing, whichever of the two is which.
        vertex, zero = (
            (prev_ts, next_ts)
            if next_event in (SunEvent.SUNSET, SunEvent.SUNRISE)
            else (next_ts, prev_ts)
        )
        sign = 1 if next_event in (SunEvent.SUNSET, SunEvent.NOON) else -1
        return sign * (1 - ((target - vertex) / (vertex - zero)) ** 2)

    def closest_event(self, dt: datetime.datetime) -> tuple[SunEvent, float]:
        """The nearest horizon crossing, which the tanh curve is anchored on."""
        (prev_event, prev_ts), (next_event, next_ts) = self.prev_and_next_events(dt)
        for wanted in (SunEvent.SUNRISE, SunEvent.SUNSET):
            if prev_event is wanted:
                return wanted, prev_ts
            if next_event is wanted:
                return wanted, next_ts
        raise SunEventOrderError("No sunrise or sunset adjacent to this moment.")


@dataclass(frozen=True)
class AdaptiveConfig:
    """The resolved curve for one zone: hub defaults with zone overrides applied."""

    observer: astral.Observer
    timezone: datetime.tzinfo = UTC
    brightness_mode: BrightnessMode = BrightnessMode.TANH
    min_brightness_pct: float = 1.0
    max_brightness_pct: float = 100.0
    min_color_temp_k: int = 2000
    max_color_temp_k: int = 5500
    night_brightness_pct: float = 1.0
    night_color_temp_k: int = 1800
    time_dark: timedelta = timedelta(seconds=5400)
    time_light: timedelta = timedelta(seconds=2700)
    ct_space: Literal["mired", "kelvin"] = "mired"
    sunrise_time: datetime.time | None = None
    sunset_time: datetime.time | None = None
    min_sunrise_time: datetime.time | None = None
    max_sunrise_time: datetime.time | None = None
    min_sunset_time: datetime.time | None = None
    max_sunset_time: datetime.time | None = None
    sunrise_offset: timedelta = timedelta()
    sunset_offset: timedelta = timedelta()

    @cached_property
    def sun(self) -> SunEvents:
        return SunEvents(
            observer=self.observer,
            timezone=self.timezone,
            sunrise_time=self.sunrise_time,
            sunset_time=self.sunset_time,
            min_sunrise_time=self.min_sunrise_time,
            max_sunrise_time=self.max_sunrise_time,
            min_sunset_time=self.min_sunset_time,
            max_sunset_time=self.max_sunset_time,
            sunrise_offset=self.sunrise_offset,
            sunset_offset=self.sunset_offset,
        )

    def brightness_pct(self, dt: datetime.datetime, *, is_night: bool) -> float:
        if is_night:
            return self.night_brightness_pct

        if self.brightness_mode is BrightnessMode.SUN:
            position = self.sun.sun_position(dt)
            if position > 0:
                return self.max_brightness_pct
            span = self.max_brightness_pct - self.min_brightness_pct
            value = span * (1 + position) + self.min_brightness_pct
        else:
            event, ts = self.sun.closest_event(dt)
            dark = self.time_dark.total_seconds()
            light = self.time_light.total_seconds()
            if event is SunEvent.SUNRISE:
                x1, x2, y1, y2 = -dark, +light, 0.05, 0.95
            else:
                x1, x2, y1, y2 = -light, +dark, 0.95, 0.05
            value = scaled_tanh(
                dt.timestamp() - ts,
                x1=x1,
                x2=x2,
                y1=y1,
                y2=y2,
                y_min=self.min_brightness_pct,
                y_max=self.max_brightness_pct,
            )

        return clamp(value, self.min_brightness_pct, self.max_brightness_pct)

    def color_temp_kelvin(self, position: float, *, is_night: bool) -> int:
        if is_night:
            return self.night_color_temp_k
        # Below the horizon the colour holds at the warm end; the night entity,
        # not the sun, is what takes it warmer still.
        fraction = max(position, 0.0)
        value = lerp_color_temp(
            self.min_color_temp_k, self.max_color_temp_k, fraction, self.ct_space
        )
        return 5 * round(value / 5)


@dataclass(frozen=True, slots=True)
class AdaptiveSettings:
    """What the curve says the lights should be doing at one instant."""

    at: datetime.datetime
    brightness_pct: float
    color_temp_kelvin: int
    sun_position: float
    is_night: bool
    rgb_color: tuple[int, int, int]
    xy_color: tuple[float, float]
    hs_color: tuple[float, float]


def compute(
    config: AdaptiveConfig, dt: datetime.datetime, *, is_night: bool = False
) -> AdaptiveSettings:
    """Evaluate the curve at ``dt``."""
    position = config.sun.sun_position(dt)
    brightness = config.brightness_pct(dt, is_night=is_night)
    kelvin = config.color_temp_kelvin(position, is_night=is_night)

    red, green, blue = color_temperature_to_rgb(kelvin)
    rgb = (round(red), round(green), round(blue))
    xy = color_RGB_to_xy(*rgb)

    return AdaptiveSettings(
        at=dt,
        brightness_pct=brightness,
        color_temp_kelvin=kelvin,
        sun_position=position,
        is_night=is_night,
        rgb_color=rgb,
        xy_color=xy,
        hs_color=color_xy_to_hs(*xy),
    )


def compute_for_transition(
    config: AdaptiveConfig,
    transition: float | None,
    *,
    is_night: bool = False,
    now: datetime.datetime | None = None,
) -> AdaptiveSettings:
    """Evaluate the curve at the moment the fade will *finish*.

    Aiming at ``now`` would leave every light one transition behind the curve
    for the whole fade; aiming at the end means it lands on the right value.
    """
    moment = (now or utcnow()) + timedelta(seconds=transition or 0)
    return compute(config, moment, is_night=is_night)
