"""The adaptive engine as wired into Home Assistant."""

from __future__ import annotations

import datetime as dt

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.better_lighting.const import DOMAIN, SubentryType
from custom_components.better_lighting.render import Trigger
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    zone_subentry,
)
from tests.ha.test_scenes import scene_subentry

ZONE = "light.kitchen"
ADAPTIVE_BRIGHTNESS = "switch.kitchen_adaptive_brightness"
# British spelling, because the entity id follows the displayed name and
# the rest of the interface says "colour".
ADAPTIVE_COLOR = "switch.kitchen_adaptive_colour"
NIGHT = "switch.kitchen_night"


def light_profile(entity_id: str, **overrides) -> ConfigSubentryData:
    data = {
        "light_entity": entity_id,
        "enabled": True,
        "brightness_offset_pct": 0,
        "color_temp_offset_k": 0,
        "min_brightness_pct": 1,
        "max_brightness_pct": 100,
        "advanced": {},
        **overrides,
    }
    return ConfigSubentryData(
        data=data,
        subentry_type=SubentryType.LIGHT_PROFILE.value,
        title=entity_id,
        unique_id=f"profile:{entity_id}",
    )


async def _advance(hass: HomeAssistant, freezer, seconds: int = 200) -> None:
    """Move the clock and let any due timers run."""
    freezer.tick(dt.timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _turn_on_zone(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ZONE}, blocking=True
    )
    await hass.async_block_till_done()


class TestAdaptiveTurnOn:
    async def test_lights_come_on_already_adapted(self, hass: HomeAssistant) -> None:
        """The zero-flash path: one command carrying the curve's values."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(hass, hub_entry())

        await _turn_on_zone(hass)

        one = hass.states.get("light.one")
        assert one.state == "on"
        # Not a bare turn-on: the curve's brightness and colour arrive with it.
        assert one.attributes["brightness"] is not None
        assert one.attributes["color_temp_kelvin"] is not None

    async def test_adaptive_switch_off_falls_back_to_a_plain_turn_on(
        self, hass: HomeAssistant
    ) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(hass, hub_entry())

        for entity_id in (ADAPTIVE_BRIGHTNESS, ADAPTIVE_COLOR):
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": entity_id}, blocking=True
            )
        await hass.async_block_till_done()
        await _turn_on_zone(hass)

        assert hass.states.get("light.one").state == "on"
        assert hass.states.get(ADAPTIVE_BRIGHTNESS).state == "off"


class TestLightProfiles:
    async def test_offset_separates_two_otherwise_identical_lights(
        self, hass: HomeAssistant
    ) -> None:
        """Requirement 9: match a mismatched bulb to its neighbour."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[
                zone_subentry(),
                light_profile("light.two", brightness_offset_pct=-25),
            ]
        )
        await setup_hub(hass, entry)

        await _turn_on_zone(hass)

        one = hass.states.get("light.one").attributes["brightness"]
        two = hass.states.get("light.two").attributes["brightness"]
        assert two < one, "the calibrated light should sit below its neighbour"

    async def test_colour_temp_offset_applies(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[
                zone_subentry(),
                light_profile("light.two", color_temp_offset_k=-500),
            ]
        )
        await setup_hub(hass, entry)

        await _turn_on_zone(hass)

        one = hass.states.get("light.one").attributes["color_temp_kelvin"]
        two = hass.states.get("light.two").attributes["color_temp_kelvin"]
        assert two < one

    async def test_max_brightness_caps_a_light(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[
                zone_subentry(),
                light_profile("light.two", max_brightness_pct=10),
            ]
        )
        await setup_hub(hass, entry)

        await _turn_on_zone(hass)

        # 10% of 255 is ~26; allow for rounding.
        assert hass.states.get("light.two").attributes["brightness"] <= 27


class TestNightMode:
    async def test_night_switch_follows_its_source_entity(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set("input_boolean.asleep", "off")
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[zone_subentry(night_source_entity="input_boolean.asleep")]
        )
        await setup_hub(hass, entry)
        assert hass.states.get(NIGHT).state == "off"

        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        assert hass.states.get(NIGHT).state == "on"

    async def test_night_mode_dims_and_warms(self, hass: HomeAssistant) -> None:
        hass.states.async_set("input_boolean.asleep", "off")
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[
                zone_subentry(
                    night_source_entity="input_boolean.asleep",
                    night_brightness_pct=2,
                    night_color_temp_k=1800,
                )
            ]
        )
        await setup_hub(hass, entry)
        await _turn_on_zone(hass)
        day_brightness = hass.states.get("light.one").attributes["brightness"]

        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        night = hass.states.get("light.one").attributes
        assert night["brightness"] < day_brightness
        # The zone asks for 1800 K but this fixture bottoms out at 2000 K, so
        # the request is clamped to what the hardware can actually produce --
        # step 5 of the resolution order.
        assert night["color_temp_kelvin"] == 2000

    async def test_the_helper_can_live_on_the_hub(self, hass: HomeAssistant) -> None:
        """One helper for the house; each room decides what to do about it."""
        hass.states.async_set("input_boolean.asleep", "off")
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            options={"night_source_entity": "input_boolean.asleep"},
            subentries_data=[zone_subentry()],
        )
        await setup_hub(hass, entry)
        assert hass.states.get(NIGHT).state == "off"

        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        assert hass.states.get(NIGHT).state == "on"

    async def test_night_mode_does_not_light_a_dark_room(
        self, hass: HomeAssistant
    ) -> None:
        """Adaptive Lighting's oldest complaint: the house lights up at bedtime."""
        hass.states.async_set("input_boolean.asleep", "off")
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass,
            hub_entry(
                options={"night_source_entity": "input_boolean.asleep"},
                subentries_data=[zone_subentry()],
            ),
        )
        assert hass.states.get("light.one").state == "off"

        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        assert hass.states.get("light.one").state == "off"
        assert hass.states.get("light.two").state == "off"


class TestTick:
    async def test_tick_corrects_a_light_that_has_drifted(
        self, hass: HomeAssistant, freezer
    ) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(hass, hub_entry())
        await _turn_on_zone(hass)

        # A small drift, of the kind a device's own rounding produces. Small
        # enough not to read as a person at the dimmer, large enough that the
        # engine will not dismiss it as already-correct.
        drifted = hass.states.get("light.one").attributes["brightness"] - 10
        hass.states.async_set(
            "light.one",
            "on",
            {**hass.states.get("light.one").attributes, "brightness": drifted},
        )
        await hass.async_block_till_done()

        # Two advances: the first releases the deterministic start-up stagger
        # that keeps zones from all rendering in the same event-loop slot, the
        # second lands on the interval itself.
        await _advance(hass, freezer)
        await _advance(hass, freezer)

        assert hass.states.get("light.one").attributes["brightness"] != drifted

    async def test_tick_leaves_a_manually_changed_light_alone(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """Take-over control: a person at the dimmer outranks the interval."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(hass, hub_entry())
        await _turn_on_zone(hass)

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 3},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        await _advance(hass, freezer)
        await _advance(hass, freezer)

        assert hass.states.get("light.one").attributes["brightness"] == 3
        # The rest of the room carries on adapting as usual.
        assert hass.states.get("light.two").attributes["brightness"] != 3

    async def test_tick_never_switches_a_dark_light_on(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """A tick adjusts what is lit; it is not allowed to change on/off state."""
        await setup_members(
            hass,
            [MemberLight("One", is_on=True, brightness=120), MemberLight("Two")],
        )
        await setup_hub(hass, hub_entry())

        await _advance(hass, freezer)
        await _advance(hass, freezer)

        assert hass.states.get("light.two").state == "off"


class TestAdaptiveAxes:
    """Brightness and colour follow the sun independently."""

    async def _setup(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(hass, hub_entry())
        await _turn_on_zone(hass)

    async def _set(self, hass: HomeAssistant, entity_id: str, on: bool) -> None:
        await hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()

    async def test_both_switches_exist(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        assert hass.states.get(ADAPTIVE_BRIGHTNESS).state == "on"
        assert hass.states.get(ADAPTIVE_COLOR).state == "on"

    async def test_switching_brightness_off_leaves_colour_adapting(
        self, hass: HomeAssistant
    ) -> None:
        """Asserted on what the engine decides, not on faked entity state.

        Writing a light's state directly is a lie the entity overwrites the
        moment it next publishes, which makes a state-based assertion here
        test the mock rather than the code.
        """
        await self._setup(hass)
        controller = next(
            iter(
                hass.config_entries.async_entries(DOMAIN)[
                    0
                ].runtime_data.controllers.values()
            )
        )
        await self._set(hass, ADAPTIVE_BRIGHTNESS, False)

        payloads = [c.data for c in controller.commands_for(Trigger.TICK)]
        assert payloads, "the tick should still have something to say"
        assert all("brightness" not in p for p in payloads)
        assert any("color_temp_kelvin" in p for p in payloads)

    async def test_switching_colour_off_leaves_brightness_adapting(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass)
        controller = next(
            iter(
                hass.config_entries.async_entries(DOMAIN)[
                    0
                ].runtime_data.controllers.values()
            )
        )
        await self._set(hass, ADAPTIVE_COLOR, False)

        payloads = [c.data for c in controller.commands_for(Trigger.TICK)]
        assert payloads
        assert all("color_temp_kelvin" not in p for p in payloads)
        assert any("brightness" in p for p in payloads)

    async def test_switching_both_off_stops_the_engine(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass)
        controller = next(
            iter(
                hass.config_entries.async_entries(DOMAIN)[
                    0
                ].runtime_data.controllers.values()
            )
        )
        await self._set(hass, ADAPTIVE_BRIGHTNESS, False)
        await self._set(hass, ADAPTIVE_COLOR, False)

        assert controller.commands_for(Trigger.TICK) == []

    async def test_a_scene_can_still_set_a_switched_off_axis(
        self, hass: HomeAssistant
    ) -> None:
        """Off means "stop following the sun", not "never set a colour"."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass,
            hub_entry(
                subentries_data=[zone_subentry(), scene_subentry("Cosy", brightness=20)]
            ),
        )
        await _turn_on_zone(hass)
        await self._set(hass, ADAPTIVE_COLOR, False)

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.kitchen_scenes", "option": "Cosy"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.one").attributes["color_temp_kelvin"] == 2200


class TestNightTurnOff:
    """Night mode can darken a room, but never while somebody is in it."""

    async def _setup(self, hass: HomeAssistant, *, presence: bool = True):
        hass.states.async_set("input_boolean.asleep", "off")
        if presence:
            hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await setup_members(
            hass,
            [
                MemberLight("One", is_on=True, brightness=150),
                MemberLight("Two", is_on=True, brightness=150),
            ],
        )
        extra = (
            {
                "presence_entity": "binary_sensor.kitchen_presence",
                "presence_clear_delay": 0,
                "presence_off_action": "none",
            }
            if presence
            else {}
        )
        await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry(
                        night_source_entity="input_boolean.asleep",
                        night_behavior="turn_off",
                        **extra,
                    )
                ]
            ),
        )

    async def _sleep(self, hass: HomeAssistant, on: bool = True) -> None:
        hass.states.async_set("input_boolean.asleep", "on" if on else "off")
        await hass.async_block_till_done()

    async def test_an_empty_room_goes_dark(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await self._sleep(hass)
        assert hass.states.get("light.one").state == "off"

    async def test_an_occupied_room_is_left_alone(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()

        await self._sleep(hass)

        assert hass.states.get("light.one").state == "on"

    async def test_it_goes_dark_once_they_leave(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        await self._sleep(hass)
        assert hass.states.get("light.one").state == "on"

        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()

        assert hass.states.get("light.one").state == "off"

    async def test_asking_again_darkens_a_light_put_on_in_the_night(
        self, hass: HomeAssistant
    ) -> None:
        """The scenario this exists for.

        The house goes to bed and the room goes dark. Somebody gets up for a
        glass of water, puts the light on, and goes back to bed. The night
        switch is no help -- night mode never stopped being on, so nothing
        changed and nothing happened.
        """
        await self._setup(hass)
        await self._sleep(hass)
        assert hass.states.get("light.one").state == "off"

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.one"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get("light.one").state == "on"

        await hass.services.async_call(DOMAIN, "night_lights_off", {}, blocking=True)
        await hass.async_block_till_done()
        assert hass.states.get("light.one").state == "off"

    async def test_asking_again_does_nothing_outside_the_night(
        self, hass: HomeAssistant
    ) -> None:
        """It repeats something that already happened. Outside the night
        nothing has, and a house-wide lights-out is not what it is for."""
        await self._setup(hass)
        assert hass.states.get("light.one").state == "on"

        await hass.services.async_call(DOMAIN, "night_lights_off", {}, blocking=True)
        await hass.async_block_till_done()
        assert hass.states.get("light.one").state == "on"

    async def test_asking_again_leaves_an_occupied_room_alone(
        self, hass: HomeAssistant
    ) -> None:
        """Down the same path as the original, so the same room is spared."""
        await self._setup(hass)
        await self._sleep(hass)
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.one"}, blocking=True
        )
        await hass.async_block_till_done()

        await hass.services.async_call(DOMAIN, "night_lights_off", {}, blocking=True)
        await hass.async_block_till_done()
        assert hass.states.get("light.one").state == "on"

        # And it goes off when they leave, like any other waiting turn-off.
        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()
        assert hass.states.get("light.one").state == "off"

    async def test_the_button_does_the_same(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await self._sleep(hass)
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.one"}, blocking=True
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.better_lighting_night_lights_off"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get("light.one").state == "off"

    async def test_the_button_survives_a_reload(self, hass: HomeAssistant) -> None:
        """It is named after the entry rather than after a room, which is
        exactly the shape the tidy-up sweep removes."""
        await self._setup(hass)
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get("button.better_lighting_night_lights_off") is not None

    async def test_a_press_cancels_the_waiting_turn_off(
        self, hass: HomeAssistant
    ) -> None:
        """Reaching for the switch says you want the light."""
        await self._setup(hass)
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        await self._sleep(hass)

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()

        assert hass.states.get("light.one").state == "on"

    async def test_the_zones_own_switch_dims_rather_than_darkens(
        self, hass: HomeAssistant
    ) -> None:
        """The helper is the house going to bed; the switch is a person."""
        await self._setup(hass)

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": NIGHT}, blocking=True
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.one").state == "on"
        assert hass.states.get("light.one").attributes["brightness"] < 150

    async def test_night_ending_leaves_the_room_off(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await self._sleep(hass)
        assert hass.states.get("light.one").state == "off"

        await self._sleep(hass, False)
        # Morning does not switch the house on.
        assert hass.states.get("light.one").state == "off"
