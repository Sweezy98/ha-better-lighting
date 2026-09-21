"""Triggers: which sensors are asking for the lights."""

from __future__ import annotations

from custom_components.better_lighting.triggers import (
    SensorTrigger,
    active_entities,
    any_active,
    is_active,
)

MOTION = SensorTrigger("binary_sensor.motion")
DOOR = SensorTrigger("cover.garage")
TRACKER = SensorTrigger("device_tracker.phone")


class TestWhatCountsAsActive:
    def test_a_binary_sensor_that_is_on(self) -> None:
        assert is_active(MOTION, {"binary_sensor.motion": "on"})
        assert not is_active(MOTION, {"binary_sensor.motion": "off"})

    def test_a_cover_that_is_open(self) -> None:
        """A garage door is a trigger without anybody configuring what open means."""
        assert is_active(DOOR, {"cover.garage": "open"})
        assert not is_active(DOOR, {"cover.garage": "closed"})

    def test_somebody_who_is_home(self) -> None:
        assert is_active(TRACKER, {"device_tracker.phone": "home"})
        assert not is_active(TRACKER, {"device_tracker.phone": "not_home"})

    def test_case_does_not_matter(self) -> None:
        assert is_active(DOOR, {"cover.garage": "Open"})

    def test_a_state_named_outright(self) -> None:
        odd = SensorTrigger("sensor.radar", active_state="detected")

        assert is_active(odd, {"sensor.radar": "detected"})
        assert not is_active(odd, {"sensor.radar": "on"})

    def test_a_named_state_is_exact(self) -> None:
        """Naming one narrows it; the usual four no longer apply."""
        odd = SensorTrigger("binary_sensor.thing", active_state="off")

        assert is_active(odd, {"binary_sensor.thing": "off"})
        assert not is_active(odd, {"binary_sensor.thing": "on"})


class TestWhatCannotBeRead:
    def test_an_unavailable_sensor_is_not_asking(self) -> None:
        assert not is_active(MOTION, {"binary_sensor.motion": "unavailable"})

    def test_an_unknown_sensor_is_not_asking(self) -> None:
        assert not is_active(MOTION, {"binary_sensor.motion": "unknown"})

    def test_a_missing_entity_is_not_asking(self) -> None:
        assert not is_active(MOTION, {})


class TestCombining:
    def test_any_one_is_enough(self) -> None:
        """A porch with a motion sensor and a door: either is reason to light it."""
        states = {"binary_sensor.motion": "off", "cover.garage": "open"}

        assert any_active([MOTION, DOOR], states)

    def test_all_clear_is_not_asking(self) -> None:
        states = {"binary_sensor.motion": "off", "cover.garage": "closed"}

        assert not any_active([MOTION, DOOR], states)

    def test_no_triggers_is_not_asking(self) -> None:
        assert not any_active([], {"binary_sensor.motion": "on"})

    def test_one_sensor_down_does_not_silence_the_other(self) -> None:
        states = {"binary_sensor.motion": "unavailable", "cover.garage": "open"}

        assert any_active([MOTION, DOOR], states)


class TestWhatToSubscribeTo:
    def test_the_entities_in_order(self) -> None:
        assert active_entities([MOTION, DOOR]) == [
            "binary_sensor.motion",
            "cover.garage",
        ]

    def test_the_same_sensor_twice_is_watched_once(self) -> None:
        both = [MOTION, SensorTrigger("binary_sensor.motion", active_state="off")]

        assert active_entities(both) == ["binary_sensor.motion"]

    def test_a_trigger_with_no_entity_is_dropped(self) -> None:
        """A half-filled row in the editor should not subscribe to nothing."""
        assert active_entities([SensorTrigger(""), MOTION]) == ["binary_sensor.motion"]
