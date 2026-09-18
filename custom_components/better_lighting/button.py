"""Per-room buttons: cycle, reset to adaptive, and hand control back."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterLightingConfigEntry
from .const import DOMAIN
from .models import ControllerConfig, RoomConfig
from .modes import ModeGroupRuntime
from .room import RoomController

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the per-room buttons."""
    runtime = entry.runtime_data
    for subentry_id, room in runtime.rooms.items():
        controller = runtime.controllers[subentry_id]
        switch = runtime.default_switch.get(subentry_id)
        async_add_entities(
            [
                RoomButton(
                    room,
                    controller,
                    "cycle_next",
                    "mdi:skip-next",
                    lambda c=controller, s=switch: c.async_cycle(s, direction=1),
                ),
                RoomButton(
                    room,
                    controller,
                    "cycle_previous",
                    "mdi:skip-previous",
                    lambda c=controller, s=switch: c.async_cycle(s, direction=-1),
                ),
                RoomButton(
                    room,
                    controller,
                    "reset_adaptive",
                    "mdi:theme-light-dark",
                    _reset_adaptive(controller),
                ),
                RoomButton(
                    room,
                    controller,
                    "clear_manual",
                    "mdi:hand-back-left-off",
                    _clear_manual(controller),
                    category=EntityCategory.DIAGNOSTIC,
                ),
            ],
            config_subentry_id=subentry_id,
        )

    for subentry_id, mode_runtime in runtime.mode_runtimes.items():
        async_add_entities(
            [ModeClearButton(mode_runtime)], config_subentry_id=subentry_id
        )

    # House-wide, and so on the hub rather than in any one room: the room that
    # needs switching off is by definition not the one being stood in.
    async_add_entities([NightLightsOffButton(entry)])


def _reset_adaptive(controller: RoomController) -> Callable[[], Awaitable[None]]:
    async def _run() -> None:
        # Requirement 1: a service or button that forces a room back to
        # adaptive, whatever it was doing.
        controller.clear_manual()
        await controller.async_set_adaptive()

    return _run


def _clear_manual(controller: RoomController) -> Callable[[], Awaitable[None]]:
    async def _run() -> None:
        controller.clear_manual()
        await controller.async_render(only_lit=True)

    return _run


class NightLightsOffButton(ButtonEntity):
    """Ask again for the lights night mode would have switched off.

    Does nothing at all outside night mode, deliberately. It is not a way to
    switch the house off; it is a way to repeat what happened when the house
    went to bed, for the light somebody put on in between.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "night_lights_off"
    _attr_icon = "mdi:weather-night"

    def __init__(self, entry: BetterLightingConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_night_lights_off"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Better Lighting",
            manufacturer="Better Lighting",
            model="Hub",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        for controller in self._entry.runtime_data.controllers.values():
            await controller.async_request_night_off()


class RoomButton(ButtonEntity):
    """A one-shot action on a room."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        room: RoomConfig,
        controller: RoomController,
        key: str,
        icon: str,
        action: Callable[[], Awaitable[None]],
        *,
        category: EntityCategory | None = None,
        enabled: bool = True,
    ) -> None:
        self.room = room
        self.controller = controller
        self._action = action
        self._attr_unique_id = f"{room.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_entity_category = category
        self._attr_entity_registry_enabled_default = enabled
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.subentry_id)},
            name=room.name,
            manufacturer="Better Lighting",
            model="Room",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        await self._action()


class ModeClearButton(ButtonEntity):
    """End a mode's session and put the rooms back."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "mode_clear"
    _attr_icon = "mdi:stop"

    def __init__(self, mode_runtime: ModeGroupRuntime) -> None:
        self.runtime = mode_runtime
        config = mode_runtime.config
        self._attr_unique_id = f"{config.subentry_id}_clear"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config.subentry_id)},
            name=config.name,
            manufacturer="Better Lighting",
            model="Mode",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        await self.runtime.async_end()


__all__ = ["ControllerConfig", "ModeClearButton", "RoomButton", "async_setup_entry"]
