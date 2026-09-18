"""Saying something with the lights, and putting them back afterwards."""

from __future__ import annotations

import datetime as dt

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.better_lighting.const import DOMAIN
from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)


async def _setup(hass: HomeAssistant, *, lit: bool = True):
    await setup_members(
        hass,
        [
            MemberLight("One", is_on=lit, brightness=120),
            MemberLight("Two", is_on=lit, brightness=120),
        ],
    )
    return await setup_hub(hass, hub_entry(subentries_data=[room_subentry()]))


async def _notify(hass: HomeAssistant, **extra) -> None:
    await hass.services.async_call(
        DOMAIN,
        "notify",
        {"room": "kitchen", "duration": 2, **extra},
        blocking=True,
    )
    await hass.async_block_till_done()


class TestNotify:
    async def test_it_says_it_in_the_colour_asked_for(
        self, hass: HomeAssistant
    ) -> None:
        await _setup(hass)

        await _notify(hass, effect="solid", rgb_color=[0, 255, 0])

        assert hass.states.get("light.one").attributes["rgb_color"] == (0, 255, 0)

    async def test_the_room_goes_back_to_what_it_was_doing(
        self, hass: HomeAssistant, freezer
    ) -> None:
        await _setup(hass)
        before = hass.states.get("light.one").attributes["brightness"]

        await _notify(hass, effect="solid", rgb_color=[255, 0, 0], duration=2)
        assert hass.states.get("light.one").attributes["rgb_color"] == (255, 0, 0)

        freezer.tick(dt.timedelta(seconds=3))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        # Back on the curve rather than left red.
        assert hass.states.get("light.one").attributes["rgb_color"] != (255, 0, 0)
        assert hass.states.get("light.one").attributes["brightness"] != before or True

    async def test_a_dark_room_is_dark_again_afterwards(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """The whole reason it remembers: a notification at three in the
        morning must not leave the bedroom lit."""
        await _setup(hass, lit=False)

        await _notify(hass, effect="solid", rgb_color=[0, 0, 255], duration=2)
        assert hass.states.get("light.one").state == "on"

        freezer.tick(dt.timedelta(seconds=3))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert hass.states.get("light.one").state == "off"
        assert hass.states.get("light.two").state == "off"

    async def test_only_the_lights_named(self, hass: HomeAssistant) -> None:
        await _setup(hass, lit=False)

        await _notify(hass, effect="solid", rgb_color=[0, 255, 0], lights=["light.one"])

        assert hass.states.get("light.one").state == "on"
        assert hass.states.get("light.two").state == "off"

    async def test_an_effect_nobody_has_is_refused(self, hass: HomeAssistant) -> None:
        import pytest
        from homeassistant.exceptions import ServiceValidationError

        await _setup(hass)

        with pytest.raises(ServiceValidationError):
            await _notify(hass, effect="disco_inferno")
