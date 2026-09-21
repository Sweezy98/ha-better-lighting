"""Motion and door triggers, end to end.

Two scenarios, one mechanism. A motion sensor on a drive lights it while there
is motion and for a while after; a garage door lights the garage while it is
open and for a while after it shuts. Re-triggering during the wait starts the
wait again, because the timer is cancelled the moment the sensor comes back.

And in both, somebody reaching for the switch stops the clock: the lights stay
on until they are turned off by hand, and then the automation has them back.
"""

from __future__ import annotations

import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)

MOTION = "binary_sensor.drive_motion"
DOOR = "binary_sensor.garage_door"
LUX = "sensor.drive_lux"
HOLIDAY = "input_boolean.holiday"

DARK_ENOUGH = {
    "condition_id": "dark",
    "name": "Dark enough",
    "kind": "below",
    "condition_entity": LUX,
    "threshold": 10,
}
NOT_ON_HOLIDAY = {
    "condition_id": "home",
    "name": "Not on holiday",
    "kind": "state_is",
    "condition_entity": HOLIDAY,
    "required_state": "off",
}


async def _build(
    hass: HomeAssistant,
    *,
    sensor: str = MOTION,
    hold: int = 60,
    conditions: list[str] | None = None,
    hub_conditions: list[dict] | None = None,
    hold_by_hand: bool = True,
):
    """An outdoor room whose one zone is driven by a sensor."""
    await setup_members(hass, [MemberLight("Drive", is_on=False)])
    hass.states.async_set(sensor, "off")

    entry = hub_entry(
        options={"conditions": hub_conditions or []},
        subentries_data=[
            room_subentry(
                "Outside",
                ["light.drive"],
                zones=[
                    {
                        "zone_id": "drive",
                        "name": "Drive",
                        "lights": ["light.drive"],
                        "presence_entity": sensor,
                        "presence_clear_delay": hold,
                        "light_on_trigger": True,
                        "presence_conditions": conditions or [],
                        "hold_when_set_by_hand": hold_by_hand,
                    }
                ],
                switches=[
                    {
                        "switch_id": "s1",
                        "name": "Drive switch",
                        "binding_type": "service_only",
                        "drives_zone": "drive",
                        "scene_order": ["__adaptive__"],
                        # One position, so the second press is the off at the
                        # end of the list -- which is how somebody actually
                        # turns these lights off again.
                        "off_at_end": True,
                    }
                ],
            )
        ],
    )
    await setup_hub(hass, entry)
    return entry


def _lit(hass: HomeAssistant) -> bool:
    state = hass.states.get("light.drive")
    return state is not None and state.state == "on"


async def _set(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


async def _wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int):
    freezer.tick(datetime.timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


class TestAMotionSensor:
    async def test_motion_lights_it(self, hass: HomeAssistant) -> None:
        await _build(hass)

        await _set(hass, MOTION, "on")

        assert _lit(hass)

    async def test_it_stays_lit_while_there_is_motion(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, hold=60)
        await _set(hass, MOTION, "on")

        await _wait(hass, freezer, 600)

        assert _lit(hass)

    async def test_it_goes_out_after_the_hold(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, hold=60)
        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")
        assert _lit(hass)

        await _wait(hass, freezer, 61)

        assert not _lit(hass)

    async def test_it_is_still_lit_part_way_through_the_hold(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, hold=60)
        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")

        await _wait(hass, freezer, 30)

        assert _lit(hass)

    async def test_fresh_motion_starts_the_wait_again(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The whole point of a hold: walking back past resets the clock."""
        await _build(hass, hold=60)
        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")
        await _wait(hass, freezer, 50)

        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")
        await _wait(hass, freezer, 50)

        assert _lit(hass)

        await _wait(hass, freezer, 20)
        assert not _lit(hass)


class TestAGarageDoor:
    async def test_it_is_lit_while_the_door_is_open(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, sensor=DOOR, hold=300)

        await _set(hass, DOOR, "on")
        await _wait(hass, freezer, 3600)

        assert _lit(hass)

    async def test_shutting_it_starts_the_timer(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, sensor=DOOR, hold=300)
        await _set(hass, DOOR, "on")

        await _set(hass, DOOR, "off")
        await _wait(hass, freezer, 301)

        assert not _lit(hass)

    async def test_opening_it_again_starts_the_wait_again(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, sensor=DOOR, hold=300)
        await _set(hass, DOOR, "on")
        await _set(hass, DOOR, "off")
        await _wait(hass, freezer, 200)

        await _set(hass, DOOR, "on")
        await _set(hass, DOOR, "off")
        await _wait(hass, freezer, 200)

        assert _lit(hass)


class TestTheSwitchWins:
    async def _press(self, hass: HomeAssistant) -> None:
        await hass.services.async_call(
            "better_lighting",
            "press",
            {"room": "outside", "controller": "Drive switch"},
            blocking=True,
        )
        await hass.async_block_till_done()

    async def test_lights_set_by_hand_are_not_switched_off(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """On by hand before the sensor ever fires: the timer never runs."""
        await _build(hass, hold=60)
        await self._press(hass)
        assert _lit(hass)

        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")
        await _wait(hass, freezer, 120)

        assert _lit(hass)

    async def test_pressing_during_the_hold_keeps_them_on(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A press that lands on a lit position stops the clock.

        This switch turns off at the end of its one-entry list, so the first
        press from a lit room is a genuine off and the second lights it again
        -- by hand this time, which is what holds it.
        """
        await _build(hass, hold=60)
        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")

        await self._press(hass)
        await self._press(hass)
        assert _lit(hass)

        await _wait(hass, freezer, 120)

        assert _lit(hass)

    async def test_a_press_that_lands_on_off_really_does_turn_them_off(
        self, hass: HomeAssistant
    ) -> None:
        """Reaching for the switch to kill a motion light has to work."""
        await _build(hass, hold=60)
        await _set(hass, MOTION, "on")
        assert _lit(hass)

        await self._press(hass)

        assert not _lit(hass)

    async def test_turning_them_off_by_hand_hands_them_back(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The automation is restored, not disabled."""
        await _build(hass, hold=60)
        await self._press(hass)
        await self._press(hass)  # Round the one-entry list and off again.
        assert not _lit(hass)

        await _set(hass, MOTION, "on")
        assert _lit(hass)

        await _set(hass, MOTION, "off")
        await _wait(hass, freezer, 61)
        assert not _lit(hass)

    async def test_the_hold_can_be_turned_off(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await _build(hass, hold=60, hold_by_hand=False)
        await self._press(hass)

        await _set(hass, MOTION, "on")
        await _set(hass, MOTION, "off")
        await _wait(hass, freezer, 61)

        assert not _lit(hass)


class TestConditions:
    async def test_a_dark_enough_rule_lets_it_through(
        self, hass: HomeAssistant
    ) -> None:
        await _build(hass, conditions=["dark"], hub_conditions=[DARK_ENOUGH])
        await _set(hass, LUX, "4")

        await _set(hass, MOTION, "on")

        assert _lit(hass)

    async def test_a_bright_drive_is_left_alone(self, hass: HomeAssistant) -> None:
        await _build(hass, conditions=["dark"], hub_conditions=[DARK_ENOUGH])
        await _set(hass, LUX, "400")

        await _set(hass, MOTION, "on")

        assert not _lit(hass)

    async def test_every_rule_has_to_hold(self, hass: HomeAssistant) -> None:
        """Implicitly ANDed: one more rule can only make it fire less often."""
        await _build(
            hass,
            conditions=["dark", "home"],
            hub_conditions=[DARK_ENOUGH, NOT_ON_HOLIDAY],
        )
        await _set(hass, LUX, "4")
        await _set(hass, HOLIDAY, "on")

        await _set(hass, MOTION, "on")

        assert not _lit(hass)

    async def test_both_holding_lets_it_through(self, hass: HomeAssistant) -> None:
        await _build(
            hass,
            conditions=["dark", "home"],
            hub_conditions=[DARK_ENOUGH, NOT_ON_HOLIDAY],
        )
        await _set(hass, LUX, "4")
        await _set(hass, HOLIDAY, "off")

        await _set(hass, MOTION, "on")

        assert _lit(hass)

    async def test_an_unreadable_sensor_blocks_by_default(
        self, hass: HomeAssistant
    ) -> None:
        """Allowed only if every rule passes, and this one has not passed."""
        await _build(hass, conditions=["dark"], hub_conditions=[DARK_ENOUGH])
        await _set(hass, LUX, "unavailable")

        await _set(hass, MOTION, "on")

        assert not _lit(hass)

    async def test_a_condition_can_be_told_to_let_it_through(
        self, hass: HomeAssistant
    ) -> None:
        await _build(
            hass,
            conditions=["dark"],
            hub_conditions=[{**DARK_ENOUGH, "unknown_blocks": False}],
        )
        await _set(hass, LUX, "unavailable")

        await _set(hass, MOTION, "on")

        assert _lit(hass)

    async def test_a_rule_named_but_not_defined_is_ignored(
        self, hass: HomeAssistant
    ) -> None:
        """A deleted rule shortens the list rather than jamming the automation.

        The same leniency a controller has about a deleted scene: dangling
        references are a repair issue, not a reason to stop working.
        """
        await _build(hass, conditions=["gone"], hub_conditions=[])

        await _set(hass, MOTION, "on")

        assert _lit(hass)


class TestAWholeRoomOnASensor:
    """The same shape one level up, where presence has always lived.

    A room has had a sensor, a hold and an on/off action since well before
    this; what it has gained is the gate and the hold-by-hand.
    """

    async def _build(self, hass: HomeAssistant, **presence):
        await setup_members(hass, [MemberLight("Garage Main", is_on=False)])
        hass.states.async_set(DOOR, "off")
        entry = hub_entry(
            options={"conditions": [DARK_ENOUGH]},
            subentries_data=[
                room_subentry(
                    "Garage",
                    ["light.garage_main"],
                    presence_entity=DOOR,
                    presence_clear_delay=300,
                    presence_on_action="adaptive",
                    presence_off_action="turn_off",
                    **presence,
                )
            ],
        )
        await setup_hub(hass, entry)
        return entry

    def _lit(self, hass: HomeAssistant) -> bool:
        state = hass.states.get("light.garage_main")
        return state is not None and state.state == "on"

    async def test_the_door_opening_lights_the_room(self, hass: HomeAssistant) -> None:
        await self._build(hass)

        await _set(hass, DOOR, "on")

        assert self._lit(hass)

    async def test_it_goes_out_after_the_hold(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await self._build(hass)
        await _set(hass, DOOR, "on")

        await _set(hass, DOOR, "off")
        await _wait(hass, freezer, 301)

        assert not self._lit(hass)

    async def test_a_condition_gates_the_whole_room_too(
        self, hass: HomeAssistant
    ) -> None:
        await self._build(hass, presence_conditions=["dark"])
        await _set(hass, LUX, "400")

        await _set(hass, DOOR, "on")

        assert not self._lit(hass)

    async def test_and_lets_it_through_when_it_holds(self, hass: HomeAssistant) -> None:
        await self._build(hass, presence_conditions=["dark"])
        await _set(hass, LUX, "4")

        await _set(hass, DOOR, "on")

        assert self._lit(hass)

    async def test_a_room_lit_by_hand_is_not_switched_off(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The gap this closes: a press is our own path, so it never set
        `manual`, and `presence_respects_manual` did not see it."""
        await self._build(hass)
        await hass.services.async_call(
            "better_lighting", "press", {"room": "garage"}, blocking=True
        )
        await hass.async_block_till_done()
        assert self._lit(hass)

        await _set(hass, DOOR, "on")
        await _set(hass, DOOR, "off")
        await _wait(hass, freezer, 301)

        assert self._lit(hass)
