"""The adaptive engine as wired into Home Assistant."""

from __future__ import annotations

import datetime as dt

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.better_lighting.const import SubentryType
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    zone_subentry,
)

ZONE = "light.kitchen"
ADAPTIVE = "switch.kitchen_adaptive"
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

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": ADAPTIVE}, blocking=True
        )
        await hass.async_block_till_done()
        await _turn_on_zone(hass)

        assert hass.states.get("light.one").state == "on"
        assert hass.states.get(ADAPTIVE).state == "off"


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
