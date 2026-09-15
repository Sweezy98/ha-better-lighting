"""Per-zone buttons: cycle, reset to adaptive, and hand control back."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterLightingConfigEntry
from .const import DOMAIN
from .models import ControllerConfig, ZoneConfig
from .modes import ModeGroupRuntime
from .zone import ZoneController

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the per-zone buttons."""
    runtime = entry.runtime_data
    for subentry_id, zone in runtime.zones.items():
        controller = runtime.controllers[subentry_id]
        switch = runtime.default_switch.get(subentry_id)
        async_add_entities(
            [
                ZoneButton(
                    zone,
                    controller,
                    "cycle_next",
                    "mdi:skip-next",
                    lambda c=controller, s=switch: c.async_cycle(s, direction=1),
                ),
                ZoneButton(
                    zone,
                    controller,
                    "cycle_previous",
                    "mdi:skip-previous",
                    lambda c=controller, s=switch: c.async_cycle(s, direction=-1),
                    enabled=False,
                ),
                ZoneButton(
                    zone,
                    controller,
                    "reset_adaptive",
                    "mdi:theme-light-dark",
                    _reset_adaptive(controller),
                ),
                ZoneButton(
                    zone,
                    controller,
                    "clear_manual",
                    "mdi:hand-back-left-off",
                    _clear_manual(controller),
                    category=EntityCategory.DIAGNOSTIC,
                    enabled=False,
                ),
            ],
            config_subentry_id=subentry_id,
        )

    for subentry_id, mode_runtime in runtime.mode_runtimes.items():
        async_add_entities(
            [ModeClearButton(mode_runtime)], config_subentry_id=subentry_id
        )


def _reset_adaptive(controller: ZoneController) -> Callable[[], Awaitable[None]]:
    async def _run() -> None:
        # Requirement 1: a service or button that forces a zone back to
        # adaptive, whatever it was doing.
        controller.clear_manual()
        await controller.async_set_adaptive()

    return _run


def _clear_manual(controller: ZoneController) -> Callable[[], Awaitable[None]]:
    async def _run() -> None:
        controller.clear_manual()
        await controller.async_render()

    return _run


class ZoneButton(ButtonEntity):
    """A one-shot action on a zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        zone: ZoneConfig,
        controller: ZoneController,
        key: str,
        icon: str,
        action: Callable[[], Awaitable[None]],
        *,
        category: EntityCategory | None = None,
        enabled: bool = True,
    ) -> None:
        self.zone = zone
        self.controller = controller
        self._action = action
        self._attr_unique_id = f"{zone.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_entity_category = category
        self._attr_entity_registry_enabled_default = enabled
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zone.subentry_id)},
            name=zone.name,
            manufacturer="Better Lighting",
            model="Zone",
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


__all__ = ["ControllerConfig", "ModeClearButton", "ZoneButton", "async_setup_entry"]
