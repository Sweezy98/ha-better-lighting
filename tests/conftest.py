"""Shared fixtures. Only the tests under tests/ha/ need these."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    setup_test_component_platform,
)

from custom_components.better_lighting.const import DOMAIN, SubentryType


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/ during tests."""
    return


class MemberLight(LightEntity):
    """A real light entity, so tests exercise the actual service path.

    Mocking ``light.turn_on`` wholesale would replace the service our own zone
    entity is registered against, so the zone would never run at all. Using real
    member entities instead means a test asserts on what the bulbs actually did.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_supported_color_modes: ClassVar = {ColorMode.COLOR_TEMP, ColorMode.HS}
    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 6500

    def __init__(
        self, name: str, *, is_on: bool = False, brightness: int | None = None
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = f"member_{name.lower()}"
        self._attr_is_on = is_on
        self._attr_brightness = brightness
        self._attr_color_mode = ColorMode.COLOR_TEMP
        self._attr_color_temp_kelvin = 3000
        self.turn_on_calls: list[dict[str, Any]] = []

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.turn_on_calls.append(dict(kwargs))
        self._attr_is_on = True
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._attr_color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            self._attr_color_mode = ColorMode.COLOR_TEMP
        if ATTR_HS_COLOR in kwargs:
            self._attr_hs_color = kwargs[ATTR_HS_COLOR]
            self._attr_color_mode = ColorMode.HS
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


def zone_subentry(
    name: str = "Kitchen", lights: list[str] | None = None, **overrides
) -> ConfigSubentryData:
    """A zone subentry payload, with sensible defaults for tests."""
    data = {
        "name": name,
        "lights": lights if lights is not None else ["light.one", "light.two"],
        "icon": "mdi:lightbulb-group",
        "all": False,
        "hide_members": False,
        "remember_on_state": True,
        "brightness_strategy": "average",
        "expand_light_groups": True,
        **overrides,
    }
    return ConfigSubentryData(
        data=data,
        subentry_type=SubentryType.ZONE.value,
        title=name,
        unique_id=f"zone:{name.lower()}",
    )


def hub_entry(**kwargs) -> MockConfigEntry:
    """A hub entry with one two-light zone unless told otherwise."""
    kwargs.setdefault("subentries_data", [zone_subentry()])
    return MockConfigEntry(
        domain=DOMAIN,
        title="Better Lighting",
        data={},
        options={},
        unique_id=DOMAIN,
        **kwargs,
    )


async def setup_members(
    hass: HomeAssistant, members: list[MemberLight]
) -> list[MemberLight]:
    """Register real member light entities."""
    setup_test_component_platform(hass, LIGHT_DOMAIN, members)
    assert await async_setup_component(
        hass, LIGHT_DOMAIN, {LIGHT_DOMAIN: {"platform": "test"}}
    )
    await hass.async_block_till_done()
    return members


async def setup_hub(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    """Add the entry to hass and set it up."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
