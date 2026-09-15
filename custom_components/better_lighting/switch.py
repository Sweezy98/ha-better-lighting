"""The per-zone adaptive and night-mode switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import BetterLightingConfigEntry
from .const import DOMAIN, NightBehavior
from .models import ZoneConfig
from .profiles import Axis
from .zone import ZoneController

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the adaptive and night switches for every zone."""
    runtime = entry.runtime_data
    for subentry_id, zone in runtime.zones.items():
        controller = runtime.controllers[subentry_id]
        async_add_entities(
            [
                AdaptiveAxisSwitch(zone, controller, Axis.BRIGHTNESS),
                AdaptiveAxisSwitch(zone, controller, Axis.COLOR),
                NightSwitch(zone, controller),
            ],
            config_subentry_id=subentry_id,
        )


class _ZoneSwitch(SwitchEntity, RestoreEntity):
    """Shared plumbing: device binding and following the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, zone: ZoneConfig, controller: ZoneController, key: str) -> None:
        self.zone = zone
        self.controller = controller
        self._attr_unique_id = f"{zone.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zone.subentry_id)},
            name=zone.name,
            manufacturer="Better Lighting",
            model="Zone",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class AdaptiveAxisSwitch(_ZoneSwitch):
    """Whether this room tracks the sun on one axis.

    Two switches rather than one, because the axes are genuinely independent:
    a room can keep warming through the evening while its brightness stays
    where somebody put it, or hold a colour while still dimming with the day.
    Switching an axis off means "stop following the sun" -- a scene can still
    set that axis.
    """

    def __init__(
        self, zone: ZoneConfig, controller: ZoneController, axis: Axis
    ) -> None:
        self.axis = axis
        key = "adaptive_brightness" if axis is Axis.BRIGHTNESS else "adaptive_color"
        super().__init__(zone, controller, key)
        self._attr_icon = (
            "mdi:brightness-auto" if axis is Axis.BRIGHTNESS else "mdi:palette-outline"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Survive a restart: a room the user took off the curve should not
        # quietly go back on it when Home Assistant restarts.
        if (last := await self.async_get_last_state()) is not None:
            await self.controller.async_set_adaptive_axis(self.axis, last.state == "on")

    @property
    def is_on(self) -> bool:
        if self.axis is Axis.BRIGHTNESS:
            return self.controller.adapt_brightness
        return self.controller.adapt_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_adaptive_axis(self.axis, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_adaptive_axis(self.axis, False)


class NightSwitch(_ZoneSwitch):
    """Whether this zone is in night mode.

    When the zone names a source entity this mirrors it, and the source wins on
    every change -- so the user keeps one source of truth for "the house is
    asleep" and this switch is a readout that can still be nudged by hand until
    the source next changes.
    """

    _attr_icon = "mdi:sleep"

    def __init__(self, zone: ZoneConfig, controller: ZoneController) -> None:
        super().__init__(zone, controller, "night")

    @property
    def is_on(self) -> bool:
        return self.controller.night_active

    @property
    def available(self) -> bool:
        return self.zone.night_behavior is not NightBehavior.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "source_entity": self.zone.night_source_entity,
            "behavior": self.zone.night_behavior.value,
            "ignores_presence": self.zone.night_ignore_presence,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_night(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_night(False)
