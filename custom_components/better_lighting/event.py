"""One event entity per configured switch.

Gives every press a first-class automation hook, so a controller can drive
things beyond lighting without anyone having to listen on the bus.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterLightingConfigEntry
from .const import DOMAIN
from .controllers import EVENT_PRESS
from .models import ControllerConfig

PARALLEL_UPDATES = 0

PRESS_KINDS = ["press", "double_press", "long_press"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one event entity per configured controller."""
    runtime = entry.runtime_data
    for controller in runtime.switches.values():
        if controller.zone_id not in runtime.zones:
            continue
        # Registered against the *room's* subentry, so the switch appears
        # inside that room's group rather than as a card of its own.
        async_add_entities(
            [ControllerPressEvent(controller, runtime.zones[controller.zone_id].name)],
            config_subentry_id=controller.zone_id,
        )


class ControllerPressEvent(EventEntity):
    """Fires whenever this switch is pressed."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "controller_press"
    _attr_icon = "mdi:gesture-tap-button"
    _attr_event_types = PRESS_KINDS

    def __init__(self, controller: ControllerConfig, zone_name: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.subentry_id}_press"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller.subentry_id)},
            name=f"{zone_name} - {controller.name}",
            manufacturer="Better Lighting",
            model="Controller",
            entry_type=DeviceEntryType.SERVICE,
            # Nested under the room's own device, so the switch reads as part
            # of the room rather than as something standing beside it.
            via_device=(DOMAIN, controller.zone_id),
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_PRESS, self._handle_press)
        )

    @callback
    def _handle_press(self, event: Event) -> None:
        if event.data.get("controller_id") != self.controller.subentry_id:
            return
        self._trigger_event(event.data.get("kind", "press"))
        self.async_write_ha_state()
