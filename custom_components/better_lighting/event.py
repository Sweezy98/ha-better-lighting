"""One event entity per configured switch.

Gives every press a first-class automation hook, so a controller can drive
things beyond lighting without anyone having to listen on the bus.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
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
        if controller.room_id not in runtime.rooms:
            continue
        # Registered against the *room's* subentry, so the switch appears
        # inside that room's group rather than as a card of its own.
        async_add_entities(
            [ControllerPressEvent(controller, runtime.rooms[controller.room_id].name)],
            config_subentry_id=controller.room_id,
        )


class ControllerPressEvent(EventEntity):
    """Fires whenever this switch is pressed."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:gesture-tap-button"
    _attr_event_types = PRESS_KINDS

    def __init__(self, controller: ControllerConfig, room_name: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.subentry_id}_press"
        # The room's own device, not one of the switch's own. A switch has no
        # hardware of ours behind it, and giving it a device of its own meant
        # deleting the switch left an empty device and a dead entity behind.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, controller.room_id)})
        self._attr_name = controller.name

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
