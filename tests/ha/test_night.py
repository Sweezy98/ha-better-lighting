"""The house going to bed: who goes dark, who is dimmed, and for how long."""

from __future__ import annotations

import datetime

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.better_lighting.const import DOMAIN
from custom_components.better_lighting.migrate import room_night_look
from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)

NIGHT = "input_boolean.asleep"
BEDROOM_SENSOR = "binary_sensor.bedroom_motion"
LOUNGE_SENSOR = "binary_sensor.lounge_motion"


async def build(hass: HomeAssistant, hub: dict | None = None, **bedroom) -> None:
    """A bedroom with a sensor, and a lounge with one, both lit."""
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"asleep": {}}}
    )
    hass.states.async_set(BEDROOM_SENSOR, "off")
    hass.states.async_set(LOUNGE_SENSOR, "off")
    await setup_members(
        hass,
        [
            MemberLight("One", is_on=True, brightness=200),
            MemberLight("Two", is_on=True, brightness=200),
        ],
    )
    entry = hub_entry(
        options={"night_source_entity": NIGHT, **(hub or {})},
        subentries_data=[
            room_subentry(
                "Bedroom",
                ["light.one"],
                **(
                    {
                        "night_behavior": "turn_off",
                        "triggers": [{"trigger_entity": BEDROOM_SENSOR}],
                        "presence_clear_delay": 120,
                    }
                    | bedroom
                ),
            ),
            room_subentry(
                "Lounge",
                ["light.two"],
                night_behavior="turn_off",
                triggers=[{"trigger_entity": LOUNGE_SENSOR}],
                presence_clear_delay=120,
            ),
        ],
    )
    await setup_hub(hass, entry)
    return entry


async def set_state(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


async def sleep(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": NIGHT}, blocking=True
    )
    await hass.async_block_till_done()


async def advance(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(datetime.timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def state(hass: HomeAssistant, entity_id: str):
    return hass.states.get(entity_id)


class TestGoingToBed:
    async def test_an_empty_room_goes_dark_and_an_occupied_one_is_dimmed(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await set_state(hass, BEDROOM_SENSOR, "on")

        await sleep(hass)

        assert state(hass, "light.two").state == "off"
        bedroom = state(hass, "light.one")
        assert bedroom.state == "on"
        # Dimmed now, not left at the evening's brightness until it empties.
        assert bedroom.attributes["brightness"] < 200

    async def test_the_occupied_room_goes_dark_once_it_empties(
        self, hass: HomeAssistant, freezer
    ) -> None:
        await build(hass)
        await set_state(hass, BEDROOM_SENSOR, "on")
        await sleep(hass)

        await set_state(hass, BEDROOM_SENSOR, "off")
        await advance(hass, freezer, 121)

        assert state(hass, "light.one").state == "off"

    async def test_a_room_whose_sensor_went_quiet_does_not_wait_out_its_delay(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await set_state(hass, BEDROOM_SENSOR, "on")
        await set_state(hass, BEDROOM_SENSOR, "off")

        await sleep(hass)

        assert state(hass, "light.one").state == "off"


class TestTheNightsOwnWait:
    async def test_night_can_wait_less_than_the_evening(
        self, hass: HomeAssistant, freezer
    ) -> None:
        await build(hass, night_own_clear_delay=True, night_presence_clear_delay=10)
        await sleep(hass)
        await set_state(hass, BEDROOM_SENSOR, "on")
        assert state(hass, "light.one").state == "on"

        await set_state(hass, BEDROOM_SENSOR, "off")
        await advance(hass, freezer, 11)

        assert state(hass, "light.one").state == "off"

    async def test_by_day_the_usual_wait_holds(
        self, hass: HomeAssistant, freezer
    ) -> None:
        await build(hass, night_own_clear_delay=True, night_presence_clear_delay=10)
        await set_state(hass, BEDROOM_SENSOR, "on")
        await set_state(hass, BEDROOM_SENSOR, "off")
        await advance(hass, freezer, 11)

        assert state(hass, "light.one").state == "on"


class TestTheBedsideSwitch:
    async def _double(self, hass: HomeAssistant) -> None:
        await hass.services.async_call(
            DOMAIN,
            "press",
            {"room": "bedroom", "controller": "Bedside", "kind": "double_press"},
            blocking=True,
        )
        await hass.async_block_till_done()

    async def _build(self, hass: HomeAssistant) -> None:
        from tests.conftest import add_room_switch, subentry_ids
        from tests.ha.test_controllers import controller_subentry

        entry = await build(hass)
        ids = subentry_ids(entry)
        add_room_switch(
            hass,
            entry,
            controller_subentry(
                "Bedside",
                room_id=ids["Bedroom"],
                scene_order=[],
                double_press_action="night_on",
            ),
            ids["Bedroom"],
        )
        await hass.async_block_till_done()

    async def test_it_sends_the_whole_house_to_bed(self, hass: HomeAssistant) -> None:
        await self._build(hass)
        await set_state(hass, BEDROOM_SENSOR, "on")

        await self._double(hass)

        # The helper itself, so every room follows -- not only this one.
        assert state(hass, NIGHT).state == "on"
        assert state(hass, "light.two").state == "off"
        assert state(hass, "light.one").state == "on"

    async def test_again_darkens_the_light_somebody_left_on(
        self, hass: HomeAssistant
    ) -> None:
        await self._build(hass)
        await set_state(hass, BEDROOM_SENSOR, "on")
        await self._double(hass)
        # A glass of water: the lounge lit by hand, and left.
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.two"}, blocking=True
        )
        await hass.async_block_till_done()
        assert state(hass, "light.two").state == "on"

        await self._double(hass)

        assert state(hass, "light.two").state == "off"
        # Somebody is still in the bedroom.
        assert state(hass, "light.one").state == "on"


class TestTheNightLook:
    async def test_a_room_follows_the_global_night_brightness(
        self, hass: HomeAssistant
    ) -> None:
        await build(
            hass,
            hub={"night_brightness_pct": 40},
            night_behavior="min_settings",
            night_look="global",
            night_brightness_pct=1,
        )
        await sleep(hass)

        # 40 % of 255, give or take the rounding.
        assert abs(state(hass, "light.one").attributes["brightness"] - 102) <= 3

    async def test_a_room_can_have_its_own(self, hass: HomeAssistant) -> None:
        await build(
            hass,
            hub={"night_brightness_pct": 40},
            night_behavior="min_settings",
            night_look="custom",
            night_brightness_pct=10,
        )
        await sleep(hass)

        assert abs(state(hass, "light.one").attributes["brightness"] - 25) <= 3


class TestStoredBeforeTheLook:
    def test_a_night_scene_becomes_dimming_with_that_scene(self) -> None:
        room = {"night_behavior": "scene", "night_scene_id": "s"}
        assert room_night_look(room, {}) == {
            "night_behavior": "min_settings",
            "night_look": "scene",
            "night_scene_id": "s",
        }

    def test_values_of_its_own_are_kept_as_its_own(self) -> None:
        room = {"night_behavior": "min_settings", "night_brightness_pct": 5}
        assert room_night_look(room, {})["night_look"] == "custom"

    def test_values_matching_the_house_follow_the_house(self) -> None:
        room = {"night_brightness_pct": 5, "night_color_temp_k": 2000}
        hub = {"night_brightness_pct": 5, "night_color_temp_k": 2000}
        migrated = room_night_look(room, hub)
        assert migrated["night_look"] == "global"
        # The default of the time, so an untouched room keeps on dimming.
        assert migrated["night_behavior"] == "min_settings"

    def test_a_room_already_migrated_is_left_alone(self) -> None:
        assert room_night_look({"night_look": "global"}, {}) is None

    async def test_the_stored_room_is_rewritten(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[room_subentry(night_behavior="scene")],
        )
        await setup_hub(hass, entry)

        room = next(iter(entry.subentries.values()))
        assert room.data["night_behavior"] == "min_settings"
        assert room.data["night_look"] == "scene"
        assert entry.minor_version == 2
